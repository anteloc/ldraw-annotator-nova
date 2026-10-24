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
command -v cline >/dev/null 2>&1 || echo "warning: 'cline' CLI not found; 'run' needs it (npm install -g cline)" >&2
profile=$HOME/.cline/ldraw-annotator
[ -f "$profile/settings/providers.json" ] || cat >&2 <<MSG
warning: no dedicated Cline profile yet. Create it once (your key stays in that profile only):
  cline auth -p openai-compatible -k <api-key> -b https://api.z.ai/api/coding/paas/v4 -m glm-5.3 --data-dir $profile
MSG
./ldraw-annotator --help > /dev/null && echo "ldraw-annotator ready"
