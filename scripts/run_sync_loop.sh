#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/jumbo/fitzlab/code/bluefors_monitor_project"
LOG_FILE="$PROJECT_ROOT/logs/sync-loop.log"

cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/backend/.venv/bin/activate"
export PYTHONPATH="$PROJECT_ROOT/backend"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting sync loop" | tee -a "$LOG_FILE"

while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running sync_once.py" | tee -a "$LOG_FILE"
  python "$PROJECT_ROOT/scripts/sync_once.py" 2>&1 | tee -a "$LOG_FILE"
  sleep 60
done
