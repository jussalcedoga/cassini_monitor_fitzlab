from __future__ import annotations

import os
import json
import shutil
import errno
from datetime import datetime
from pathlib import Path
from typing import Any, List, Tuple

from app.config import WAREHOUSE_ROOT, LOG_DIR, DB_PATH, DB_READONLY_PATH, ensure_dirs
from app.db import connect

SYNC_LOG = LOG_DIR / "sync.log"
LOCK_FILE = LOG_DIR / "sync.lock"
LOCK_STALE_AFTER_SECONDS = int(os.getenv("SYNC_LOCK_STALE_AFTER_SECONDS", "3600"))
LOCK_PROCESS_HINTS = ("sync_once.py", "run_sync_once.sh")

COLUMN_LIST = [
    "ts_eastern",
    "P1","P2","P3","P4","P5","P6",
    "T_50K","T_4K","T_Still","T_MXC","Flow",
    "total_hours_scroll_1","total_hours_scroll_2",
    "total_hours_turbo_1","total_hours_pulse_tube",
    "scroll_1","scroll_2","turbo_1","pulse_tube",
]

def log(msg: str) -> None:
    ensure_dirs()
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with SYNC_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

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

def parquet_files(root: Path) -> List[Path]:
    return sorted(root.glob("year=*/month=*/day=*/*.parquet"))

def path_check() -> dict[str, Any]:
    files = parquet_files(WAREHOUSE_ROOT) if WAREHOUSE_ROOT.exists() else []
    return {
        "warehouse_exists": WAREHOUSE_ROOT.exists(),
        "warehouse_root": str(WAREHOUSE_ROOT),
        "parquet_count": len(files),
        "sample_files": [str(path) for path in files[:5]],
    }

def file_sig(path: Path) -> Tuple[int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns

def changed_files(con, files: List[Path]) -> List[Path]:
    changed = []
    for p in files:
        size_bytes, mtime_ns = file_sig(p)
        row = con.execute(
            "SELECT size_bytes, mtime_ns FROM ingested_files WHERE path = ?",
            [str(p)]
        ).fetchone()
        if row is None or row[0] != size_bytes or row[1] != mtime_ns:
            changed.append(p)
    return changed

def refresh_readonly_snapshot() -> None:
    """
    Copy the writable DB to a read-only snapshot after sync finishes.
    Use a temp file + atomic replace so the API never sees a partial file.
    """
    tmp_path = DB_READONLY_PATH.with_suffix(".tmp")
    shutil.copy2(DB_PATH, tmp_path)
    os.replace(tmp_path, DB_READONLY_PATH)

def sync_once() -> dict:
    if not acquire_lock():
        log("sync already running, skipping")
        return {"status": "skipped_locked"}

    con = None
    try:
        if not WAREHOUSE_ROOT.exists():
            raise FileNotFoundError(f"Warehouse root not found: {WAREHOUSE_ROOT}")

        con = connect(write=True)
        files = parquet_files(WAREHOUSE_ROOT)
        changed = changed_files(con, files)

        log(f"warehouse={WAREHOUSE_ROOT}")
        log(f"discovered_parquet_files={len(files)} changed_files={len(changed)}")

        files_synced = 0

        for idx, p in enumerate(changed, start=1):
            size_bytes, mtime_ns = file_sig(p)
            log(f"[{idx}/{len(changed)}] syncing {p}")

            con.execute("DELETE FROM readings WHERE source_file = ?", [str(p)])

            con.execute(
                f"""
                INSERT OR REPLACE INTO readings (
                    {", ".join(COLUMN_LIST)},
                    source_file
                )
                SELECT
                    {", ".join(COLUMN_LIST)},
                    ? AS source_file
                FROM read_parquet(?)
                """,
                [str(p), str(p)]
            )

            con.execute(
                """
                INSERT OR REPLACE INTO ingested_files(path, size_bytes, mtime_ns, ingested_at)
                VALUES (?, ?, ?, NOW())
                """,
                [str(p), size_bytes, mtime_ns]
            )

            files_synced += 1

        total_rows = con.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        total_files = con.execute("SELECT COUNT(*) FROM ingested_files").fetchone()[0]

        con.close()
        con = None

        # Refresh the API snapshot only after the writer connection is fully closed
        refresh_readonly_snapshot()

        result = {
            "status": "ok",
            "files_seen": len(files),
            "files_changed": len(changed),
            "files_synced": files_synced,
            "tracked_files": total_files,
            "rows_total": total_rows,
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
