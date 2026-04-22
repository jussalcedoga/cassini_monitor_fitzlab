#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/backend/.venv/bin/activate"
if [[ -f "$ROOT/backend/.env" ]]; then
  set -a
  source "$ROOT/backend/.env"
  set +a
fi
export PYTHONPATH="$ROOT/backend"
cd "$ROOT/backend"
python -m uvicorn app.api:app --host "${API_HOST:-127.0.0.1}" --port "${API_PORT:-8001}"
