#!/usr/bin/env bash
set -uo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
PY_BIN="$(backend_python)"
export_backend_runtime_env

LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/sync-loop.log"
mkdir -p "$LOG_DIR"

cd "$ROOT"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting sync loop" | tee -a "$LOG_FILE"

while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running sync_once.py" | tee -a "$LOG_FILE"
  if "$PY_BIN" "$ROOT/scripts/sync_once.py" 2>&1 | tee -a "$LOG_FILE"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync pass completed" | tee -a "$LOG_FILE"
  else
    status=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync pass failed with status $status; retrying after sleep" | tee -a "$LOG_FILE"
  fi
  sleep 60
done
