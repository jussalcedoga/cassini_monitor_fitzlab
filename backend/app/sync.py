from __future__ import annotations

import errno
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.bluefors_logs import READING_COLUMNS, build_day_frame, day_directories, day_signature
from app.config import DATA_DIR, DB_PATH, DB_READONLY_PATH, LOG_DIR, LOGS_ROOT, ensure_dirs
from app.db import connect

SYNC_LOG = LOG_DIR / "sync.log"
LOCK_FILE = LOG_DIR / "sync.lock"
LOCK_STALE_AFTER_SECONDS = int(os.getenv("SYNC_LOCK_STALE_AFTER_SECONDS", "3600"))
LOCK_PROCESS_HINTS = ("sync_once.py", "run_sync_once.sh")


def log(msg: str) -> None:
    ensure_dirs()
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with SYNC_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _lock_age_seconds() -> float:
    if not LOCK_FILE.exists():
        return 0.0
    return max(0.0, datetime.now().timestamp() - LOCK_FILE.stat().st_mtime)


def _read_lock_info() -> dict[str, Any]:
    info: dict[str, Any] = {"pid": None, "started_at": None}
    if not LOCK_FILE.exists():
        return info

    try:
        raw = LOCK_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return info

    if not raw:
        return info

    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                info["pid"] = int(payload.get("pid")) if payload.get("pid") is not None else None
                info["started_at"] = payload.get("started_at")
                return info
        except Exception:
            pass

    try:
        info["pid"] = int(raw)
    except Exception:
        pass
    return info


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM


def _pid_looks_like_sync(pid: int) -> bool | None:
    cmdline_path = Path("/proc") / str(pid) / "cmdline"
    if not cmdline_path.exists():
        return None

    try:
        cmdline = cmdline_path.read_text(encoding="utf-8", errors="ignore").replace("\x00", " ")
    except Exception:
        return None

    return any(hint in cmdline for hint in LOCK_PROCESS_HINTS)


def _stale_lock_reason() -> str | None:
    if not LOCK_FILE.exists():
        return None

    info = _read_lock_info()
    pid = info.get("pid")
    age_seconds = _lock_age_seconds()

    if pid is not None:
        if not _pid_exists(pid):
            return f"pid {pid} is not running"

        looks_like_sync = _pid_looks_like_sync(pid)
        if looks_like_sync is False:
            return f"pid {pid} is not a sync process"

    if age_seconds >= LOCK_STALE_AFTER_SECONDS:
        age_minutes = round(age_seconds / 60, 1)
        return f"lock age {age_minutes} minutes exceeds {LOCK_STALE_AFTER_SECONDS} seconds"

    return None


def _clear_stale_lock(reason: str) -> None:
    try:
        LOCK_FILE.unlink()
        log(f"cleared stale lock: {reason}")
    except FileNotFoundError:
        pass
    except Exception as exc:
        log(f"failed to clear stale lock: {exc}")


def acquire_lock() -> bool:
    ensure_dirs()
    stale_reason = _stale_lock_reason()
    if stale_reason is not None:
        _clear_stale_lock(stale_reason)

    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "started_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        os.write(fd, payload.encode("utf-8"))
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_lock() -> None:
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        pass


def source_roots(root: Path) -> list[Path]:
    return day_directories(root)


def path_check() -> dict[str, Any]:
    days = source_roots(LOGS_ROOT) if LOGS_ROOT.exists() else []
    return {
        "logs_root_exists": LOGS_ROOT.exists(),
        "logs_root": str(LOGS_ROOT),
        "source_count": len(days),
        "sample_sources": [str(path) for path in days[:5]],
    }


def changed_sources(con, day_dirs: list[Path]) -> list[Path]:
    changed = []
    for day_dir in day_dirs:
        size_bytes, mtime_ns = day_signature(day_dir)
        row = con.execute(
            "SELECT size_bytes, mtime_ns FROM ingested_files WHERE path = ?",
            [str(day_dir)],
        ).fetchone()
        if row is None or row[0] != size_bytes or row[1] != mtime_ns:
            changed.append(day_dir)
    return changed


def refresh_readonly_snapshot() -> None:
    ensure_dirs()

    legacy_tmp_path = DB_READONLY_PATH.with_suffix(".tmp")
    if legacy_tmp_path.exists():
        try:
            legacy_tmp_path.unlink()
            log(f"removed stale legacy snapshot temp file: {legacy_tmp_path}")
        except Exception as exc:
            log(f"could not remove legacy snapshot temp file {legacy_tmp_path}: {exc}")

    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{DB_READONLY_PATH.stem}.",
        suffix=".next",
        dir=str(DATA_DIR),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        shutil.copy2(DB_PATH, tmp_path)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, DB_READONLY_PATH)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def sync_once() -> dict:
    if not acquire_lock():
        log("sync already running, skipping")
        return {"status": "skipped_locked"}

    con = None
    try:
        if not LOGS_ROOT.exists():
            raise FileNotFoundError(f"BlueFors logs root not found: {LOGS_ROOT}")

        con = connect(write=True)
        days = source_roots(LOGS_ROOT)
        changed = changed_sources(con, days)

        log(f"logs_root={LOGS_ROOT}")
        log(f"discovered_log_sources={len(days)} changed_sources={len(changed)}")

        files_synced = 0
        latest_ts = None

        for idx, day_dir in enumerate(changed, start=1):
            size_bytes, mtime_ns = day_signature(day_dir)
            log(f"[{idx}/{len(changed)}] rebuilding {day_dir}")

            day_df = build_day_frame(day_dir)
            con.execute("DELETE FROM readings WHERE source_file = ?", [str(day_dir)])

            if not day_df.empty:
                con.register("day_frame", day_df)
                con.execute(
                    f"""
                    INSERT OR REPLACE INTO readings ({", ".join(READING_COLUMNS)})
                    SELECT {", ".join(READING_COLUMNS)}
                    FROM day_frame
                    """
                )
                con.unregister("day_frame")

            con.execute(
                """
                INSERT OR REPLACE INTO ingested_files(path, size_bytes, mtime_ns, ingested_at)
                VALUES (?, ?, ?, NOW())
                """,
                [str(day_dir), size_bytes, mtime_ns],
            )
            files_synced += 1

        total_rows = con.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        total_sources = con.execute("SELECT COUNT(*) FROM ingested_files").fetchone()[0]
        latest_ts = con.execute("SELECT MAX(ts_eastern) FROM readings").fetchone()[0]

        con.close()
        con = None
        refresh_readonly_snapshot()

        result = {
            "status": "ok",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "sources_seen": len(days),
            "sources_changed": len(changed),
            "sources_synced": files_synced,
            "tracked_sources": total_sources,
            "rows_total": total_rows,
            "latest_ts_eastern": latest_ts.isoformat() if latest_ts is not None else None,
            "snapshot_path": str(DB_READONLY_PATH),
        }
        log(json.dumps(result))
        return result
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        release_lock()
