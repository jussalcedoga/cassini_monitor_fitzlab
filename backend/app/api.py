from __future__ import annotations

from datetime import datetime, timedelta
from numbers import Integral, Real
from typing import Optional, Any

import math
import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import API_KEY, ALLOWED_KEYS
from app.db import connect

app = FastAPI(title="Cassini BlueFors API", version="1.0.0")

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

    # pandas / numpy missing values
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass

    # pandas Timestamp / datetime
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.isoformat()

    # numpy scalars
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
        return clean_record({
            "status": "ok",
            "rows": total_rows,
            "files": total_files,
            "latest_ts_eastern": latest,
        })
    finally:
        con.close()

@app.get("/latest")
def latest(x_api_key: Optional[str] = Header(default=None)):
    require_key(x_api_key)
    con = connect()
    try:
        df = con.execute("""
            SELECT *
            FROM readings
            ORDER BY ts_eastern DESC
            LIMIT 1
        """).fetchdf()
        if df.empty:
            return {}
        return clean_record(df.iloc[0].to_dict())
    finally:
        con.close()

@app.get("/history/{key}")
def history(
    key: str,
    hours: int = Query(24, ge=1, le=24 * 365),
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
            [start]
        ).fetchdf()

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
        latest_df = con.execute("""
            SELECT
                ts_eastern,
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
        """).fetchdf()

        if latest_df.empty:
            return {}

        latest_row = latest_df.iloc[0].to_dict()

        last24_df = con.execute("""
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
        """).fetchdf()

        out = {}
        out.update(latest_row)
        if not last24_df.empty:
            out.update(last24_df.iloc[0].to_dict())

        return clean_record(out)
    finally:
        con.close()
