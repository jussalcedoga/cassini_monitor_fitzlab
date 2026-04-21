from __future__ import annotations

import duckdb
from app.config import DB_PATH, DB_READONLY_PATH, ensure_dirs

def connect(write: bool = False) -> duckdb.DuckDBPyConnection:
    """
    write=False  -> open the read-only snapshot database
    write=True   -> open the main writable database
    """
    ensure_dirs()

    if write:
        con = duckdb.connect(str(DB_PATH))
        con.execute("PRAGMA threads=4;")
        con.execute("""
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
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS ingested_files (
                path VARCHAR PRIMARY KEY,
                size_bytes BIGINT,
                mtime_ns BIGINT,
                ingested_at TIMESTAMP
            );
        """)
        return con

    # API path: read the snapshot copy only
    target = DB_READONLY_PATH if DB_READONLY_PATH.exists() else DB_PATH
    return duckdb.connect(str(target), read_only=True)