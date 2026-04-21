#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_SESSION="${API_SESSION:-cassini-api}"
TUNNEL_SESSION="${TUNNEL_SESSION:-cassini-cloudflare}"
RUN_SYNC_LOOP="${RUN_SYNC_LOOP:-0}"
SYNC_SESSION="${SYNC_SESSION:-cassini-sync}"

start_session() {
  local session_name="$1"
  local command="$2"

  if tmux has-session -t "$session_name" 2>/dev/null; then
    tmux kill-session -t "$session_name"
  fi

  tmux new-session -d -s "$session_name" "cd '$ROOT' && $command"
}

start_session "$API_SESSION" "bash scripts/run_api.sh"
start_session "$TUNNEL_SESSION" "bash scripts/run_cloudflared_quick.sh"

if [[ "$RUN_SYNC_LOOP" == "1" ]]; then
  start_session "$SYNC_SESSION" "bash scripts/run_sync_loop.sh"
fi

tmux ls
