from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from numbers import Integral, Real
from typing import Any, Callable, Hashable, Optional

import numpy as np
import orjson
import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import ALLOWED_KEYS, API_KEY, STALE_DATA_SECONDS, SYNC_STALL_EXIT_SECONDS
from app.db import Store
from app.runtime import Watchdog, configure_logging
from app.sync import Syncer, SyncWorker
from app.tunnel import TunnelMonitor

log = logging.getLogger("cassini.api")

DASHBOARD_KEYS = [
    "T_50K",
    "T_4K",
    "T_Still",
    "T_MXC",
    "P1",
    "P2",
    "P3",
    "P4",
    "P5",
    "P6",
    "Flow",
    "pulse_tube",
    "turbo_1",
    "scroll_1",
    "scroll_2",
]

DEFAULT_DASHBOARD_HOURS = 48  # the Streamlit app's default window
DEFAULT_DASHBOARD_MAX_POINTS = 1200
CACHE_MAX_AGE_SECONDS = 60  # "last N hours" windows move even without new data
# A pulse-tube phase must last this long to count as a cooldown or warm-up;
# shorter on/off blips are ignored. The ongoing phase is always reported.
PHASE_MIN_DURATION = timedelta(hours=2)
PHASE_LABELS = {"cooldown": "Cooldown started", "warmup": "Warm-up started"}


class PayloadCache:
    """Computed payloads keyed by request and valid for one data version.

    The sync worker bumps the version whenever readings change, so repeated
    dashboard refreshes are served from memory instead of re-querying.
    """

    def __init__(self, max_entries: int = 256):
        self.max_entries = max_entries
        self._entries: dict[Hashable, tuple[int, float, Any]] = {}
        self._lock = threading.Lock()
        self._build_locks: dict[Hashable, threading.Lock] = {}

    def _fresh(self, key: Hashable, version: int) -> Optional[tuple[int, float, Any]]:
        entry = self._entries.get(key)
        if entry and entry[0] == version and time.monotonic() - entry[1] < CACHE_MAX_AGE_SECONDS:
            return entry
        return None

    def get(self, key: Hashable, version: int, builder: Callable[[], Any]) -> Any:
        entry = self._fresh(key, version)
        if entry:
            return entry[2]
        with self._lock:
            build_lock = self._build_locks.setdefault(key, threading.Lock())
        with build_lock:
            entry = self._fresh(key, version)
            if entry:
                return entry[2]
            value = builder()
            with self._lock:
                self._entries[key] = (version, time.monotonic(), value)
                if len(self._entries) > self.max_entries:
                    oldest = min(self._entries, key=lambda k: self._entries[k][1])
                    self._entries.pop(oldest, None)
                    self._build_locks.pop(oldest, None)
            return value


class AppState:
    store: Store
    syncer: Syncer
    worker: SyncWorker
    tunnel: TunnelMonitor
    watchdog: Watchdog
    cache: PayloadCache


STATE = AppState()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    STATE.cache = PayloadCache()
    STATE.store = Store()
    STATE.syncer = Syncer(STATE.store)
    STATE.worker = SyncWorker(STATE.syncer, on_change=prewarm)
    STATE.tunnel = TunnelMonitor()
    STATE.watchdog = Watchdog(STATE.worker, SYNC_STALL_EXIT_SECONDS)
    log.info(
        "API starting: %s rows, latest %s, db %s",
        STATE.syncer.summary["rows_total"],
        STATE.syncer.summary["latest_ts"],
        STATE.store.path,
    )
    STATE.worker.start()
    STATE.tunnel.start()
    STATE.watchdog.start()
    try:
        yield
    finally:
        STATE.watchdog.stop()
        STATE.tunnel.stop()
        STATE.worker.stop()
        STATE.worker.join(timeout=30)
        STATE.store.close()


app = FastAPI(title="Cassini BlueFors API", version="2.0.0", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def require_key(x_api_key: Optional[str]) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def clean_value(v: Any):
    if v is None:
        return None

    if isinstance(v, dict):
        return {k: clean_value(val) for k, val in v.items()}

    if isinstance(v, (list, tuple)):
        return [clean_value(item) for item in v]

    try:
        if pd.isna(v):
            return None
    except Exception:
        pass

    if isinstance(v, (pd.Timestamp, datetime)):
        return v.isoformat()

    try:
        if hasattr(v, "item") and not isinstance(v, (str, bytes)):
            return clean_value(v.item())
    except Exception:
        pass

    if isinstance(v, Integral) and not isinstance(v, bool):
        return int(v)

    if isinstance(v, Real) and not isinstance(v, bool):
        fv = float(v)
        if math.isnan(fv) or math.isinf(fv):
            return None
        return fv

    return v


def clean_record(d: dict) -> dict:
    return {k: clean_value(v) for k, v in d.items()}


def json_response(payload: Any) -> Response:
    body = payload if isinstance(payload, bytes) else orjson.dumps(payload)
    return Response(content=body, media_type="application/json")


def maybe_downsample(df: pd.DataFrame, max_points: Optional[int]) -> pd.DataFrame:
    if max_points is None or df.empty or len(df) <= max_points:
        return df

    step = max(1, math.ceil(len(df) / max_points))
    sampled = df.iloc[::step].copy()

    if sampled.index[-1] != df.index[-1]:
        sampled = pd.concat([sampled, df.iloc[[-1]]])

    return sampled.sort_index(kind="stable").reset_index(drop=True)


def iso_strings(ts: pd.Series) -> list[str]:
    return ts.dt.strftime("%Y-%m-%dT%H:%M:%S").tolist()


def series_points(ts_strings: list[str], values: pd.Series, drop_missing: bool) -> list[dict[str, Any]]:
    floats = values.to_numpy(dtype="float64", na_value=np.nan).tolist()
    if drop_missing:
        return [{"ts_eastern": t, "value": v} for t, v in zip(ts_strings, floats) if not math.isnan(v)]
    return [{"ts_eastern": t, "value": None if math.isnan(v) else v} for t, v in zip(ts_strings, floats)]


def aggregate_counters(cur) -> dict[str, Any]:
    """Whole-history totals in a single ordered pass over readings."""
    df = cur.execute(
        """
        WITH d AS (
            SELECT
                T_MXC, scroll_1, scroll_2, turbo_1, pulse_tube,
                epoch(ts_eastern) - epoch(LAG(ts_eastern) OVER (ORDER BY ts_eastern)) AS dt_prev,
                epoch(LEAD(ts_eastern) OVER (ORDER BY ts_eastern)) - epoch(ts_eastern) AS dt_next
            FROM readings
        )
        SELECT
            SUM(CASE WHEN COALESCE(scroll_1, 0) >= 0.5 THEN COALESCE(dt_prev, 0) ELSE 0 END) / 3600.0 AS total_hours_scroll_1,
            SUM(CASE WHEN COALESCE(scroll_2, 0) >= 0.5 THEN COALESCE(dt_prev, 0) ELSE 0 END) / 3600.0 AS total_hours_scroll_2,
            SUM(CASE WHEN COALESCE(turbo_1, 0) >= 0.5 THEN COALESCE(dt_prev, 0) ELSE 0 END) / 3600.0 AS total_hours_turbo_1,
            SUM(CASE WHEN COALESCE(pulse_tube, 0) >= 0.5 THEN COALESCE(dt_prev, 0) ELSE 0 END) / 3600.0 AS total_hours_pulse_tube,
            SUM(CASE WHEN T_MXC < 0.02 THEN COALESCE(dt_next, 0) ELSE 0 END) / 3600.0 AS hours_below_20mK_total
        FROM d
        """
    ).fetchdf()
    if df.empty:
        return {}
    return df.iloc[0].to_dict()


def runtime_counters(counters: dict[str, Any]) -> dict[str, Any]:
    keys = ("total_hours_scroll_1", "total_hours_scroll_2", "total_hours_turbo_1", "total_hours_pulse_tube")
    return {key: counters.get(key) for key in keys}


def metrics_payload(cur, counters: dict[str, Any]) -> dict[str, Any]:
    latest_df = cur.execute(
        """
        SELECT
            ts_eastern,
            T_50K,
            T_4K,
            T_Still,
            T_MXC,
            P1, P2, P3, P4, P5, P6,
            Flow,
            total_hours_scroll_1,
            total_hours_scroll_2,
            total_hours_turbo_1,
            total_hours_pulse_tube,
            scroll_1,
            scroll_2,
            turbo_1,
            pulse_tube
        FROM readings
        ORDER BY ts_eastern DESC
        LIMIT 1
        """
    ).fetchdf()

    if latest_df.empty:
        return {}

    since_24h = datetime.now() - timedelta(hours=24)

    last24_df = cur.execute(
        """
        SELECT
            MIN(T_Still) AS T_Still_min_24h,
            MAX(T_Still) AS T_Still_max_24h,
            MIN(T_MXC) AS T_MXC_min_24h,
            MAX(T_MXC) AS T_MXC_max_24h,
            MIN(P1) AS P1_min_24h,
            MAX(P1) AS P1_max_24h,
            MIN(Flow) AS Flow_min_24h,
            MAX(Flow) AS Flow_max_24h
        FROM readings
        WHERE ts_eastern >= ?
        """,
        [since_24h],
    ).fetchdf()

    cold_streak_df = cur.execute(
        """
        WITH latest AS (
            SELECT ts_eastern, T_MXC
            FROM readings
            ORDER BY ts_eastern DESC
            LIMIT 1
        ),
        last_warm AS (
            SELECT MAX(ts_eastern) AS ts
            FROM readings
            WHERE T_MXC >= 0.02
        )
        SELECT
            CASE
                WHEN (SELECT T_MXC FROM latest) < 0.02 THEN (
                    epoch((SELECT ts_eastern FROM latest)) - epoch(
                        COALESCE(
                            (SELECT MIN(ts_eastern) FROM readings WHERE ts_eastern > (SELECT ts FROM last_warm)),
                            (SELECT MIN(ts_eastern) FROM readings)
                        )
                    )
                ) / 3600.0
                ELSE 0
            END AS hours_below_20mK_current
        """
    ).fetchdf()

    pump_starts_df = cur.execute(
        """
        WITH recent AS (
            SELECT
                ts_eastern,
                pulse_tube,
                turbo_1,
                scroll_1,
                scroll_2,
                LAG(pulse_tube) OVER (ORDER BY ts_eastern) AS prev_pulse_tube,
                LAG(turbo_1) OVER (ORDER BY ts_eastern) AS prev_turbo_1,
                LAG(scroll_1) OVER (ORDER BY ts_eastern) AS prev_scroll_1,
                LAG(scroll_2) OVER (ORDER BY ts_eastern) AS prev_scroll_2
            FROM readings
            WHERE ts_eastern >= ?
        )
        SELECT
            SUM(CASE WHEN COALESCE(prev_pulse_tube, 0) < 0.5 AND COALESCE(pulse_tube, 0) >= 0.5 THEN 1 ELSE 0 END) AS pulse_tube_starts_24h,
            SUM(CASE WHEN COALESCE(prev_turbo_1, 0) < 0.5 AND COALESCE(turbo_1, 0) >= 0.5 THEN 1 ELSE 0 END) AS turbo_1_starts_24h,
            SUM(CASE WHEN COALESCE(prev_scroll_1, 0) < 0.5 AND COALESCE(scroll_1, 0) >= 0.5 THEN 1 ELSE 0 END) AS scroll_1_starts_24h,
            SUM(CASE WHEN COALESCE(prev_scroll_2, 0) < 0.5 AND COALESCE(scroll_2, 0) >= 0.5 THEN 1 ELSE 0 END) AS scroll_2_starts_24h
        FROM recent
        """,
        [since_24h],
    ).fetchdf()

    out = latest_df.iloc[0].to_dict()
    if not last24_df.empty:
        out.update(last24_df.iloc[0].to_dict())
    out["hours_below_20mK_total"] = counters.get("hours_below_20mK_total")
    if not cold_streak_df.empty:
        out.update(cold_streak_df.iloc[0].to_dict())
    if not pump_starts_df.empty:
        out.update(pump_starts_df.iloc[0].to_dict())

    out.update(runtime_counters(counters))

    return clean_record(out)


def phase_events(cur) -> list[dict[str, Any]]:
    """Cooldown / warm-up starts over the full history, from pulse-tube switching."""
    rows = cur.execute(
        """
        WITH s AS (
            SELECT ts_eastern, CASE WHEN pulse_tube >= 0.5 THEN 1 ELSE 0 END AS on_
            FROM readings
            WHERE pulse_tube IS NOT NULL
        ),
        c AS (
            SELECT ts_eastern, on_, LAG(on_) OVER (ORDER BY ts_eastern) AS prev
            FROM s
        )
        SELECT ts_eastern, on_ FROM c WHERE prev IS NULL OR on_ <> prev ORDER BY ts_eastern
        """
    ).fetchall()
    if not rows:
        return []
    end_ts = cur.execute("SELECT MAX(ts_eastern) FROM readings WHERE pulse_tube IS NOT NULL").fetchone()[0]

    segments = [
        [start, rows[i + 1][0] if i + 1 < len(rows) else end_ts, bool(on)]
        for i, (start, on) in enumerate(rows)
    ]
    last = len(segments) - 1
    kept = [seg for i, seg in enumerate(segments) if i in (0, last) or seg[1] - seg[0] >= PHASE_MIN_DURATION]

    merged: list[list] = []
    for seg in kept:
        if merged and merged[-1][2] == seg[2]:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)

    events = []
    for start, _, on in merged[1:]:
        kind = "cooldown" if on else "warmup"
        events.append({"ts_eastern": start.isoformat(), "kind": kind, "label": PHASE_LABELS[kind]})
    return events


# --- cached builders -----------------------------------------------------

def _version() -> int:
    return STATE.syncer.version


def cached_counters() -> dict[str, Any]:
    def build():
        with STATE.store.cursor() as cur:
            return aggregate_counters(cur)

    return STATE.cache.get(("counters",), _version(), build)


def cached_metrics() -> dict[str, Any]:
    def build():
        with STATE.store.cursor() as cur:
            return metrics_payload(cur, cached_counters())

    return STATE.cache.get(("metrics",), _version(), build)


def build_dashboard(hours: int, max_points: int) -> bytes:
    start = datetime.now() - timedelta(hours=hours)
    with STATE.store.cursor() as cur:
        history_df = cur.execute(
            f"""
            SELECT ts_eastern, {", ".join(DASHBOARD_KEYS)}
            FROM readings
            WHERE ts_eastern >= ?
            ORDER BY ts_eastern
            """,
            [start],
        ).fetchdf()
    history_df = maybe_downsample(history_df, max_points)
    ts_strings = iso_strings(history_df["ts_eastern"]) if not history_df.empty else []

    return orjson.dumps(
        {
            "hours": hours,
            "max_points": max_points,
            "metrics": cached_metrics(),
            "histories": {
                key: series_points(ts_strings, history_df[key], drop_missing=True) if not history_df.empty else []
                for key in DASHBOARD_KEYS
            },
        }
    )


def cached_dashboard(hours: int, max_points: int) -> bytes:
    return STATE.cache.get(("dashboard", hours, max_points), _version(), lambda: build_dashboard(hours, max_points))


def cached_events() -> bytes:
    def build():
        with STATE.store.cursor() as cur:
            return orjson.dumps({"events": phase_events(cur)})

    return STATE.cache.get(("events",), _version(), build)


def prewarm() -> None:
    """Called by the sync worker after new data lands."""
    cached_dashboard(DEFAULT_DASHBOARD_HOURS, DEFAULT_DASHBOARD_MAX_POINTS)
    cached_events()


# --- routes --------------------------------------------------------------

@app.get("/")
def root():
    return {
        "name": "Cassini BlueFors API",
        "status": "ok",
        "health": "/health",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    summary = STATE.syncer.summary
    latest = summary.get("latest_ts")
    age = (datetime.now() - latest).total_seconds() if latest is not None else None
    payload = {
        "status": "ok",
        "rows": summary.get("rows_total"),
        "files": summary.get("days_tracked"),
        "latest_ts_eastern": latest,
        "data_age_seconds": None if age is None else round(age),
        "stale": age is None or age > STALE_DATA_SECONDS,
        "server_time": datetime.now(),
        "db_size_mb": round(STATE.store.size_bytes() / 1e6, 1),
        "sync": STATE.worker.status(),
        "tunnel": STATE.tunnel.status(),
    }
    return json_response(clean_value(payload))


@app.get("/latest")
def latest(x_api_key: Optional[str] = Header(default=None)):
    require_key(x_api_key)

    def build():
        with STATE.store.cursor() as cur:
            df = cur.execute(
                """
                SELECT *
                FROM readings
                ORDER BY ts_eastern DESC
                LIMIT 1
                """
            ).fetchdf()
        if df.empty:
            return orjson.dumps({})
        payload = df.iloc[0].to_dict()
        payload.update(runtime_counters(cached_counters()))
        return orjson.dumps(clean_record(payload))

    return json_response(STATE.cache.get(("latest",), _version(), build))


@app.get("/history/{key}")
def history(
    key: str,
    hours: int = Query(24, ge=1, le=24 * 365),
    max_points: Optional[int] = Query(default=None, ge=100, le=10000),
    x_api_key: Optional[str] = Header(default=None),
):
    require_key(x_api_key)
    if key not in ALLOWED_KEYS:
        raise HTTPException(status_code=400, detail=f"Invalid key: {key}")

    def build():
        start = datetime.now() - timedelta(hours=hours)
        with STATE.store.cursor() as cur:
            df = cur.execute(
                f"""
                SELECT ts_eastern, {key} AS value
                FROM readings
                WHERE ts_eastern >= ?
                ORDER BY ts_eastern
                """,
                [start],
            ).fetchdf()
        df = maybe_downsample(df, max_points)
        points = series_points(iso_strings(df["ts_eastern"]), df["value"], drop_missing=False) if not df.empty else []
        return orjson.dumps({"key": key, "hours": hours, "points": points})

    return json_response(STATE.cache.get(("history", key, hours, max_points), _version(), build))


@app.get("/metrics")
def metrics(x_api_key: Optional[str] = Header(default=None)):
    require_key(x_api_key)
    return json_response(cached_metrics())


@app.get("/events")
def events(x_api_key: Optional[str] = Header(default=None)):
    require_key(x_api_key)
    return json_response(cached_events())


@app.get("/dashboard")
def dashboard(
    hours: int = Query(24, ge=1, le=24 * 365),
    max_points: int = Query(DEFAULT_DASHBOARD_MAX_POINTS, ge=100, le=10000),
    x_api_key: Optional[str] = Header(default=None),
):
    require_key(x_api_key)
    return json_response(cached_dashboard(hours, max_points))
