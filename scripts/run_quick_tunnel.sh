#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/backend/.env" ]]; then
  set -a
  source "$ROOT/backend/.env"
  set +a
fi

if [[ -x "$ROOT/cloudflared" ]]; then
  CLOUDFLARED_BIN="$ROOT/cloudflared"
elif command -v cloudflared >/dev/null 2>&1; then
  CLOUDFLARED_BIN="$(command -v cloudflared)"
else
  echo "cloudflared not found in PATH and no bundled binary at $ROOT/cloudflared" >&2
  exit 1
fi

exec "$CLOUDFLARED_BIN" tunnel --url "http://${API_HOST:-127.0.0.1}:${API_PORT:-8000}"
