#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv/bin/python ]]; then
  printf 'First run detected. Starting setup...\n'
  ./Setup.command
fi

if ! ./run_overlay.sh; then
  printf '\nTencent Overlay failed to start. Review the message above.\n\n'
  read -r -p 'Press Return to close...' _
fi
