# ldraw-annotator-nova

Fast, resumable annotation of LDraw models (`.ldr`/`.mpd`, up to tens of MB, batches of
hundreds). It produces self-contained `.mpd` files with:

- a **semantic submodel hierarchy**: assemblies a builder would name, moving parts isolated,
  depth decided per branch;
- a one-line **description** after every `0 FILE`;
- a `0 // <part> (<colour>)` comment above every part;
- flexible parts (hoses, cables, flex axles…) isolated in their own submodels.

See [docs/output-contract.md](docs/output-contract.md) for the exact format, and
[AGENTS.md](AGENTS.md) for how agents operate it.

## Setup (once)

```sh
./setup.sh          # uv (or venv + pip); needs Python >= 3.12 and the `claude` CLI, logged in
```

## Use

```sh
./ldraw-annotator run unannotated/01-small --out output/annotated -j 6
./ldraw-annotator status
```

- **Resume.** If a run is aborted, run the same command again.
- **Speed.**
  - Only part references reach the LLM: geometry is set aside and copied verbatim.
  - Each LLM call is a single-shot structured answer (`claude -p --json-schema`) over a compact
    digest.
  - Calls of all models share one pool of `-j` workers.
- **Models.** `--split-model` (default `sonnet`) decides the hierarchy. `--refine-model` (default
  `haiku`) rewrites parent descriptions.

## Store results in the DB (manual)

```sh
./ldraw-annotator db-sync output/annotated
```

This upserts `MODELS_DESCRIPTIONS` and `SUBMODELS_DESCRIPTIONS`, plus `SUBMODELS_REFERENCES(model,
parent, child, count)` (created on first use), in `data/ldraw-info.db`. The FTS tables follow
through their triggers.

## Layout

| Path | What |
|---|---|
| `ldraw_annotator/mpd.py` | streaming MPD reader/writer, section classification, IR |
| `ldraw_annotator/flex.py` | flexible parts (LSynth, LDCad) |
| `ldraw_annotator/digest.py` | the compact views the LLM reads |
| `ldraw_annotator/plan.py` | answer schemas, validation, applying splits |
| `ldraw_annotator/check.py` | lossless and contract verification |
| `ldraw_annotator/run.py` | batch orchestration, `claude -p` calls, resume, status |
| `ldraw_annotator/db.py` | db-sync |
| `scripts/add_parts_descriptions.py` | part comments (standalone script, also used by the pipeline) |
| `prompts/`, `docs/annotation-guide.md` | what the LLM is told |
| `tests/` | deterministic tests: `.venv/bin/python -m pytest` |
