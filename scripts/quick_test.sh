#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/common.sh"
MODE="${1:-path}"
PY_BIN="$(backend_python)"
export PYTHONPATH="$ROOT/backend"
cd "$ROOT/backend"

if [[ "$MODE" == "path" ]]; then
"$PY_BIN" - <<'PY'
from app.sync import path_check
import json
print(json.dumps(path_check(), indent=2))
PY
elif [[ "$MODE" == "db" ]]; then
"$PY_BIN" - <<'PY'
from app.db import Store
store = Store(read_only=True)
with store.cursor() as con:
    print("rows:", con.execute("SELECT COUNT(*) FROM readings").fetchone()[0])
    print("days:", con.execute("SELECT COUNT(*) FROM ingested_days").fetchone()[0])
    print("latest:", con.execute("SELECT MAX(ts_eastern) FROM readings").fetchone()[0])
store.close()
PY
else
  echo "Usage: quick_test.sh [path|db]"
  exit 1
fi
