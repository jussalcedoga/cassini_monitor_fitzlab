#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-path}"
source "$ROOT/backend/.venv/bin/activate"
export PYTHONPATH="$ROOT/backend"
cd "$ROOT/backend"

if [[ "$MODE" == "path" ]]; then
python - <<'PY'
from app.sync import path_check
import json
print(json.dumps(path_check(), indent=2))
PY
elif [[ "$MODE" == "db" ]]; then
python - <<'PY'
from app.db import connect
con = connect()
print("rows:", con.execute("SELECT COUNT(*) FROM readings").fetchone()[0])
print("files:", con.execute("SELECT COUNT(*) FROM ingested_files").fetchone()[0])
print("latest:", con.execute("SELECT MAX(ts_eastern) FROM readings").fetchone()[0])
con.close()
PY
else
  echo "Usage: quick_test.sh [path|db]"
  exit 1
fi
