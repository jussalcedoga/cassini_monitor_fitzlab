#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

backend_python() {
  local candidates=(
    "$ROOT/backend/.venv/Scripts/python.exe"
    "$ROOT/backend/.venv/bin/python"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi

  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi

  echo "No usable Python interpreter found. Create backend/.venv first." >&2
  return 1
}

dotenv_value() {
  local key="$1"
  local env_path="$ROOT/backend/.env"
  local py_bin
  py_bin="$(backend_python)"
  "$py_bin" - "$env_path" "$key" <<'PY'
from pathlib import Path
import sys

try:
    from dotenv import dotenv_values
except Exception:
    sys.exit(0)

env_path = Path(sys.argv[1])
env_key = sys.argv[2]
if env_path.exists():
    value = dotenv_values(env_path).get(env_key)
    if value is not None:
        print(value)
PY
}

export_backend_runtime_env() {
  local api_host_value="${API_HOST:-}"
  local api_port_value="${API_PORT:-}"

  if [[ -z "$api_host_value" ]]; then
    api_host_value="$(dotenv_value API_HOST)"
  fi

  if [[ -z "$api_port_value" ]]; then
    api_port_value="$(dotenv_value API_PORT)"
  fi

  export API_HOST="${api_host_value:-127.0.0.1}"
  export API_PORT="${api_port_value:-8001}"
  export PYTHONPATH="$ROOT/backend"
}

cloudflared_bin() {
  local candidates=(
    "$ROOT/cloudflared.exe"
    "$ROOT/cloudflared"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  if command -v cloudflared >/dev/null 2>&1; then
    command -v cloudflared
    return 0
  fi

  if command -v cloudflared.exe >/dev/null 2>&1; then
    command -v cloudflared.exe
    return 0
  fi

  echo "cloudflared not found in PATH and no bundled binary in repo root." >&2
  return 1
}
