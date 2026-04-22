from __future__ import annotations

import math
from datetime import datetime, timedelta
from numbers import Integral, Real
from typing import Any, Optional

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import ALLOWED_KEYS, API_KEY
from app.db import connect, read_target_status

app = FastAPI(title="Cassini BlueFors API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

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

DEFAULT_DASHBOARD_MAX_POINTS = 1200


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


def maybe_downsample(df: pd.DataFrame, max_points: Optional[int]) -> pd.DataFrame:
    if max_points is None or df.empty or len(df) <= max_points:
        return df

    step = max(1, math.ceil(len(df) / max_points))
    sampled = df.iloc[::step].copy()

    if sampled.index[-1] != df.index[-1]:
        sampled = pd.concat([sampled, df.iloc[[-1]]])

    return sampled.sort_index(kind="stable").reset_index(drop=True)


def to_history_records(df: pd.DataFrame, key: str) -> list[dict[str, Any]]:
    if key not in df:
        return []

    series_df = df[["ts_eastern", key]].rename(columns={key: "value"})
    series_df = series_df.dropna(subset=["value"])
    return [clean_record(r) for r in series_df.to_dict(orient="records")]


def metrics_payload(con) -> dict[str, Any]:
    latest_df = con.execute(
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

    last24_df = con.execute(
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
        WHERE ts_eastern >= NOW() - INTERVAL 24 HOUR
        """
    ).fetchdf()

    cold_totals_df = con.execute(
        """
        WITH intervals AS (
            SELECT
                ts_eastern,
                T_MXC,
                LEAD(ts_eastern) OVER (ORDER BY ts_eastern) AS next_ts
            FROM readings
        )
        SELECT
            SUM(
                CASE
                    WHEN T_MXC < 0.02 AND next_ts IS NOT NULL
                    THEN epoch(next_ts) - epoch(ts_eastern)
                    ELSE 0
                END
            ) / 3600.0 AS hours_below_20mK_total
        FROM intervals
        """
    ).fetchdf()

    cold_streak_df = con.execute(
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

    pump_starts_df = con.execute(
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
            WHERE ts_eastern >= NOW() - INTERVAL 24 HOUR
        )
        SELECT
            SUM(CASE WHEN COALESCE(prev_pulse_tube, 0) < 0.5 AND COALESCE(pulse_tube, 0) >= 0.5 THEN 1 ELSE 0 END) AS pulse_tube_starts_24h,
            SUM(CASE WHEN COALESCE(prev_turbo_1, 0) < 0.5 AND COALESCE(turbo_1, 0) >= 0.5 THEN 1 ELSE 0 END) AS turbo_1_starts_24h,
            SUM(CASE WHEN COALESCE(prev_scroll_1, 0) < 0.5 AND COALESCE(scroll_1, 0) >= 0.5 THEN 1 ELSE 0 END) AS scroll_1_starts_24h,
            SUM(CASE WHEN COALESCE(prev_scroll_2, 0) < 0.5 AND COALESCE(scroll_2, 0) >= 0.5 THEN 1 ELSE 0 END) AS scroll_2_starts_24h
        FROM recent
        """
    ).fetchdf()

    out = latest_df.iloc[0].to_dict()
    if not last24_df.empty:
        out.update(last24_df.iloc[0].to_dict())
    if not cold_totals_df.empty:
        out.update(cold_totals_df.iloc[0].to_dict())
    if not cold_streak_df.empty:
        out.update(cold_streak_df.iloc[0].to_dict())
    if not pump_starts_df.empty:
        out.update(pump_starts_df.iloc[0].to_dict())

    return clean_record(out)


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
    con = connect()
    try:
        total_rows = con.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        total_files = con.execute("SELECT COUNT(*) FROM ingested_files").fetchone()[0]
        latest = con.execute("SELECT MAX(ts_eastern) FROM readings").fetchone()[0]
        payload = {
            "status": "ok",
            "rows": total_rows,
            "files": total_files,
            "latest_ts_eastern": latest,
        }
        payload.update(read_target_status())
        return clean_record(payload)
    finally:
        con.close()


@app.get("/latest")
def latest(x_api_key: Optional[str] = Header(default=None)):
    require_key(x_api_key)
    con = connect()
    try:
        df = con.execute(
            """
            SELECT *
            FROM readings
            ORDER BY ts_eastern DESC
            LIMIT 1
            """
        ).fetchdf()
        if df.empty:
            return {}
        return clean_record(df.iloc[0].to_dict())
    finally:
        con.close()


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

    start = datetime.now() - timedelta(hours=hours)
    con = connect()
    try:
        df = con.execute(
            f"""
            SELECT ts_eastern, {key} AS value
            FROM readings
            WHERE ts_eastern >= ?
            ORDER BY ts_eastern
            """,
            [start],
        ).fetchdf()
        df = maybe_downsample(df, max_points)
        records = [clean_record(r) for r in df.to_dict(orient="records")]
        return {
            "key": key,
            "hours": hours,
            "points": records,
        }
    finally:
        con.close()


@app.get("/metrics")
def metrics(x_api_key: Optional[str] = Header(default=None)):
    require_key(x_api_key)
    con = connect()
    try:
        return metrics_payload(con)
    finally:
        con.close()


@app.get("/dashboard")
def dashboard(
    hours: int = Query(24, ge=1, le=24 * 365),
    max_points: int = Query(DEFAULT_DASHBOARD_MAX_POINTS, ge=100, le=10000),
    x_api_key: Optional[str] = Header(default=None),
):
    require_key(x_api_key)
    start = datetime.now() - timedelta(hours=hours)
    con = connect()
    try:
        history_df = con.execute(
            f"""
            SELECT ts_eastern, {", ".join(DASHBOARD_KEYS)}
            FROM readings
            WHERE ts_eastern >= ?
            ORDER BY ts_eastern
            """,
            [start],
        ).fetchdf()
        history_df = maybe_downsample(history_df, max_points)

        return {
            "hours": hours,
            "max_points": max_points,
            "metrics": metrics_payload(con),
            "histories": {key: to_history_records(history_df, key) for key in DASHBOARD_KEYS},
        }
    finally:
        con.close()
