#!/bin/sh
# One-time setup for humans. Agents never need to install anything.
set -eu
task_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$task_dir"
if command -v uv >/dev/null 2>&1; then
  uv sync --locked --extra test 2>/dev/null || uv sync --extra test
else
  python3 -m venv .venv
  .venv/bin/python -m pip install -e '.[test]'
fi
command -v claude >/dev/null 2>&1 || echo "warning: 'claude' CLI not found; 'run' needs it" >&2
./ldraw-annotator --help > /dev/null && echo "ldraw-annotator ready"
