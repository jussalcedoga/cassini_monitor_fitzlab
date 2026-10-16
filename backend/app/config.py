import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
load_dotenv(PROJECT_ROOT / ".env")


def _int_env(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


LOGS_ROOT = Path(
    os.getenv(
        "BLUEFORS_LOGS_ROOT",
        os.getenv("WAREHOUSE_ROOT", "C:/Users/Fitzlab/Bluefors logs"),
    )
).expanduser()

DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"

DB_PATH = DATA_DIR / "cassini.duckdb"

API_KEY = os.getenv("API_KEY", "cassini")
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = _int_env("API_PORT", 8001)

# Sync: BlueFors appends one sample per minute. Polling is cheap (only the
# newest day folders are listed), so a short interval keeps latency low.
SYNC_INTERVAL_SECONDS = _int_env("SYNC_INTERVAL_SECONDS", 10)
FULL_RESCAN_SECONDS = _int_env("FULL_RESCAN_SECONDS", 900)
RECENT_DAYS = _int_env("SYNC_RECENT_DAYS", 2)
TAIL_RESYNC_MINUTES = _int_env("TAIL_RESYNC_MINUTES", 5)
SYNC_TIME_BUDGET_SECONDS = _int_env("SYNC_TIME_BUDGET_SECONDS", 20)
COMPACT_INTERVAL_SECONDS = _int_env("COMPACT_INTERVAL_SECONDS", 24 * 3600)

# Exit (and let the service manager restart us) if the sync loop wedges.
SYNC_STALL_EXIT_SECONDS = _int_env("SYNC_STALL_EXIT_SECONDS", 900)
# /health reports "stale" when the newest reading is older than this.
STALE_DATA_SECONDS = _int_env("STALE_DATA_SECONDS", 600)

# Cloudflare tunnel monitoring (cloudflared runs as its own service).
TUNNEL_METRICS_URL = os.getenv("TUNNEL_METRICS_URL", "http://127.0.0.1:20241").rstrip("/")
TUNNEL_SERVICE_NAME = os.getenv("TUNNEL_SERVICE_NAME", "CassiniTunnel")
TUNNEL_WATCHDOG = os.getenv("TUNNEL_WATCHDOG", "1") == "1"
PUBLIC_URL_FILE = REPO_ROOT / "CURRENT_PUBLIC_URL.txt"

# Publish the public URL, HMAC-signed, to an ntfy topic so the Streamlit app
# can follow quick-tunnel URL changes (secrets api_url_topic / api_url_key).
URL_ANNOUNCE_SERVER = os.getenv("URL_ANNOUNCE_SERVER", "https://ntfy.sh").rstrip("/")
URL_ANNOUNCE_TOPIC = os.getenv("URL_ANNOUNCE_TOPIC", "")
URL_ANNOUNCE_KEY = os.getenv("URL_ANNOUNCE_KEY", "")

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
