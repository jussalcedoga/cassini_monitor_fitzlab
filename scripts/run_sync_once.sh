#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/backend/.venv/bin/activate"
export PYTHONPATH="$ROOT/backend"
cd "$ROOT/backend"
python "$ROOT/scripts/sync_once.py"
