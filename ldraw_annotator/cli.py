"""Command line: ldraw-annotator <command> ... (JSON to stdout).

Exit codes: 0 ok, 1 a check failed / some model did not finish, 2 bad input or error.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from . import check, db, digest, mpd, parts, plan, run

LDRAW_SUFFIXES = {".ldr", ".mpd"}


def inputs(paths):
    """Expand files and directories (recursively) into LDraw model files, sorted."""
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in LDRAW_SUFFIXES)
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(p)
    return files


def latest_ir(work, model):
    """IR of a model in the work directory (latest round), or a fresh prep of a model file."""
    p = Path(model)
    if p.is_file():
        return mpd.load(p)
    wd = Path(work) / model
    if (wd / "ir.final.json").exists():
        return json.loads((wd / "ir.final.json").read_text())
    rounds = sorted(int(f.stem[4:]) for f in wd.glob("ir.r*.json"))
    if not rounds:
        raise FileNotFoundError(f"no IR for {model!r} in {work}; run prep or run first")
    return json.loads((wd / f"ir.r{rounds[-1]}.json").read_text())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ldraw-annotator", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    # Every command but status reads the LDraw info DB (part and colour descriptions).
    db_arg = argparse.ArgumentParser(add_help=False)
    db_arg.add_argument("--db", help=f"LDraw info DB, ldraw-info.db (default: ${parts.DB_ENV})")

    p = sub.add_parser("run", parents=[db_arg], help="annotate files/directories (resumable batch)")
    p.add_argument("inputs", nargs="+")
    p.add_argument("--out", required=True, help="directory for annotated .mpd files")
    p.add_argument("--work", default="work", help="work directory (resume state)")
    p.add_argument("-j", "--jobs", type=int, default=4, help="parallel LLM calls (and models)")
    p.add_argument("--split-model", default=os.environ.get("LDRAW_ANNOTATOR_SPLIT_MODEL", run.SPLIT_MODEL),
                   help="provider:model that decides the hierarchy (default: %(default)s, "
                        "or $LDRAW_ANNOTATOR_SPLIT_MODEL)")
    p.add_argument("--refine-model", default=os.environ.get("LDRAW_ANNOTATOR_REFINE_MODEL", run.REFINE_MODEL),
                   help="provider:model that rewrites parent descriptions (default: %(default)s, "
                        "or $LDRAW_ANNOTATOR_REFINE_MODEL)")
    p.add_argument("--thinking", default=run.THINKING, choices=["none", "low", "medium", "high", "xhigh"],
                   help="reasoning effort (default: %(default)s, the fastest that answers well)")
    p.add_argument("--force", action="store_true", help="redo models that already have an output")
    p.add_argument("--no-pyldraw", action="store_true", help="skip the pyldraw3 reparse in check")

    p = sub.add_parser("status", help="progress of a batch")
    p.add_argument("--work", default="work")

    p = sub.add_parser("prep", parents=[db_arg], help="prepare one model and save its IR (ir.r0.json)")
    p.add_argument("input")
    p.add_argument("--work", default="work")

    p = sub.add_parser("digest", parents=[db_arg], help="print what an agent sees")
    p.add_argument("model", help="model file, or model name in the work directory")
    p.add_argument("--work", default="work")
    p.add_argument("--tree", action="store_true", help="tree view (refine) instead of units")

    p = sub.add_parser("apply", parents=[db_arg], help="validate and apply one split plan to a model's latest IR")
    p.add_argument("model")
    p.add_argument("plan", help="plan JSON file")
    p.add_argument("--work", default="work")

    p = sub.add_parser("write", parents=[db_arg], help="write a model's latest IR as .mpd")
    p.add_argument("model")
    p.add_argument("output")
    p.add_argument("--work", default="work")

    p = sub.add_parser("check", parents=[db_arg], help="verify an annotated output against its input")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--no-pyldraw", action="store_true")

    p = sub.add_parser("db-sync", parents=[db_arg], help="(user) store annotated models' descriptions/hierarchy in the DB")
    p.add_argument("inputs", nargs="+", help="annotated .mpd files or directories")

    args = ap.parse_args(argv)
    parts.db_file = getattr(args, "db", None)  # else parts falls back to $LDRAW_ANNOTATOR_DB
    try:
        code, result = dispatch(args)
    except (FileNotFoundError, ValueError) as e:
        code, result = 2, {"error": str(e)}
    json.dump(result, sys.stdout, indent=1, ensure_ascii=False)
    print()
    return code


def dispatch(args):
    if args.cmd == "run":
        runner = run.Runner(args.out, args.work, args.jobs, args.split_model, args.refine_model,
                            args.force, not args.no_pyldraw, args.thinking)
        results = runner.run(inputs(args.inputs))
        return (0 if all(r["status"] == "done" for r in results) else 1), results
    if args.cmd == "status":
        return 0, run.status(args.work)
    parts.db_path()  # fail early with a clear message when the DB is missing
    if args.cmd == "prep":
        ir = mpd.load(args.input)
        out = Path(args.work) / run.model_key(args.input) / "ir.r0.json"
        run.save_json(out, ir)
        kinds = {}
        for s in ir["sections"].values():
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        return 0, {"ir": str(out), "sections": kinds,
                   "items": sum(len(mpd.items(s)) for s in ir["sections"].values() if s["kind"] == "model")}
    if args.cmd == "digest":
        ir = latest_ir(args.work, args.model)
        if args.tree:
            print(digest.tree_digest(ir, digest.refine_ids(ir)))
        else:
            todo = [sid for sid in mpd.output_order(ir) if ir["sections"][sid].get("todo")]
            for n, ids in enumerate(digest.pack_units(ir, todo)):
                print(f"===== unit {n}: {', '.join(ids)}\n{digest.unit_digest(ir, ids)}")
        return 0, {"todo": sum(1 for s in ir["sections"].values() if s.get("todo"))}
    if args.cmd == "apply":
        ir = latest_ir(args.work, args.model)
        answer = json.loads(Path(args.plan).read_text())
        ids = [sp.get("id") for sp in answer.get("sections", [])]
        errors, notes = plan.repair_split(ir, ids, answer)
        if errors:
            return 1, {"errors": errors, "repairs": notes}
        created = plan.apply_split(ir, answer)
        wd = Path(args.work) / run.model_key(args.model)
        n = max([int(f.stem[4:]) for f in wd.glob("ir.r*.json")] + [0]) + 1
        run.save_json(wd / f"ir.r{n}.json", ir)
        return 0, {"created": created, "repairs": notes, "ir": str(wd / f"ir.r{n}.json")}
    if args.cmd == "write":
        ir = latest_ir(args.work, args.model)
        plan.finalize_names(ir)
        mpd.write(ir, args.output)
        return 0, {"output": args.output}
    if args.cmd == "check":
        report = check.check(args.input, args.output, pyldraw=not args.no_pyldraw)
        return (0 if report["ok"] else 1), report
    if args.cmd == "db-sync":
        return 0, db.sync(inputs(args.inputs))
    raise ValueError(args.cmd)


if __name__ == "__main__":
    sys.exit(main())
