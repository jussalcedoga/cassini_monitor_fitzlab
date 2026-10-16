"""Mirror every BlueFors day folder into DuckDB once.

The CassiniAPI service already does this continuously; use this only for a
manual rebuild while the service is stopped (DuckDB allows one writer).
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.db import Store
from app.runtime import configure_logging
from app.sync import Syncer

if __name__ == "__main__":
    configure_logging()
    store = Store()
    try:
        syncer = Syncer(store)
        result = syncer.sync_all()
        result.update({key: str(value) for key, value in syncer.summary.items()})
        print(json.dumps(result, indent=2))
    finally:
        store.close()
