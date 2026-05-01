#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/common.sh"
PY_BIN="$(backend_python)"
export_backend_runtime_env
cd "$ROOT/backend"
"$PY_BIN" -m uvicorn app.api:app --host "$API_HOST" --port "$API_PORT"
