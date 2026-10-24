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
./setup.sh          # uv (or venv + pip); needs Python >= 3.12 and the Cline CLI (`npm install -g cline`)
```

The LLM calls go through the Cline CLI, in a **dedicated Cline profile**. Your own Cline settings,
MCP servers and history stay untouched, and the calls don't carry MCP tool schemas. Create the
profile once with your z.ai key:

```sh
cline auth -p openai-compatible -k <api-key> -b https://api.z.ai/api/coding/paas/v4 \
  -m glm-5.3 --data-dir ~/.cline/ldraw-annotator
```

## Use

```sh
./ldraw-annotator run unannotated/01-small --out output/annotated -j 6
./ldraw-annotator status
```

- **Resume.** If a run is aborted, run the same command again.
- **Speed.**
  - Only part references reach the LLM: geometry is set aside and copied verbatim.
  - Each LLM call is a single-shot JSON answer (`cline --plan --json`) over a compact digest,
    validated (and repaired where harmless) by the tool.
  - Calls of all models share one pool of `-j` workers. Provider rate limits are waited out
    automatically.
  - Measured: all 21 test models (up to 36 MB) in about 13 minutes of wall time with `-j 8`.
- **Models.** `--provider` (default `openai-compatible`). `--split-model` (default `glm-5.3`)
  decides the hierarchy. `--refine-model` (default `glm-5.3-flash`) rewrites parent descriptions.
  `--thinking` sets the reasoning effort (default `low`; GLM's default reasoning made calls
  ~10x slower). `--cline-dir` selects another profile.

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
| `ldraw_annotator/run.py` | batch orchestration, Cline calls, resume, status |
| `ldraw_annotator/db.py` | db-sync |
| `scripts/add_parts_descriptions.py` | part comments (standalone script, also used by the pipeline) |
| `prompts/`, `docs/annotation-guide.md` | what the LLM is told |
| `tests/` | deterministic tests: `.venv/bin/python -m pytest` |
