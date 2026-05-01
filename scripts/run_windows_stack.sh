#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
RUN_SYNC="${RUN_SYNC:-1}"
RUN_API="${RUN_API:-1}"
RUN_TUNNEL="${RUN_TUNNEL:-1}"

cleanup() {
  local code=$?
  trap - EXIT INT TERM
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait || true
  exit "$code"
}

PIDS=()
trap cleanup EXIT INT TERM

start_supervised() {
  local name="$1"
  local logfile="$2"
  shift 2

  (
    while true; do
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting $name" >>"$logfile"
      "$@" >>"$logfile" 2>&1
      status=$?
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] $name exited with status $status; restarting in 5 seconds" >>"$logfile"
      sleep 5
    done
  ) &
  PIDS+=("$!")
}

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Cassini Windows stack from $ROOT"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync loop log: $LOG_DIR/windows-sync-loop.log"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] API log: $LOG_DIR/windows-api.log"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Tunnel log: $LOG_DIR/windows-tunnel.log"

if [[ "$RUN_SYNC" == "1" ]]; then
  start_supervised "sync loop" "$LOG_DIR/windows-sync-loop.log" bash "$ROOT/scripts/run_sync_loop.sh"
fi

if [[ "$RUN_API" == "1" ]]; then
  start_supervised "API" "$LOG_DIR/windows-api.log" bash "$ROOT/scripts/run_api.sh"
fi

sleep 3
if [[ "$RUN_TUNNEL" == "1" ]]; then
  start_supervised "Cloudflare tunnel" "$LOG_DIR/windows-tunnel.log" bash "$ROOT/scripts/run_cloudflare_tunnel.sh"
fi

TUNNEL_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}"
if [[ -z "$TUNNEL_TOKEN" ]]; then
  TUNNEL_TOKEN="$(dotenv_value CLOUDFLARE_TUNNEL_TOKEN)"
fi

echo
echo "Cassini backend is running."
echo "Keep this terminal open."
echo "The supervisor restarts the sync loop, API, and tunnel if any of them exit unexpectedly."
if [[ "$RUN_TUNNEL" != "1" ]]; then
  echo "Tunnel launch is disabled in this session because RUN_TUNNEL=$RUN_TUNNEL."
elif [[ -n "$TUNNEL_TOKEN" ]]; then
  echo "Named Cloudflare Tunnel mode is active."
  echo "Use the hostname configured for that tunnel as api_base in Streamlit secrets."
else
  echo "Quick Tunnel mode is active."
  echo "When Cloudflare prints a https://...trycloudflare.com URL in $LOG_DIR/windows-tunnel.log,"
  echo "use that value as api_base in Streamlit secrets."
fi
echo

wait
