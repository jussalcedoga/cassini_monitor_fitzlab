#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

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

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Cassini Windows stack from $ROOT"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync loop log: $LOG_DIR/windows-sync-loop.log"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] API log: $LOG_DIR/windows-api.log"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Tunnel log: $LOG_DIR/windows-tunnel.log"

bash "$ROOT/scripts/run_sync_loop.sh" >>"$LOG_DIR/windows-sync-loop.log" 2>&1 &
PIDS+=("$!")

bash "$ROOT/scripts/run_api.sh" >>"$LOG_DIR/windows-api.log" 2>&1 &
PIDS+=("$!")

sleep 3
bash "$ROOT/scripts/run_cloudflare_tunnel.sh" >>"$LOG_DIR/windows-tunnel.log" 2>&1 &
PIDS+=("$!")

TUNNEL_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}"
if [[ -z "$TUNNEL_TOKEN" ]]; then
  TUNNEL_TOKEN="$(dotenv_value CLOUDFLARE_TUNNEL_TOKEN)"
fi

echo
echo "Cassini backend is running."
echo "Keep this terminal open."
if [[ -n "$TUNNEL_TOKEN" ]]; then
  echo "Named Cloudflare Tunnel mode is active."
  echo "Use the hostname configured for that tunnel as api_base in Streamlit secrets."
else
  echo "Quick Tunnel mode is active."
  echo "When Cloudflare prints a https://...trycloudflare.com URL in $LOG_DIR/windows-tunnel.log,"
  echo "use that value as api_base in Streamlit secrets."
fi
echo

wait
