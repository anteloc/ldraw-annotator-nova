"""Batch orchestrator: prep -> rounds of LLM decisions -> refine -> write -> check. Resumable.

Each LLM call is one single-shot chat completion (llm.py, any provider through any-llm).
The system prompt is a prompt + the guide + the answer's JSON Schema (identical for every call,
so cacheable); the prompt is a digest; the reply's JSON object is validated by plan.py. Calls of all
models in the batch share one worker pool.

Work directory layout (the filesystem is the manifest; an abort never redoes finished work):
  work/<model>/ir.r0.json          IR after prep (part comments added, flexible parts isolated)
  work/<model>/units/r1-u00.json   one finished call: {"sections": [...], "plan": {...}}
  work/<model>/ir.r1.json          IR after round 1, and so on
  work/<model>/units/refine.json   the bottom-up description call
  work/<model>/check.json          verification report of the written output
  work/<model>/log.jsonl           one line per call: agent, unit, seconds, tokens, errors
  <out>/<model>.mpd                the result, written last
"""

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from pathlib import Path

from . import check, digest, llm, mpd, plan

MAX_ROUNDS = 10  # safety cap; recursion normally ends when every open section is a leaf
CALL_TIMEOUT = 900
ATTEMPTS = 3  # retries ask again only for the rejected sections, with the reasons
RATE_LIMIT_WAITS = [10, 20, 40, 80, 160]  # seconds to wait before re-sending a rate-limited call

# Default agents (LLMs): z.ai GLM (set ZAI_API_KEY; ZAI_BASE_URL for the GLM Coding Plan endpoint).
SPLIT_AGENT = "zai:glm-5.3"
REFINE_AGENT = "zai:glm-5.3-flash"
# Measured on 106-1: "low" answers in 1-9 s per call; the provider default and "none" (not
# honoured by GLM) stream long reasoning and took 90-130 s for the same plan.
THINKING = "low"
PROMPTS = resources.files(__package__) / "prompts"


def model_key(path):
    return re.sub(r"[^\w.-]+", "_", Path(path).stem).strip("_")


def save_json(path, data):
    mpd.atomic_write(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")))


class Runner:
    def __init__(self, out_dir, work_dir="work", jobs=4, split_agent=SPLIT_AGENT, refine_agent=REFINE_AGENT,
                 force=False, pyldraw=True, thinking=THINKING):
        self.out, self.work = Path(out_dir), Path(work_dir)
        self.jobs, self.force, self.pyldraw = jobs, force, pyldraw
        self.agents = {"split": split_agent, "refine": refine_agent}
        self.thinking = thinking
        for agent in self.agents.values():
            if ":" not in agent:
                raise ValueError(f"agent {agent!r} is not provider:agent (e.g. {SPLIT_AGENT}, ollama:qwen3:8b)")
        self.llm = ThreadPoolExecutor(jobs)
        self.log_lock = threading.Lock()
        guide = (PROMPTS / "annotation-guide.md").read_text()
        self.system = {}
        for kind, schema in (("split", plan.SPLIT_SCHEMA), ("refine", plan.REFINE_SCHEMA)):
            text = (PROMPTS / f"{kind}.md").read_text()
            self.system[kind] = (text + ("\n\n" + guide if kind == "split" else "")
                                 + "\n\n## Answer format\n\nReply with ONE JSON object and nothing else "
                                 "(no prose, no code fences, no tools), valid against this JSON Schema:\n\n"
                                 + json.dumps(schema, separators=(",", ":")))

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
            futures = {uid: self.llm.submit(self.unit, wd, ir, uid, ids, "split") for uid, ids in units.items()}
            # Units read the IR while they run (digests, checks): change it only once all are done.
            answers = {uid: future.result() for uid, future in futures.items()}
            for uid, ids in units.items():  # apply in a fixed order: ids and names are deterministic
                answer = answers[uid]
                plan.apply_split(ir, answer)
                answered = {sp["id"] for sp in answer["sections"]}
                if len(answered) < len(ids):
                    fail(ir, uid, [sid for sid in ids if sid not in answered])
            save_json(wd / f"ir.r{r}.json", ir)

        parents = digest.refine_ids(ir)
        if parents:
            answer = self.unit(wd, ir, "refine", parents, "refine")
            plan.apply_refine(ir, answer)
            answered = {sp["id"] for sp in answer["sections"]}
            if len(answered) < len(parents):  # these keep their round descriptions; flag them
                ir.setdefault("failed_units", []).append(
                    f"refine: {', '.join(sid for sid in parents if sid not in answered)}")
            save_json(wd / "ir.final.json", ir)

        plan.finalize_names(ir)
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

    def unit(self, wd, ir, uid, ids, kind):
        """Return the plan of one unit: {"sections": [...]} with every section answered correctly.

        A saved answer is reused. A retry asks again only for the sections that were missing or
        rejected, with the reasons. Sections still failing after ATTEMPTS are left out (the caller
        falls back for them) and recorded in units/<uid>.failed.json.
        """
        saved = wd / "units" / f"{uid}.json"
        if saved.exists():
            data = json.loads(saved.read_text())
            if data["sections"] == ids:
                return data["plan"]
        repair = plan.repair_split if kind == "split" else plan.repair_refine
        schema = plan.SPLIT_SCHEMA if kind == "split" else plan.REFINE_SCHEMA
        view = digest.unit_digest if kind == "split" else digest.tree_digest
        good, feedback, errors = {}, "", []
        for attempt in range(1, ATTEMPTS + 1):
            pending = [sid for sid in ids if sid not in good]
            text = view(ir, pending) + feedback
            answer, info = self.call(kind, schema, text)
            for wait in RATE_LIMIT_WAITS:  # the provider's rate limit doesn't use up an attempt
                if answer is not None or "rate limit" not in (info["error"] or "").lower():
                    break
                self.log(wd, dict(info, unit=uid, attempt=attempt, sections=len(pending),
                                  errors=[info["error"]], repairs=[], waited=wait))
                time.sleep(wait)
                answer, info = self.call(kind, schema, text)
            errors, notes, answered = [], [], set()
            if answer is None:
                errors.append(info["error"])
            for sp in (answer or {}).get("sections", []):
                sid = sp.get("id") if isinstance(sp, dict) else None
                if sid not in pending or sid in answered:
                    continue
                answered.add(sid)
                found, fixed = repair(ir, [sid], {"sections": [sp]})  # repairs sp in place
                errors, notes = errors + found, notes + fixed
                if not found:
                    good[sid] = sp
            if answer is not None:
                errors += [f"{sid}: missing from the answer" for sid in pending if sid not in answered]
            self.log(wd, dict(info, unit=uid, attempt=attempt, sections=len(pending), errors=errors[:10],
                              repairs=notes[:10]))
            if len(good) == len(ids):
                break
            feedback = ("\n\nYour previous answer for these sections was rejected for these reasons; "
                        "answer again, fixing them:\n- " + "\n- ".join(errors[:20]))
        result = {"sections": [good[sid] for sid in ids if sid in good]}
        if len(good) == len(ids):
            save_json(saved, {"sections": ids, "plan": result})
        else:
            save_json(wd / "units" / f"{uid}.failed.json", {"sections": ids, "errors": errors})
        return result

    def call(self, kind, schema, text):
        """One LLM call. Returns (the reply's JSON object or None, log info)."""
        t0 = time.time()
        info = {"agent": self.agents[kind], "input_chars": len(text)}
        try:
            reply, usage = llm.chat(self.agents[kind], self.system[kind], text, self.thinking, CALL_TIMEOUT)
        except llm.RateLimited as e:
            return None, dict(info, seconds=round(time.time() - t0, 1), error=f"rate limit: {e}"[:300])
        except Exception as e:  # network, auth, provider errors: the attempt fails, the batch goes on
            return None, dict(info, seconds=round(time.time() - t0, 1), error=f"{type(e).__name__}: {e}"[:300])
        info.update(usage, seconds=round(time.time() - t0, 1))
        try:
            return parse_answer(reply), dict(info, error=None)
        except ValueError as e:
            return None, dict(info, error=f"reply is not one JSON object: {e}")

    def log(self, wd, entry):
        wd.mkdir(parents=True, exist_ok=True)
        with self.log_lock, open(wd / "log.jsonl", "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def parse_answer(text):
    """The JSON object in a model's reply (tolerates code fences or prose around it)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found")
    answer = json.loads(text[start:end + 1])
    if not isinstance(answer, dict):
        raise ValueError("not a JSON object")
    return answer


def fail(ir, uid, ids):
    """Sections a unit couldn't get right: they stay as they are (unsplit), flagged in check.json."""
    ir.setdefault("failed_units", []).append(f"{uid}: {', '.join(ids)}")
    for sid in ids:
        s = ir["sections"][sid]
        s["todo"], s["state"] = False, "leaf"


# ------------------------------------------------------------ status


def status(work_dir="work", out_dir=None):
    """Per-model progress, derived from the files in the work directory."""
    rows = []
    if not Path(work_dir).is_dir():
        return rows
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
                     "tokens": sum((c.get("input_tokens") or 0) + (c.get("output_tokens") or 0) for c in calls),
                     "seconds": report and report.get("seconds"),
                     "failed_units": report.get("failed_units") if report else [],
                     "errors": report["errors"][:3] if report else []})
    return rows
