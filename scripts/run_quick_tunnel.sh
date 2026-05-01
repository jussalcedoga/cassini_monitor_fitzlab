#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/common.sh"
export_backend_runtime_env
CLOUDFLARED_BIN="$(cloudflared_bin)"
exec "$CLOUDFLARED_BIN" tunnel --url "http://${API_HOST}:${API_PORT}"
