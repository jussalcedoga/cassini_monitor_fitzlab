import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.sync import sync_once

if __name__ == "__main__":
    sync_once()