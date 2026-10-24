"""Batch orchestrator: prep -> rounds of LLM decisions -> refine -> write -> check. Resumable.

Each LLM call is one single-shot `claude -p` run: the system prompt is a prompt + the guide
(identical for every call, so cacheable), stdin is a digest, and the reply is JSON validated
by --json-schema and then by plan.py. Calls of all models in the batch share one worker pool.

Work directory layout (the filesystem is the manifest; an abort never redoes finished work):
  work/<model>/ir.r0.json          IR after prep (part comments added, flexible parts isolated)
  work/<model>/units/r1-u00.json   one finished call: {"sections": [...], "plan": {...}}
  work/<model>/ir.r1.json          IR after round 1, and so on
  work/<model>/units/refine.json   the bottom-up description call
  work/<model>/check.json          verification report of the written output
  work/<model>/log.jsonl           one line per call: unit, seconds, tokens, cost, errors
  <out>/<model>.mpd                the result, written last
"""

import json
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import check, digest, mpd, plan

MAX_ROUNDS = 10  # safety cap; recursion normally ends when every open section is a leaf
CALL_TIMEOUT = 900
ATTEMPTS = 2  # a rejected answer is retried once, with the validation errors appended


def model_key(path):
    return re.sub(r"[^\w.-]+", "_", Path(path).stem).strip("_")


def save_json(path, data):
    mpd.atomic_write(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")))


class Runner:
    def __init__(self, out_dir, work_dir="work", jobs=4, split_model="sonnet", refine_model="haiku",
                 force=False, pyldraw=True):
        self.out, self.work = Path(out_dir), Path(work_dir)
        self.jobs, self.force, self.pyldraw = jobs, force, pyldraw
        self.models = {"split": split_model, "refine": refine_model}
        self.llm = ThreadPoolExecutor(jobs)
        self.log_lock = threading.Lock()
        guide = (mpd.ROOT / "docs" / "annotation-guide.md").read_text()
        self.prompts = {}
        for kind in ("split", "refine"):
            text = (mpd.ROOT / "prompts" / f"{kind}.md").read_text()
            self.prompts[kind] = self.work / ".prompts" / f"{kind}.md"
            mpd.atomic_write(self.prompts[kind], text + ("\n\n" + guide if kind == "split" else ""))

    # ------------------------------------------------------------ batch

    def run(self, files):
        files = list(files)
        keys = [model_key(f) for f in files]
        if len(set(keys)) != len(keys):
            raise ValueError("two inputs map to the same work directory; rename one of them")
        try:
            with ThreadPoolExecutor(self.jobs) as pool:
                return list(pool.map(self.model_safely, files))
        finally:
            self.llm.shutdown()

    def model_safely(self, path):
        try:
            return self.model(Path(path))
        except Exception as e:  # one broken model never stops the batch
            return {"model": Path(path).name, "status": "error", "error": f"{type(e).__name__}: {e}"}

    # ------------------------------------------------------------ one model

    def model(self, path):
        t0 = time.time()
        wd, out = self.work / model_key(path), self.out / (path.stem + ".mpd")
        if out.exists() and not self.force:
            return {"model": path.name, "status": "done", "skipped": True, "output": str(out)}
        rounds = sorted(int(p.stem[4:]) for p in wd.glob("ir.r*.json"))
        if rounds and not self.force:
            r = rounds[-1]
            ir = json.loads((wd / f"ir.r{r}.json").read_text())
        else:
            r, ir = 0, mpd.load(path)
            save_json(wd / "ir.r0.json", ir)

        while r < MAX_ROUNDS:
            todo = [sid for sid in mpd.output_order(ir) if ir["sections"][sid].get("todo")]
            if not todo:
                break
            r += 1
            units = {f"r{r}-u{k:02d}": ids for k, ids in enumerate(digest.pack_units(ir, todo))}
            futures = {uid: self.llm.submit(self.unit, wd, ir, uid, ids, "split",
                                            digest.unit_digest(ir, ids))
                       for uid, ids in units.items()}
            for uid, ids in units.items():  # apply in a fixed order: ids and names are deterministic
                answer = futures[uid].result()
                if answer is None:
                    fail(ir, uid, ids)
                else:
                    plan.apply_split(ir, answer)
            save_json(wd / f"ir.r{r}.json", ir)

        parents = digest.refine_ids(ir)
        if parents:
            answer = self.unit(wd, ir, "refine", parents, "refine", digest.tree_digest(ir, parents))
            if answer is not None:
                plan.apply_refine(ir, answer)
            save_json(wd / "ir.final.json", ir)

        draft = wd / out.name
        mpd.write(ir, draft)
        report = check.check(path, draft, pyldraw=self.pyldraw)
        report.update(rounds=r, seconds=round(time.time() - t0, 1), failed_units=ir.get("failed_units", []))
        save_json(wd / "check.json", report)
        if report["ok"]:
            self.out.mkdir(parents=True, exist_ok=True)
            draft.replace(out)
        return {"model": path.name, "status": "done" if report["ok"] else "check_failed",
                "output": str(out if report["ok"] else draft), "rounds": r,
                "seconds": report["seconds"], "errors": report["errors"][:5],
                "failed_units": report["failed_units"]}

    # ------------------------------------------------------------ one LLM call

    def unit(self, wd, ir, uid, ids, kind, text):
        """Return a validated plan for one unit, reusing a saved answer; None if it failed."""
        saved = wd / "units" / f"{uid}.json"
        if saved.exists():
            data = json.loads(saved.read_text())
            if data["sections"] == ids:
                return data["plan"]
        repair = plan.repair_split if kind == "split" else plan.repair_refine
        schema = plan.SPLIT_SCHEMA if kind == "split" else plan.REFINE_SCHEMA
        feedback, errors = "", []
        for attempt in range(1, ATTEMPTS + 1):
            answer, info = self.call(kind, schema, text + feedback)
            errors, notes = ([info["error"]], []) if answer is None else repair(ir, ids, answer)
            self.log(wd, dict(info, unit=uid, attempt=attempt, sections=len(ids), errors=errors[:10],
                              repairs=notes[:10]))
            if not errors:
                save_json(saved, {"sections": ids, "plan": answer})
                return answer
            feedback = ("\n\nYour previous answer was rejected for these reasons; answer again, "
                        "fixing them:\n- " + "\n- ".join(errors[:20]))
        save_json(wd / "units" / f"{uid}.failed.json", {"sections": ids, "errors": errors})
        return None

    def call(self, kind, schema, text):
        """One headless Claude call. Returns (structured output or None, log info)."""
        cmd = ["claude", "-p", "--model", self.models[kind], "--tools", "", "--output-format", "json",
               "--no-session-persistence", "--strict-mcp-config", "--json-schema", json.dumps(schema),
               "--system-prompt-file", str(self.prompts[kind].resolve())]
        t0 = time.time()
        info = {"model": self.models[kind], "input_chars": len(text)}
        try:
            # A neutral working directory keeps project files out of the model's context.
            proc = subprocess.run(cmd, input=text, capture_output=True, text=True,
                                  timeout=CALL_TIMEOUT, cwd=tempfile.gettempdir())
            data = json.loads(proc.stdout or "{}")
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
            return None, dict(info, seconds=round(time.time() - t0, 1), error=f"{type(e).__name__}: {e}")
        usage = data.get("usage") or {}
        info.update(seconds=round(time.time() - t0, 1), cost=data.get("total_cost_usd"),
                    input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                    cache_read=usage.get("cache_read_input_tokens"),
                    cache_write=usage.get("cache_creation_input_tokens"))
        answer = data.get("structured_output")
        if data.get("is_error") or data.get("subtype") != "success" or not isinstance(answer, dict):
            detail = data.get("result") or proc.stderr.strip()[-300:] or f"exit code {proc.returncode}"
            return None, dict(info, error=f"claude call failed: {str(detail)[:300]}")
        return answer, dict(info, error=None)

    def log(self, wd, entry):
        wd.mkdir(parents=True, exist_ok=True)
        with self.log_lock, open(wd / "log.jsonl", "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def fail(ir, uid, ids):
    """A unit that failed twice: its sections stay as they are (unsplit), flagged in check.json."""
    ir.setdefault("failed_units", []).append(uid)
    for sid in ids:
        s = ir["sections"][sid]
        s["todo"], s["state"] = False, "leaf"


# ------------------------------------------------------------ status


def status(work_dir="work", out_dir=None):
    """Per-model progress, derived from the files in the work directory."""
    rows = []
    for wd in sorted(p for p in Path(work_dir).iterdir() if p.is_dir() and not p.name.startswith(".")):
        calls = [json.loads(l) for l in (wd / "log.jsonl").read_text().splitlines()] \
            if (wd / "log.jsonl").exists() else []
        report = json.loads((wd / "check.json").read_text()) if (wd / "check.json").exists() else None
        rounds = sorted(int(p.stem[4:]) for p in wd.glob("ir.r*.json"))
        if report:
            state = "done" if report["ok"] else "check_failed"
        elif (wd / "units" / "refine.json").exists():
            state = "refined"
        elif rounds:
            state = f"round {rounds[-1]}" if rounds[-1] else "prepped"
        else:
            state = "new"
        rows.append({"model": wd.name, "state": state, "rounds": rounds[-1] if rounds else None,
                     "calls": len(calls), "failed_calls": sum(1 for c in calls if c.get("errors")),
                     "llm_seconds": round(sum(c.get("seconds") or 0 for c in calls), 1),
                     "cost_usd": round(sum(c.get("cost") or 0 for c in calls), 4),
                     "seconds": report and report.get("seconds"),
                     "failed_units": report.get("failed_units") if report else [],
                     "errors": report["errors"][:3] if report else []})
    return rows
