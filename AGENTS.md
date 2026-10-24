# AGENTS.md — ldraw-annotator-nova

Turns unannotated LDraw models (`.ldr`/`.mpd`, any size) into annotated, self-contained `.mpd`
files: a semantic submodel hierarchy, a description per submodel, a `0 //` comment per part.
The format of the result is specified in [docs/output-contract.md](docs/output-contract.md).

## Annotate models (the normal case)

```sh
./ldraw-annotator run <files or directories...> --out output/annotated [-j 6]
./ldraw-annotator status            # progress, calls, cost, failures (reads ./work)
```

- `run` is **resumable**. If it's aborted, run the same command again: finished models and
  finished LLM calls are never redone.
- Models that already have an output are skipped. Use `--force` to redo them.
- Exit code 0 means every model is done and verified. 1 means some model failed its check (see
  `status` and `work/<model>/check.json`). 2 means bad input.
- Nothing to install, write or research: the tool calls the LLM itself (`claude -p`), checks every
  answer, and verifies every output.

## How it works (so you can debug it)

1. **prep** (no LLM, < 1 s even for 50 MB):
   - split sections and set embedded-part geometry aside;
   - add part comments (`scripts/add_parts_descriptions.py`);
   - move flexible parts into their own submodels (`ldraw_annotator/flex.py`).
2. **rounds** (LLM, parallel). Every section that needs a decision is shown as a compact digest.
   One single-shot call per unit returns, per section:
   - a description;
   - either "leaf" (stop) or new child submodels, one level down.

   New children are decided in the next round. Recursion ends when every branch is a leaf.
3. **refine** (LLM, 1 small call): parent descriptions are rewritten from their children's.
4. **write + check**: the output is written, then verified (lossless, contract rules, pyldraw3
   reparse).

Prompts: `prompts/split.md`, `prompts/refine.md`, plus the knowledge base
`docs/annotation-guide.md`.

## Debugging commands

| Command | Does |
|---|---|
| `./ldraw-annotator digest <model file or work name> [--tree]` | print what the LLM sees |
| `./ldraw-annotator prep <file>` | prep only; saves `work/<model>/ir.r0.json` |
| `./ldraw-annotator apply <model> <plan.json>` | apply a hand-written split plan to the latest IR |
| `./ldraw-annotator write <model> <out.mpd>` | write the latest IR |
| `./ldraw-annotator check <input> <output>` | verify an output |
| `.venv/bin/python -m pytest` | deterministic tests (no LLM) |

The work files are in `work/<model>/`:

| File | Contents |
|---|---|
| `ir.rN.json` | the IR after round N |
| `units/*.json` | accepted LLM answers |
| `units/*.failed.json` | rejected answers |
| `log.jsonl` | per call: time, tokens, cost, errors |
| `check.json` | the verification report |

## Rules

- Never edit `data/ldraw-info.db` from an agent. `db-sync` is run by the user.
- Never hand-edit outputs. Change the tool, prompts or guide, then rerun with `--force`.
