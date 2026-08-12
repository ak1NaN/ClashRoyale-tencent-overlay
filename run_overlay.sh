#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"
PROFILE="$ROOT_DIR/config/arena_grid_profile.json"

[[ -x "$PYTHON" ]] || {
  printf 'Environment is missing. Double-click Setup.command first.\n' >&2
  exit 1
}

exec "$PYTHON" "$ROOT_DIR/overlay/arena_elixir_overlay.py" \
  --locked \
  --auto-connect \
  --runtime-root "$ROOT_DIR" \
  --settings "$ROOT_DIR/config/runtime_settings.json" \
  --profile "$PROFILE" \
  --script "$ROOT_DIR/overlay/hook_opponent_elixir.js"
