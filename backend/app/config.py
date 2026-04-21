from pathlib import Path
import os
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

WAREHOUSE_ROOT = Path(
    os.getenv(
        "WAREHOUSE_ROOT",
        "/jumbo/fitzlab/code/BlueFors Log DB/data/warehouse/readings",
    )
)

DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"

DB_PATH = DATA_DIR / "cassini.duckdb"
DB_READONLY_PATH = DATA_DIR / "cassini_readonly.duckdb"

API_KEY = os.getenv("API_KEY", "cassini")
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))

ALLOWED_KEYS = {
    "P1","P2","P3","P4","P5","P6",
    "T_50K","T_4K","T_Still","T_MXC","Flow",
    "total_hours_scroll_1","total_hours_scroll_2",
    "total_hours_turbo_1","total_hours_pulse_tube",
    "scroll_1","scroll_2","turbo_1","pulse_tube",
}

def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)