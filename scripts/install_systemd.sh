#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "Usage: sudo bash scripts/install_systemd.sh USERNAME"
  exit 1
fi
USER_NAME="$1"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_DIR="/etc/systemd/system"

sed "s|__USER__|$USER_NAME|g; s|__ROOT__|$ROOT|g" "$ROOT/services/cassini-api.service" | tee "$SERVICE_DIR/cassini-api.service" >/dev/null
sed "s|__USER__|$USER_NAME|g; s|__ROOT__|$ROOT|g" "$ROOT/services/cassini-sync.service" | tee "$SERVICE_DIR/cassini-sync.service" >/dev/null
sed "s|__USER__|$USER_NAME|g; s|__ROOT__|$ROOT|g" "$ROOT/services/cloudflared-quick.service" | tee "$SERVICE_DIR/cloudflared-quick.service" >/dev/null
cp "$ROOT/services/cassini-sync.timer" "$SERVICE_DIR/cassini-sync.timer"

systemctl daemon-reload
echo "Installed systemd units."
