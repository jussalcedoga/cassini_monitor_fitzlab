from __future__ import annotations

from pathlib import Path

import duckdb

from app.config import DB_PATH, DB_READONLY_PATH, READONLY_MAX_LAG_SECONDS, ensure_dirs


def _path_mtime(path: Path) -> float | None:
    if not path.exists():
        return None

    try:
        return path.stat().st_mtime
    except OSError:
        return None


def read_target_status() -> dict[str, object]:
    snapshot_mtime = _path_mtime(DB_READONLY_PATH)
    main_mtime = _path_mtime(DB_PATH)

    target = DB_READONLY_PATH
    target_reason = "snapshot"

    if snapshot_mtime is None and main_mtime is not None:
        target = DB_PATH
        target_reason = "snapshot_missing"
    elif snapshot_mtime is not None and main_mtime is not None:
        lag_seconds = max(0.0, main_mtime - snapshot_mtime)
        if lag_seconds > READONLY_MAX_LAG_SECONDS:
            target = DB_PATH
            target_reason = f"snapshot_stale_by_{int(lag_seconds)}s"

    return {
        "target_path": str(target),
        "target_reason": target_reason,
        "snapshot_exists": snapshot_mtime is not None,
        "main_exists": main_mtime is not None,
        "snapshot_mtime": snapshot_mtime,
        "main_mtime": main_mtime,
        "readonly_max_lag_seconds": READONLY_MAX_LAG_SECONDS,
    }


def resolve_read_target() -> Path:
    return Path(str(read_target_status()["target_path"]))


def connect(write: bool = False) -> duckdb.DuckDBPyConnection:
    """
    write=False  -> open the freshest safe database for API reads
    write=True   -> open the main writable database
    """
    ensure_dirs()

    if write:
        con = duckdb.connect(str(DB_PATH))
        con.execute("PRAGMA threads=4;")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                ts_eastern TIMESTAMP PRIMARY KEY,
                P1 DOUBLE,
                P2 DOUBLE,
                P3 DOUBLE,
                P4 DOUBLE,
                P5 DOUBLE,
                P6 DOUBLE,
                T_50K DOUBLE,
                T_4K DOUBLE,
                T_Still DOUBLE,
                T_MXC DOUBLE,
                Flow DOUBLE,
                total_hours_scroll_1 DOUBLE,
                total_hours_scroll_2 DOUBLE,
                total_hours_turbo_1 DOUBLE,
                total_hours_pulse_tube DOUBLE,
                scroll_1 DOUBLE,
                scroll_2 DOUBLE,
                turbo_1 DOUBLE,
                pulse_tube DOUBLE,
                source_file VARCHAR
            );
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS ingested_files (
                path VARCHAR PRIMARY KEY,
                size_bytes BIGINT,
                mtime_ns BIGINT,
                ingested_at TIMESTAMP
            );
            """
        )
        return con

    target = resolve_read_target()
    return duckdb.connect(str(target), read_only=True)
