#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

if command -v py >/dev/null 2>&1; then
  PY_BOOTSTRAP=(py -3)
elif command -v python >/dev/null 2>&1; then
  PY_BOOTSTRAP=(python)
elif command -v python3 >/dev/null 2>&1; then
  PY_BOOTSTRAP=(python3)
else
  echo "Python was not found. Install Python 3.11+ on the Windows host first." >&2
  exit 1
fi

if [[ ! -d "$ROOT/backend/.venv" ]]; then
  "${PY_BOOTSTRAP[@]}" -m venv "$ROOT/backend/.venv"
fi

PY_BIN="$(backend_python)"
"$PY_BIN" -m pip install --upgrade pip
"$PY_BIN" -m pip install -r "$ROOT/backend/requirements.txt"

echo
echo "Bootstrap complete."
echo "Next:"
echo "  1. Copy backend/.env.example to backend/.env"
echo "  2. Set BLUEFORS_LOGS_ROOT to the Windows BlueFors log directory"
echo "  3. Optionally set CLOUDFLARE_TUNNEL_TOKEN for a stable public hostname"
echo "  4. Run: bash scripts/run_windows_stack.sh"
