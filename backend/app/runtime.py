from __future__ import annotations

import logging
import os
import sys
import threading
import time

from app.config import PROJECT_ROOT
from app.sync import SyncWorker

log = logging.getLogger("cassini.runtime")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
RELOAD_EXIT_CODE = 75


def code_stamp() -> float:
    """Newest mtime of the backend code and its .env."""
    paths = [*(PROJECT_ROOT / "app").glob("*.py"), PROJECT_ROOT / ".env"]
    stamps = []
    for path in paths:
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(stamps, default=0.0)


def configure_logging() -> None:
    root = logging.getLogger()
    if not any(getattr(handler, "_cassini", False) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler._cassini = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(logging.INFO)


def keep_system_awake() -> bool:
    """Ask Windows not to idle-sleep or hibernate while this thread lives."""
    if os.name != "nt":
        return False
    import ctypes

    return bool(ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))


class Watchdog(threading.Thread):
    """Exit the process if the sync loop dies or wedges, or the code changes.

    The Windows service wrapper treats a non-zero exit as a failure and
    restarts the API, which is a cleaner recovery than limping along with
    stale data, and makes `git pull` deploy backend changes by itself.
    """

    CHECK_SECONDS = 30

    def __init__(self, worker: SyncWorker, stall_seconds: int):
        super().__init__(name="watchdog", daemon=True)
        self.worker = worker
        self.stall_seconds = stall_seconds
        self.stop_event = threading.Event()
        self._code_stamp = code_stamp()
        self._pending_stamp: float | None = None

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        if keep_system_awake():
            log.info("holding a system-required power request (no idle sleep/hibernate)")
        while not self.stop_event.wait(self.CHECK_SECONDS):
            keep_system_awake()
            if not self.worker.is_alive():
                self._exit("sync worker thread exited", 70)
            stalled = time.monotonic() - self.worker.heartbeat
            if stalled > self.stall_seconds:
                self._exit(f"sync loop stalled for {stalled:.0f}s", 71)

            # Reload once a code/.env change has been stable for one check,
            # so a half-finished `git pull` is never loaded.
            stamp = code_stamp()
            if stamp != self._code_stamp:
                if stamp == self._pending_stamp:
                    self._exit("backend code or .env changed; restarting to load it", RELOAD_EXIT_CODE)
                self._pending_stamp = stamp

    @staticmethod
    def _exit(reason: str, code: int) -> None:
        log.critical("%s; exiting so the service manager restarts the API", reason)
        logging.shutdown()
        os._exit(code)
