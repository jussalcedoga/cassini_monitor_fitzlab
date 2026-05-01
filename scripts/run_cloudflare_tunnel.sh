#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
export_backend_runtime_env

CLOUDFLARED_BIN="$(cloudflared_bin)"
TUNNEL_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}"

if [[ -z "$TUNNEL_TOKEN" ]]; then
  TUNNEL_TOKEN="$(dotenv_value CLOUDFLARE_TUNNEL_TOKEN)"
fi

if [[ -n "$TUNNEL_TOKEN" ]]; then
  exec "$CLOUDFLARED_BIN" tunnel run --token "$TUNNEL_TOKEN"
fi

exec "$CLOUDFLARED_BIN" tunnel --url "http://${API_HOST}:${API_PORT}"
