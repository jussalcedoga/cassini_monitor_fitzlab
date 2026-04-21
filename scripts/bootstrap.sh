#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "[bootstrap] project root: $ROOT"

python3 -m venv "$ROOT/backend/.venv"
source "$ROOT/backend/.venv/bin/activate"
pip install -U pip
pip install -r "$ROOT/backend/requirements.txt"
deactivate

echo "[bootstrap] backend env ready"

python3 -m venv "$ROOT/frontend/.venv"
source "$ROOT/frontend/.venv/bin/activate"
pip install -U pip
pip install -r "$ROOT/frontend/requirements.txt"
deactivate

echo "[bootstrap] frontend env ready"
