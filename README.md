# ldraw-annotator-nova

Turn plain LDraw models (`.ldr` / `.mpd`, from a few parts up to tens of MB) into annotated,
self-contained `.mpd` files. An LLM structures the model; the tool checks every answer and
verifies every output.

Each output has:

- a **submodel hierarchy** a builder would recognise (`chassis`, `wheels`, `doors`, `hinged-roof`…),
  as deep as each branch needs;
- a one-line **description** of every submodel;
- a **part comment** above every part: `0 // Plate  2 x  2 (black)`;
- **flexible parts** (hoses, cables, flex axles…) in their own submodels.

```
0 FILE 106 - car.ldr
0 Blue UNICEF car built on a black chassis with steering wheel, four grey wheels and a spare, …
0 Name: 106 - car.ldr
…
0 FILE 106 - chassis.ldr
0 Black car chassis: long plates and car base with mudguards, steering stand and wheel, wheel pin and red rear lights
…
0 // Plate  2 x 10 (black)
1 0 0 0 0 1 0 0 0 1 0 0 0 1 3832.dat
```

Nothing is lost: every part, colour, position and step of the input is still there. The exact
format is in [docs/output-contract.md](docs/output-contract.md).

## Install

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) (or `pipx`, or `pip`).

```sh
# installs the `ldraw-annotator` command, in its own environment
uv tool install https://github.com/anteloc/ldraw-annotator-nova/releases/download/v0.1.0/ldraw_annotator_nova-0.1.0-py3-none-any.whl

# or, with pipx
pipx install https://github.com/anteloc/ldraw-annotator-nova/releases/download/v0.1.0/ldraw_annotator_nova-0.1.0-py3-none-any.whl

ldraw-annotator --help
```

### The parts database

Part and colour names come from `ldraw-info.db`, a SQLite file published next to the wheel on
the [releases page](https://github.com/anteloc/ldraw-annotator-nova/releases). Download it once
and tell the tool where it is:

```sh
mkdir -p ~/.local/share/ldraw-annotator
curl -L -o ~/.local/share/ldraw-annotator/ldraw-info.db \
  https://github.com/anteloc/ldraw-annotator-nova/releases/download/v0.1.0/ldraw-info.db

export LDRAW_ANNOTATOR_DB=~/.local/share/ldraw-annotator/ldraw-info.db   # e.g. in ~/.zshrc
```

Or pass it to each command with `--db path/to/ldraw-info.db`.

### An LLM

The default is [z.ai](https://z.ai) GLM. Set your key:

```sh
export ZAI_API_KEY=...
# with a GLM Coding Plan key, also:
export ZAI_BASE_URL=https://api.z.ai/api/coding/paas/v4
```

Other providers, and local models with Ollama, are covered under
[Choosing the LLM](#choosing-the-llm).

## Annotate models

```sh
# one model
ldraw-annotator run my-car.mpd --out annotated

# several files and whole directories (searched recursively for .ldr and .mpd)
ldraw-annotator run models/ more-models/ truck.ldr --out annotated

# more calls in parallel (default 4): faster on big batches, if your provider allows it
ldraw-annotator run models/ --out annotated -j 8
```

`run` prints a JSON summary per model and writes `annotated/<model>.mpd`. A model's output is
written only once it has passed verification.

### Resume, redo, watch

All progress is kept in a work directory (`./work` by default; change it with `--work`).

```sh
# aborted (Ctrl-C, crash, network down)? run the same command again: finished models and
# finished LLM calls are not redone
ldraw-annotator run models/ --out annotated

# models that already have an output are skipped; --force redoes them
ldraw-annotator run my-car.mpd --out annotated --force

# progress of a batch, from another terminal: state, calls, tokens, failures per model
ldraw-annotator status
ldraw-annotator status --work /path/to/other/work
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | every model is done and verified |
| 1 | some model failed its verification (see `status` and `work/<model>/check.json`) |
| 2 | bad input: missing file, missing database, bad option |

A model is still "done" when the LLM couldn't decide a few sections after 3 attempts: those
sections are left as they were, and listed under `failed_units`.

## Choosing the LLM

There are two LLM jobs, each with its own model:

- **split** (`--split-model`) decides the hierarchy. It's the hard job: use a strong model.
- **refine** (`--refine-model`) rewrites parent descriptions from their children's. A small,
  cheap model is enough.

A model is written `provider:model`. Keys and endpoints come from each provider's usual
environment variables, so they never appear on the command line.

| Provider | Model example | Environment |
|---|---|---|
| z.ai (default) | `zai:glm-5.3` | `ZAI_API_KEY`, optional `ZAI_BASE_URL` |
| OpenRouter | `openrouter:z-ai/glm-5.3` | `OPENROUTER_API_KEY` |
| OpenAI | `openai:gpt-5.6-sol` | `OPENAI_API_KEY` |
| Anthropic | `anthropic:claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| Ollama (local) | `ollama:qwen3:32b` | optional `OLLAMA_HOST` (default `http://localhost:11434`) |

```sh
# defaults: zai:glm-5.3 for split, zai:glm-5.3-flash for refine
ldraw-annotator run models/ --out annotated

# the same models through OpenRouter
ldraw-annotator run models/ --out annotated \
  --split-model openrouter:z-ai/glm-5.3 --refine-model openrouter:z-ai/glm-5.3-flash

# Anthropic: a strong model for the hierarchy, a small one for descriptions
ldraw-annotator run models/ --out annotated \
  --split-model anthropic:claude-sonnet-5-5 --refine-model anthropic:claude-haiku-5-5
```

To avoid typing them every time, set your choice once:

```sh
export LDRAW_ANNOTATOR_SPLIT_MODEL=openrouter:z-ai/glm-5.3
export LDRAW_ANNOTATOR_REFINE_MODEL=openrouter:z-ai/glm-5.3-flash
```

### Local models with Ollama

No key and no cost; your models never leave your machine.

```sh
ollama serve                 # if it isn't running already
ollama pull qwen3:32b
ollama pull qwen3:8b

ldraw-annotator run my-car.mpd --out annotated \
  --split-model ollama:qwen3:32b --refine-model ollama:qwen3:8b -j 1

# Ollama on another machine
OLLAMA_HOST=http://gpu-box:11434 ldraw-annotator run my-car.mpd --out annotated \
  --split-model ollama:qwen3:32b --refine-model ollama:qwen3:8b
```

- Keep `-j` low (1-2): a local server answers one call at a time anyway.
- Small models often break the answer rules. Their sections are retried, then left unsplit and
  listed under `failed_units`. If `status` shows many, use a bigger model for `--split-model`.
- Mixing works well: a cloud model for split, a local one for refine:

  ```sh
  ldraw-annotator run models/ --out annotated --split-model zai:glm-5.3 --refine-model ollama:qwen3:8b
  ```

### Other providers

The calls go through [any-llm](https://github.com/mozilla-ai/any-llm), which supports many more
providers: Mistral, Gemini, Groq, Bedrock, LM Studio, llama.cpp and others. Some need their SDK
installed alongside the tool:

```sh
uv tool install --with 'any-llm-sdk[mistral]' <wheel url>
ldraw-annotator run models/ --out annotated --split-model mistral:mistral-large-latest
```

### Reasoning effort

`--thinking` (`none`, `low`, `medium`, `high`, `xhigh`; default `low`) sets how long reasoning
models think. `low` is the fastest setting that still answers well; with GLM, the provider's
default reasoning made calls about 10x slower.

## Other commands

```sh
# verify an annotated file against its input: lossless, format rules, pyldraw3 reparse
ldraw-annotator check my-car.mpd annotated/my-car.mpd

# see what the LLM is shown for a model (before or during a run)
ldraw-annotator digest my-car.mpd
ldraw-annotator digest my-car --tree        # the hierarchy so far, from the work directory

# prepare a model without any LLM: part comments + flexible parts, saved as work/<model>/ir.r0.json
ldraw-annotator prep my-car.mpd
```

Every command prints JSON, so results are easy to script:

```sh
ldraw-annotator status | jq '.[] | select(.state != "done") | .model'
```

## Store results in the database

`db-sync` writes annotated models into the database, for search and reuse:

```sh
ldraw-annotator db-sync annotated/
```

It upserts `MODELS_DESCRIPTIONS` and `SUBMODELS_DESCRIPTIONS`, plus `SUBMODELS_REFERENCES(model,
parent, child, count)` (created on first use). The full-text search tables follow through their
triggers. It's the only command that writes to the database, so keep a copy if you care about it.

## Development

```sh
git clone https://github.com/anteloc/ldraw-annotator-nova && cd ldraw-annotator-nova
uv sync --extra test              # .venv with the package installed in editable mode
uv run ldraw-annotator --help
uv run pytest                     # deterministic tests, no LLM; uses data/ldraw-info.db
uv build --wheel                  # dist/ldraw_annotator_nova-<version>-py3-none-any.whl
```

To publish a release, bump `version` in `pyproject.toml` and push a tag such as `v0.2.0`. The
`release` workflow runs the tests, then attaches the wheel and `ldraw-info.db` to that release.

How the pipeline works is described in [AGENTS.md](AGENTS.md).

## License

Code: [AGPL-3.0](LICENSE). Documentation and prompts: [CC BY-SA 4.0](CC-BY-SA-4.0).
