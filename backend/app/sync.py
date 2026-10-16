from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import duckdb

from app.bluefors_logs import READING_COLUMNS, build_day_frame, day_directories, day_signature, known_log_files
from app.config import (
    COMPACT_INTERVAL_SECONDS,
    FULL_RESCAN_SECONDS,
    LOGS_ROOT,
    RECENT_DAYS,
    SYNC_INTERVAL_SECONDS,
    SYNC_TIME_BUDGET_SECONDS,
    TAIL_RESYNC_MINUTES,
)
from app.db import Store

log = logging.getLogger("cassini.sync")

INSERT_COLUMNS = ", ".join(READING_COLUMNS)
COMPACT_AFTER_ROWS = 50_000


@dataclass
class DayState:
    size_bytes: int
    mtime_ns: int
    max_ts: Optional[datetime]


def path_check() -> dict[str, Any]:
    days = day_directories(LOGS_ROOT) if LOGS_ROOT.exists() else []
    return {
        "logs_root_exists": LOGS_ROOT.exists(),
        "logs_root": str(LOGS_ROOT),
        "source_count": len(days),
        "sample_sources": [str(path) for path in days[:5]],
    }


class Syncer:
    """Mirror BlueFors day folders into DuckDB with minimal write churn.

    - New or edited past days are rebuilt in one transaction.
    - The newest days (still being appended to) only replace the last few
      minutes of rows, so steady-state churn is a handful of rows per minute.
    - Days are processed newest-first, so a fresh database serves live data
      within seconds while history backfills.
    """

    def __init__(self, store: Store, logs_root: Path = LOGS_ROOT):
        self.store = store
        self.logs_root = logs_root
        self.days = self._load_days()
        self.version = 0
        self.rows_since_compact = 0
        self.last_compact = time.time()
        self.summary: dict[str, Any] = {}
        self.refresh_summary()

    def _load_days(self) -> dict[str, DayState]:
        with self.store.cursor() as cur:
            rows = cur.execute("SELECT path, size_bytes, mtime_ns, max_ts FROM ingested_days").fetchall()
        return {path: DayState(size, mtime, max_ts) for path, size, mtime, max_ts in rows}

    def refresh_summary(self) -> None:
        with self.store.cursor() as cur:
            rows_total, latest_ts = cur.execute("SELECT COUNT(*), MAX(ts_eastern) FROM readings").fetchone()
        self.summary = {
            "rows_total": rows_total,
            "latest_ts": latest_ts,
            "days_tracked": len(self.days),
        }

    def _day_dirs(self) -> list[Path]:
        if not self.logs_root.exists():
            raise FileNotFoundError(f"BlueFors logs root not found: {self.logs_root}")
        if known_log_files(self.logs_root):
            return [self.logs_root]
        with os.scandir(self.logs_root) as it:
            return sorted(Path(entry.path) for entry in it if entry.is_dir())

    def run_once(self, full_scan: bool = False, time_budget: Optional[float] = None) -> dict[str, Any]:
        started = time.monotonic()
        children = self._day_dirs()
        recent = children[-RECENT_DAYS:]
        recent_keys = {str(path) for path in recent}

        if full_scan:
            candidates = children
        else:
            candidates = [path for path in children if str(path) not in self.days]
            candidates += [path for path in recent if str(path) in self.days]

        changed: list[tuple[Path, tuple[int, int]]] = []
        for day_dir in candidates:
            signature = day_signature(day_dir)
            if signature == (0, 0):
                continue
            state = self.days.get(str(day_dir))
            if state is None or (state.size_bytes, state.mtime_ns) != signature:
                changed.append((day_dir, signature))
        changed.sort(key=lambda item: item[0].name, reverse=True)

        synced = 0
        for day_dir, signature in changed:
            if time_budget is not None and synced and time.monotonic() - started > time_budget:
                break
            self._sync_day(day_dir, signature, appendable=str(day_dir) in recent_keys)
            synced += 1

        if synced:
            self.version += 1
            self.refresh_summary()

        return {
            "days_seen": len(children),
            "days_changed": len(changed),
            "days_synced": synced,
            "backlog": len(changed) - synced,
            "full_scan": full_scan,
            "elapsed_s": round(time.monotonic() - started, 3),
        }

    def _sync_day(self, day_dir: Path, signature: tuple[int, int], appendable: bool) -> None:
        key = str(day_dir)
        previous = self.days.get(key)
        frame = build_day_frame(day_dir)
        max_ts = None if frame.empty else frame["ts_eastern"].max().to_pydatetime()

        tail_only = (
            appendable
            and previous is not None
            and previous.max_ts is not None
            and signature[0] >= previous.size_bytes
        )

        with self.store.write_lock, self.store.cursor() as cur:
            cur.execute("BEGIN TRANSACTION")
            try:
                if tail_only:
                    cutoff = previous.max_ts - timedelta(minutes=TAIL_RESYNC_MINUTES)
                    cur.execute("DELETE FROM readings WHERE source_file = ? AND ts_eastern >= ?", [key, cutoff])
                    frame = frame[frame["ts_eastern"] >= cutoff]
                else:
                    cur.execute("DELETE FROM readings WHERE source_file = ?", [key])

                if not frame.empty:
                    cur.register("day_frame", frame)
                    cur.execute(f"INSERT INTO readings ({INSERT_COLUMNS}) SELECT {INSERT_COLUMNS} FROM day_frame")
                    cur.unregister("day_frame")

                cur.execute("DELETE FROM ingested_days WHERE path = ?", [key])
                cur.execute(
                    "INSERT INTO ingested_days VALUES (?, ?, ?, ?, ?)",
                    [key, signature[0], signature[1], max_ts, datetime.now()],
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

        self.days[key] = DayState(signature[0], signature[1], max_ts)
        self.rows_since_compact += len(frame)

    def maybe_compact(self, backlog: int) -> bool:
        if backlog:
            return False
        due_by_rows = self.rows_since_compact >= COMPACT_AFTER_ROWS
        due_by_time = self.rows_since_compact > 0 and time.time() - self.last_compact >= COMPACT_INTERVAL_SECONDS
        if not (due_by_rows or due_by_time):
            return False
        self.compact()
        return True

    def compact(self) -> None:
        """Rewrite readings in time order and checkpoint.

        Sorted storage keeps DuckDB's per-row-group min/max pruning effective
        for time-window queries, and rewriting drops deleted rows so the file
        stays proportional to the data.
        """
        started = time.monotonic()
        size_before = self.store.size_bytes()
        with self.store.write_lock, self.store.cursor() as cur:
            cur.execute("BEGIN TRANSACTION")
            try:
                cur.execute("CREATE TEMP TABLE readings_sorted AS SELECT * FROM readings ORDER BY ts_eastern")
                cur.execute("DELETE FROM readings")
                cur.execute("INSERT INTO readings SELECT * FROM readings_sorted")
                cur.execute("DROP TABLE readings_sorted")
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            try:
                cur.execute("CHECKPOINT")
            except duckdb.Error as exc:
                log.warning("checkpoint after compaction deferred: %s", exc)

        self.rows_since_compact = 0
        self.last_compact = time.time()
        self.version += 1
        log.info(
            "compacted readings in %.1fs (db %.1f MB -> %.1f MB)",
            time.monotonic() - started,
            size_before / 1e6,
            self.store.size_bytes() / 1e6,
        )

    def sync_all(self) -> dict[str, Any]:
        """Run until every day folder is mirrored (used by the CLI script)."""
        result = self.run_once(full_scan=True)
        while result["backlog"]:
            result = self.run_once(full_scan=False)
        self.maybe_compact(0)
        return result


class SyncWorker(threading.Thread):
    def __init__(self, syncer: Syncer, on_change: Optional[Callable[[], None]] = None):
        super().__init__(name="sync-worker", daemon=True)
        self.syncer = syncer
        self.on_change = on_change
        self.stop_event = threading.Event()
        self.heartbeat = time.monotonic()
        self.last_success_at: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.last_error_at: Optional[datetime] = None
        self.last_result: dict[str, Any] = {}
        self.consecutive_errors = 0
        self._last_full_scan: Optional[float] = None

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        log.info("sync worker started (logs root: %s)", self.syncer.logs_root)
        while not self.stop_event.is_set():
            delay: float = SYNC_INTERVAL_SECONDS
            try:
                now = time.monotonic()
                full_scan = self._last_full_scan is None or now - self._last_full_scan >= FULL_RESCAN_SECONDS
                result = self.syncer.run_once(full_scan=full_scan, time_budget=SYNC_TIME_BUDGET_SECONDS)
                if full_scan:
                    self._last_full_scan = now
                compacted = self.syncer.maybe_compact(result["backlog"])

                if result["days_synced"] or compacted:
                    self._notify()
                if result["backlog"] or (result["days_synced"] > 1):
                    log.info("sync progress: %s", result)

                self.last_result = result
                self.last_success_at = datetime.now()
                self.consecutive_errors = 0
                if result["backlog"]:
                    delay = 0.2
            except Exception as exc:
                self.consecutive_errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.last_error_at = datetime.now()
                log.exception("sync pass failed (%d in a row)", self.consecutive_errors)
                delay = min(60.0, SYNC_INTERVAL_SECONDS * self.consecutive_errors)
            self.heartbeat = time.monotonic()
            self.stop_event.wait(delay)

    def _notify(self) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change()
        except Exception:
            log.exception("post-sync hook failed")

    def status(self) -> dict[str, Any]:
        return {
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "consecutive_errors": self.consecutive_errors,
            "last_pass": self.last_result,
            "interval_s": SYNC_INTERVAL_SECONDS,
        }
