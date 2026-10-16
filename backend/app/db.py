from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from app.bluefors_logs import VALUE_COLUMNS
from app.config import DB_PATH, ensure_dirs

# No PRIMARY KEY on readings: DuckDB's ART index grew without bound under the
# per-minute delete/insert churn (910k rows had bloated the old file to 9.3 GiB).
# Rows are replaced per source_file inside transactions instead.
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS readings (
    ts_eastern TIMESTAMP NOT NULL,
    {", ".join(f"{column} DOUBLE" for column in VALUE_COLUMNS)},
    source_file VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS ingested_days (
    path VARCHAR NOT NULL,
    size_bytes BIGINT,
    mtime_ns BIGINT,
    max_ts TIMESTAMP,
    ingested_at TIMESTAMP
);
"""


class Store:
    """One DuckDB database owned by this process.

    DuckDB allows a single writer process per file, so the API process owns
    the connection and hands out a cursor per thread (cursors are safe to use
    concurrently; DuckDB's MVCC keeps readers consistent during writes).
    """

    def __init__(self, path: Path = DB_PATH, read_only: bool = False):
        ensure_dirs()
        self.path = path
        self._con = duckdb.connect(
            str(path),
            read_only=read_only,
            config={"threads": 4, "memory_limit": "2GB"},
        )
        if not read_only:
            self._con.execute(SCHEMA)
        self.write_lock = threading.Lock()

    @contextmanager
    def cursor(self) -> Iterator[duckdb.DuckDBPyConnection]:
        cur = self._con.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def close(self) -> None:
        self._con.close()
