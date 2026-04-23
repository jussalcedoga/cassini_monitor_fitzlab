from concurrent.futures import ThreadPoolExecutor, as_completed
import math
from pathlib import Path
import time
from typing import Dict, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

API_BASE = st.secrets["api_base"].rstrip("/")
API_KEY = st.secrets["api_key"]
DASHBOARD_PASSWORD = st.secrets["dashboard_password"]
REQUEST_TIMEOUT_SECONDS = 10
PLOT_MAX_POINTS = 1200
FETCH_RETRIES = 1
FETCH_RETRY_SLEEP_SECONDS = 0.35

st.set_page_config(
    page_title="Cassini BlueFors Dashboard",
    page_icon="🪐",
    layout="wide",
    initial_sidebar_state="expanded",
)

HEADERS = {"X-API-Key": API_KEY}
APP_DIR = Path(__file__).resolve().parent

DEFAULT_HOURS = 48
AVAILABLE_WINDOWS = [6, 12, 24, 48, 72]
AUTO_REFRESH_SECONDS = [0, 30, 60, 120, 300]

TEMPERATURE_KEYS = ["T_50K", "T_4K", "T_Still", "T_MXC"]
PRESSURE_KEYS = ["P1", "P2", "P3", "P4", "P5", "P6"]
FLOW_KEYS = ["Flow"]
STATE_KEYS = ["pulse_tube", "turbo_1", "scroll_1", "scroll_2"]
HISTORY_KEYS = TEMPERATURE_KEYS + PRESSURE_KEYS + FLOW_KEYS + STATE_KEYS
EM_STAGE_KEYS = ["T_50K", "T_4K", "T_Still", "T_MXC"]
EM_DEFAULT_ATTEN_DB = {
    "T_50K": 0.0,
    "T_4K": 20.0,
    "T_Still": 20.0,
    "T_MXC": 20.0,
}
EM_ROOM_TEMP_DEFAULT_K = 300.0
EM_DEFAULT_FREQ_GHZ = 5.0
K_B = 1.380649e-23
H = 6.62607015e-34
MXC_BASE_TARGET_K = 0.010
MXC_BASE_BAND_K = 0.005
MXC_SENSOR_FLOOR_TRIGGER_K = 0.009
MXC_SENSOR_FLOOR_LOOKBACK_H = 3.0
MXC_BASE_TREND_TOL_KPH = 0.004
MXC_COLD_TREND_KPH = 0.006
MXC_WARM_TREND_KPH = 0.006

PRETTY_NAMES = {
    "T_50K": "50 K Stage",
    "T_4K": "4 K Stage",
    "T_Still": "Still",
    "T_MXC": "Mixing Chamber",
    "P1": "Pressure P1",
    "P2": "Pressure P2",
    "P3": "Pressure P3",
    "P4": "Pressure P4",
    "P5": "Pressure P5",
    "P6": "Pressure P6",
    "Flow": "Flow",
    "pulse_tube": "Pulse Tube",
    "turbo_1": "Turbo",
    "scroll_1": "Scroll 1",
    "scroll_2": "Scroll 2",
    "total_hours_pulse_tube": "Pulse Tube Hours",
    "total_hours_turbo_1": "Turbo Hours",
    "total_hours_scroll_1": "Scroll 1 Hours",
    "total_hours_scroll_2": "Scroll 2 Hours",
}

PRESSURE_UNIT = "mbar"

COLORS = {
    "T_50K": "#FE2A2A",
    "T_4K": "#54D400",
    "T_Still": "#FECB00",
    "T_MXC": "#0065FF",
    "P1": "#8b5cf6",
    "P2": "#ec4899",
    "P3": "#f97316",
    "P4": "#eab308",
    "P5": "#14b8a6",
    "P6": "#6366f1",
    "Flow": "#06b6d4",
    "pulse_tube": "#22c55e",
    "turbo_1": "#3b82f6",
    "scroll_1": "#a855f7",
    "scroll_2": "#f43f5e",
}

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 2.4rem;
        padding-bottom: 1.25rem;
        max-width: 1480px;
    }
    .metric-card {
        background: linear-gradient(180deg, rgba(15,23,42,0.98), rgba(15,23,42,0.9));
        border: 1px solid rgba(148,163,184,0.16);
        border-radius: 18px;
        padding: 1rem 1rem 0.9rem 1rem;
        min-height: 115px;
    }
    .metric-card--base {
        border-color: rgba(34, 197, 94, 0.34);
        box-shadow: inset 0 0 0 1px rgba(34, 197, 94, 0.10);
    }
    .metric-card--base .metric-value {
        color: #dcfce7;
    }
    .metric-card--cooling {
        border-color: rgba(59, 130, 246, 0.34);
        box-shadow: inset 0 0 0 1px rgba(59, 130, 246, 0.10);
    }
    .metric-card--cooling .metric-value {
        color: #dbeafe;
    }
    .metric-card--warming {
        border-color: rgba(249, 115, 22, 0.34);
        box-shadow: inset 0 0 0 1px rgba(249, 115, 22, 0.10);
    }
    .metric-card--warming .metric-value {
        color: #fed7aa;
    }
    .metric-card--sensor-floor {
        background: linear-gradient(180deg, rgba(8,47,73,0.98), rgba(15,23,42,0.94));
        border-color: rgba(125, 211, 252, 0.42);
        box-shadow: inset 0 0 0 1px rgba(125, 211, 252, 0.14);
    }
    .metric-card--sensor-floor .metric-value {
        color: #7dd3fc;
    }
    .metric-title {
        color: #94a3b8;
        font-size: 0.92rem;
        margin-bottom: 0.35rem;
    }
    .metric-value {
        color: white;
        font-size: 1.8rem;
        font-weight: 700;
        line-height: 1.1;
    }
    .metric-help {
        color: #64748b;
        font-size: 0.82rem;
        margin-top: 0.4rem;
    }
    .section-caption {
        color: #94a3b8;
        font-size: 0.97rem;
        margin-top: -0.2rem;
        margin-bottom: 0.85rem;
    }
    .status-chip {
        display: inline-block;
        padding: 0.30rem 0.72rem;
        border-radius: 999px;
        font-size: 0.88rem;
        font-weight: 600;
        margin-right: 0.42rem;
        margin-bottom: 0.35rem;
    }
    .login-spacer {
        height: 4.8rem;
    }
    .login-kicker {
        color: #7dd3fc;
        font-size: 0.86rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: 0.75rem;
    }
    .login-title {
        color: #0f172a;
        font-size: clamp(2.35rem, 4.6vw, 3.75rem);
        font-weight: 800;
        line-height: 1.05;
        margin-bottom: 0.6rem;
    }
    .login-copy {
        color: var(--st-text-color, var(--text-color, #475569));
        opacity: 0.82;
        font-size: 1rem;
        line-height: 1.55;
        margin-bottom: 0.95rem;
    }
    .login-meta {
        color: #94a3b8;
        font-size: 0.92rem;
        margin-top: 0.7rem;
    }
    .hero-kicker {
        color: #7dd3fc;
        font-size: 0.84rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: 0.25rem;
    }
    .hero-copy {
        color: var(--st-text-color, var(--text-color, #475569));
        opacity: 0.82;
        font-size: 1rem;
        line-height: 1.5;
        margin-top: 0.15rem;
    }
    .hero-title {
        color: #0f172a;
        font-size: clamp(2.5rem, 5vw, 4.25rem);
        font-weight: 800;
        line-height: 1.08;
        margin: 0 0 0.65rem 0;
        letter-spacing: -0.03em;
    }
    .hero-meta {
        color: #64748b;
        font-size: 0.88rem;
        margin-top: 0.42rem;
    }
    .hero-spacer {
        height: 0.85rem;
    }
    .hero-top-spacer {
        height: 1.4rem;
    }
    .refresh-note {
        color: #94a3b8;
        font-size: 0.92rem;
        margin-bottom: 0.85rem;
    }
    html[data-cassini-theme="dark"] .login-title,
    html[data-cassini-theme="dark"] .hero-title {
        color: #f8fafc !important;
    }
    html[data-cassini-theme="dark"] .login-copy,
    html[data-cassini-theme="dark"] .hero-copy {
        color: #cbd5e1 !important;
        opacity: 0.9;
    }
    html[data-cassini-theme="light"] .login-title,
    html[data-cassini-theme="light"] .hero-title {
        color: #0f172a !important;
    }
    html[data-cassini-theme="light"] .login-copy,
    html[data-cassini-theme="light"] .hero-copy {
        color: #475569 !important;
        opacity: 0.82;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if "ok" not in st.session_state:
    st.session_state.ok = False


def mount_theme_bridge() -> None:
    components.html(
        """
        <script>
        const applyCassiniTheme = () => {
          const parentDoc = window.parent.document;
          const app = parentDoc.querySelector('.stApp');
          if (!app) return;
          const scheme = window.parent.getComputedStyle(app).getPropertyValue('color-scheme').trim() || 'light';
          parentDoc.documentElement.setAttribute('data-cassini-theme', scheme);
        };
        applyCassiniTheme();
        const observer = new MutationObserver(applyCassiniTheme);
        observer.observe(window.parent.document.body, { childList: true, subtree: true, attributes: true });
        window.addEventListener('beforeunload', () => observer.disconnect(), { once: true });
        </script>
        """,
        height=0,
    )


mount_theme_bridge()


def asset_path(name: str) -> Path:
    for candidate in (APP_DIR / name, APP_DIR / "frontend" / name):
        if candidate.exists():
            return candidate
    return APP_DIR / name


def safe_image(path: Path):
    try:
        return Image.open(path)
    except Exception:
        return None


def format_refresh_label(seconds: int) -> str:
    if seconds == 0:
        return "Off"
    if seconds < 60:
        return f"Every {seconds} s"
    return f"Every {seconds // 60} min"


def render_login_page():
    logo = safe_image(asset_path("logo.png"))
    cassini = safe_image(asset_path("cassini.png"))

    c1, c2, c3 = st.columns([0.72, 1.56, 0.72])
    with c2:
        st.markdown('<div class="login-spacer"></div>', unsafe_allow_html=True)

        image_cols = st.columns([0.4, 0.6])
        with image_cols[0]:
            if logo is not None:
                st.image(logo, width=180)
        with image_cols[1]:
            if cassini is not None:
                st.image(cassini, width=230)

        st.markdown('<div class="login-kicker">FitzLab • Dartmouth Engineering</div>', unsafe_allow_html=True)
        st.markdown('<div class="login-title">Cassini BlueFors Dashboard</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="login-copy">Secure access to live BlueFors temperatures, pressures, pump states, and cooldown history.</div>',
            unsafe_allow_html=True,
        )

        pwd = st.text_input("Password", type="password")
        if st.button("Enter"):
            if pwd == DASHBOARD_PASSWORD:
                st.session_state.ok = True
                st.rerun()
            else:
                st.error("Wrong password")

        st.markdown(
            "[FitzLab Website](https://sites.google.com/view/fitzlab/home)  |  [Main Designer: Juan Salcedo](https://www.linkedin.com/in/jussalcedoga/)"
        )
        st.markdown(
            '<div class="login-meta">Built for FitzLab at Dartmouth. Once you are in, the dashboard refreshes in place so you can follow the latest point without logging in again.</div>',
            unsafe_allow_html=True,
        )


def empty_history_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["ts_eastern", "value"])


def fetch_json(path: str, params: Optional[dict] = None):
    last_error = None
    for attempt in range(FETCH_RETRIES + 1):
        try:
            response = requests.get(
                f"{API_BASE}{path}",
                headers=HEADERS,
                params=params or {},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= FETCH_RETRIES:
                raise
            time.sleep(FETCH_RETRY_SLEEP_SECONDS * (attempt + 1))
    raise last_error


def downsample_df(df: pd.DataFrame, max_points: int = PLOT_MAX_POINTS) -> pd.DataFrame:
    if df.empty or len(df) <= max_points:
        return df

    step = max(1, math.ceil(len(df) / max_points))
    sampled = df.iloc[::step].copy()
    if sampled.index[-1] != df.index[-1]:
        sampled = pd.concat([sampled, df.iloc[[-1]]])
    return sampled.sort_index(kind="stable").reset_index(drop=True)


def history_payload_to_df(points) -> pd.DataFrame:
    df = pd.DataFrame(points or [])
    if df.empty:
        return empty_history_df()

    df["ts_eastern"] = pd.to_datetime(df["ts_eastern"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["ts_eastern"]).sort_values("ts_eastern").copy()
    return downsample_df(df)


def is_missing(v) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except Exception:
        return False


def coalesce_value(*values):
    for value in values:
        if not is_missing(value):
            return value
    return None


def latest_history_value(df: pd.DataFrame):
    value, _ = latest_valid_history_point(df)
    return value


def latest_valid_history_point(df: pd.DataFrame):
    if df.empty or "value" not in df:
        return None, pd.NaT

    history = df.copy()
    history["ts_eastern"] = pd.to_datetime(history["ts_eastern"], errors="coerce")
    history["value"] = pd.to_numeric(history["value"], errors="coerce")
    history = history.dropna(subset=["ts_eastern", "value"]).sort_values("ts_eastern")
    if history.empty:
        return None, pd.NaT

    last_row = history.iloc[-1]
    return float(last_row["value"]), last_row["ts_eastern"]


def latest_history_timestamp(df: pd.DataFrame):
    if df.empty or "ts_eastern" not in df:
        return None
    ts = pd.to_datetime(df["ts_eastern"], errors="coerce").dropna()
    if ts.empty:
        return None
    return ts.iloc[-1].isoformat()


def latest_timestamp_from_histories(histories: Dict[str, pd.DataFrame]):
    timestamps = []
    for df in histories.values():
        ts = latest_history_timestamp(df)
        if ts is not None:
            timestamps.append(ts)
    if not timestamps:
        return None
    return max(timestamps)


def freshest_value(latest: dict, histories: Dict[str, pd.DataFrame], key: str):
    snapshot_value = latest.get(key)
    snapshot_ts = pd.to_datetime(latest.get("ts_eastern"), errors="coerce")

    history_df = histories.get(key, empty_history_df())
    history_value, history_ts = latest_valid_history_point(history_df)
    history_ts = pd.to_datetime(history_ts, errors="coerce")

    if not is_missing(history_value) and (pd.isna(snapshot_ts) or (not pd.isna(history_ts) and history_ts >= snapshot_ts)):
        return history_value

    return coalesce_value(snapshot_value, history_value)


def merge_records(primary: dict, fallback: dict) -> dict:
    merged = dict(fallback or {})
    for key, value in (primary or {}).items():
        if not is_missing(value):
            merged[key] = value
    return merged


def merge_histories(primary: Dict[str, pd.DataFrame], fallback: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    merged: Dict[str, pd.DataFrame] = {}
    all_keys = set(fallback or {}).union(primary or {})
    for key in all_keys:
        current_df = (primary or {}).get(key)
        fallback_df = (fallback or {}).get(key)
        merged[key] = current_df if current_df is not None and not current_df.empty else (fallback_df if fallback_df is not None else empty_history_df())
    return merged


def synthesize_latest_snapshot(latest: dict, metrics: dict, histories: Dict[str, pd.DataFrame], health: Optional[dict] = None) -> dict:
    snapshot = merge_records(latest, metrics)

    for key in HISTORY_KEYS:
        snapshot[key] = coalesce_value(
            snapshot.get(key),
            latest_history_value(histories.get(key, empty_history_df())),
        )

    snapshot["ts_eastern"] = coalesce_value(
        snapshot.get("ts_eastern"),
        latest_timestamp_from_histories(histories),
        (health or {}).get("latest_ts_eastern"),
    )
    return snapshot


def build_stage_temperature_history(temp_histories: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    merged_df = None

    for key in EM_STAGE_KEYS:
        source_df = temp_histories.get(key, empty_history_df())
        if source_df is None or source_df.empty:
            continue

        stage_df = source_df[["ts_eastern", "value"]].copy()
        stage_df["ts_eastern"] = pd.to_datetime(stage_df["ts_eastern"], errors="coerce")
        stage_df["value"] = pd.to_numeric(stage_df["value"], errors="coerce")
        stage_df = stage_df.dropna(subset=["ts_eastern"]).copy()
        if stage_df.empty:
            continue

        stage_df["ts_eastern"] = stage_df["ts_eastern"].dt.floor("min")
        stage_df = stage_df.sort_values("ts_eastern").drop_duplicates(subset=["ts_eastern"], keep="last")
        stage_df = stage_df.rename(columns={"value": key})[["ts_eastern", key]]

        if merged_df is None:
            merged_df = stage_df
        else:
            merged_df = merged_df.merge(stage_df, on="ts_eastern", how="outer")

    if merged_df is None or merged_df.empty:
        return pd.DataFrame(columns=["ts_eastern", *EM_STAGE_KEYS])

    for key in EM_STAGE_KEYS:
        if key not in merged_df:
            merged_df[key] = np.nan

    merged_df = merged_df.sort_values("ts_eastern").reset_index(drop=True)
    return merged_df[["ts_eastern", *EM_STAGE_KEYS]]


def compute_em_history(temp_histories: Dict[str, pd.DataFrame], latest: dict, stage_attens_db: Dict[str, float], freq_ghz: float, room_temp_k: float = EM_ROOM_TEMP_DEFAULT_K) -> Dict[str, pd.DataFrame]:
    temperature_history = build_stage_temperature_history(temp_histories)
    if temperature_history.empty:
        return {}

    stage_temps = {key: temperature_history[key].to_numpy(dtype=float) for key in EM_STAGE_KEYS}
    _, teff_map = compute_em_chain(stage_temps, stage_attens_db, freq_ghz, room_temp_k=room_temp_k)

    return {
        key: pd.DataFrame(
            {
                "ts_eastern": temperature_history["ts_eastern"],
                "value": np.asarray(teff_map[key], dtype=float),
            }
        )
        for key in EM_STAGE_KEYS
    }


def recent_temperature_slope(df: pd.DataFrame, lookback_hours: int = 3) -> Optional[float]:
    if df.empty or len(df) < 2:
        return None

    window_end = pd.to_datetime(df["ts_eastern"], errors="coerce").max()
    if pd.isna(window_end):
        return None

    window_start = window_end - pd.Timedelta(hours=lookback_hours)
    recent = df[df["ts_eastern"] >= window_start].copy()
    if len(recent) < 2:
        recent = df.tail(min(len(df), 12)).copy()
    if len(recent) < 2:
        return None

    recent["ts_eastern"] = pd.to_datetime(recent["ts_eastern"], errors="coerce")
    recent["value"] = pd.to_numeric(recent["value"], errors="coerce")
    recent = recent.dropna(subset=["ts_eastern", "value"])
    if len(recent) < 2:
        return None

    dt_hours = (recent["ts_eastern"].iloc[-1] - recent["ts_eastern"].iloc[0]).total_seconds() / 3600.0
    if dt_hours <= 0:
        return None

    return float((recent["value"].iloc[-1] - recent["value"].iloc[0]) / dt_hours)


def fridge_state(latest: dict, histories: Dict[str, pd.DataFrame]) -> tuple[str, str, str]:
    mxc_history = histories.get("T_MXC", empty_history_df())
    mxc = freshest_value(latest, histories, "T_MXC")
    still = freshest_value(latest, histories, "T_Still")
    pulse_tube = freshest_value(latest, histories, "pulse_tube")
    turbo = freshest_value(latest, histories, "turbo_1")
    scroll_1 = freshest_value(latest, histories, "scroll_1")
    scroll_2 = freshest_value(latest, histories, "scroll_2")
    slope = recent_temperature_slope(mxc_history, lookback_hours=3)
    last_valid_mxc, last_valid_mxc_ts = latest_valid_history_point(mxc_history)

    snapshot_ts = pd.to_datetime(latest.get("ts_eastern"), errors="coerce")
    history_ts = pd.to_datetime(latest_timestamp_from_histories(histories), errors="coerce")
    valid_timestamps = [ts for ts in (snapshot_ts, history_ts) if not pd.isna(ts)]
    current_ts = max(valid_timestamps) if valid_timestamps else pd.NaT

    still_value = None if is_missing(still) else float(still)
    pumps_on = any(
        not is_missing(value) and float(value) >= 0.5
        for value in (pulse_tube, turbo, scroll_1, scroll_2)
    )

    if is_missing(mxc):
        if not is_missing(last_valid_mxc) and not pd.isna(last_valid_mxc_ts) and not pd.isna(current_ts):
            age_h = max(0.0, (current_ts - last_valid_mxc_ts).total_seconds() / 3600.0)
            if age_h <= MXC_SENSOR_FLOOR_LOOKBACK_H and float(last_valid_mxc) <= MXC_SENSOR_FLOOR_TRIGGER_K and pumps_on:
                return (
                    "Below sensor range",
                    f"Last valid MXC point was {fmt_temp(last_valid_mxc, always_mk=True)}; the fridge appears colder than the readable sensor floor.",
                    "sensor-floor",
                )
        return "Unknown", "MXC reading unavailable.", "default"

    mxc = float(mxc)
    warm_threshold = MXC_WARM_TREND_KPH if mxc < 0.05 else 0.01
    cool_threshold = MXC_COLD_TREND_KPH if mxc < 0.05 else 0.01
    in_base_band = mxc <= MXC_BASE_TARGET_K or (
        mxc <= MXC_BASE_TARGET_K + MXC_BASE_BAND_K and (slope is None or abs(slope) <= MXC_BASE_TREND_TOL_KPH)
    )

    if slope is not None and slope >= warm_threshold:
        return "Warming up", "MXC is trending warmer.", "warming"

    if in_base_band:
        return "At base", f"MXC is at {fmt_temp(mxc, always_mk=True)} and within the base-temperature band.", "base"

    if slope is not None and slope <= -cool_threshold:
        return "Cooling down", "MXC is still trending colder.", "cooling"

    if not pumps_on and (mxc >= 0.02 or (still_value is not None and still_value > 1.0)):
        return "Warming up", "Cooling hardware is not fully engaged.", "warming"

    if pumps_on and mxc >= 0.02:
        return "Cooling down", "Cooling hardware is active above base temperature.", "cooling"

    if mxc < 0.02:
        return "At base", "Cryostat is holding near its base temperature.", "base"

    return "Cooling down", "Cryostat is settling toward a colder state.", "cooling"


@st.cache_data(show_spinner=False, ttl=20)
def load_dashboard(hours: int):
    health = {}
    try:
        health = fetch_json("/health")
    except requests.RequestException:
        pass

    try:
        payload = fetch_json("/dashboard", params={"hours": hours, "max_points": PLOT_MAX_POINTS})
        if isinstance(payload, dict) and "metrics" in payload and "histories" in payload:
            metrics = payload.get("metrics", {}) or {}
            histories = {
                key: history_payload_to_df(points)
                for key, points in payload.get("histories", {}).items()
            }
            return metrics, metrics, histories, health
    except requests.RequestException:
        pass

    latest = {}
    metrics = {}
    histories = {key: empty_history_df() for key in HISTORY_KEYS}

    with ThreadPoolExecutor(max_workers=min(6, len(HISTORY_KEYS) + 3)) as pool:
        futures = {
            pool.submit(fetch_json, "/latest"): ("latest", None),
            pool.submit(fetch_json, "/metrics"): ("metrics", None),
        }
        for key in HISTORY_KEYS:
            futures[pool.submit(fetch_json, f"/history/{key}", {"hours": hours, "max_points": PLOT_MAX_POINTS})] = ("history", key)

        for future in as_completed(futures):
            kind, key = futures[future]
            try:
                payload = future.result()
            except requests.RequestException:
                continue
            if kind == "latest":
                latest = payload or {}
            elif kind == "metrics":
                metrics = payload or {}
            else:
                histories[key] = history_payload_to_df(payload.get("points", []))

    return latest, metrics, histories, health


def fmt_temp(v: Optional[float], always_mk: bool = False) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if always_mk and abs(v) < 1.0:
        return f"{v * 1e3:,.1f} mK"
    if abs(v) < 0.1:
        return f"{v * 1e3:.1f} mK"
    if abs(v) < 10:
        return f"{v:.3f} K"
    return f"{v:.2f} K"


def fmt_pressure(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if v == 0:
        return f"0 {PRESSURE_UNIT}"
    if abs(v) < 1e-2 or abs(v) >= 1e3:
        return f"{v:.2e} {PRESSURE_UNIT}"
    if abs(v) < 1:
        return f"{v:.4f} {PRESSURE_UNIT}"
    if abs(v) < 100:
        return f"{v:.3f} {PRESSURE_UNIT}"
    return f"{v:.1f} {PRESSURE_UNIT}"


def fmt_flow(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):.4f}"


def fmt_em_temp(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if abs(v) < 0.5:
        return f"{v * 1e3:,.1f} mK"
    if abs(v) < 10:
        return f"{v:.3f} K"
    return f"{v:.2f} K"


def fmt_hours(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):,.1f} h"


def fmt_percent(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):.1f}%"


def fmt_count(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    return str(int(round(float(v))))


def fmt_state(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "Unknown"
    return "On" if float(v) >= 0.5 else "Off"


def chip_color(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "#64748b"
    return "#16a34a" if float(v) >= 0.5 else "#dc2626"


def thermal_n(temp_k, freq_ghz: float):
    temp = np.asarray(temp_k, dtype=float)
    invalid = ~np.isfinite(temp) | (temp <= 0)
    safe_temp = np.where(invalid, np.nan, np.maximum(temp, 1e-9))
    freq_hz = float(freq_ghz) * 1e9
    exponent = (H * freq_hz) / (K_B * safe_temp)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        n = 1.0 / np.expm1(exponent)
    return np.where(invalid, np.nan, n)


def n_to_teff(n, freq_ghz: float):
    occupation = np.asarray(n, dtype=float)
    invalid = ~np.isfinite(occupation) | (occupation < 0)
    safe_occupation = np.where(invalid, np.nan, np.maximum(occupation, 1e-20))
    freq_hz = float(freq_ghz) * 1e9
    hf_over_k = (H * freq_hz) / K_B
    with np.errstate(divide="ignore", invalid="ignore"):
        temp = hf_over_k / np.log1p(1.0 / safe_occupation)
    return np.where(invalid, np.nan, temp)


def compute_em_chain(stage_temps: Dict[str, object], stage_attens_db: Dict[str, float], freq_ghz: float, room_temp_k: float = EM_ROOM_TEMP_DEFAULT_K):
    n_in = thermal_n(room_temp_k, freq_ghz)
    n_eff: Dict[str, object] = {}
    t_eff: Dict[str, object] = {}

    for key in EM_STAGE_KEYS:
        atten_db = float(stage_attens_db[key])
        loss = 10 ** (atten_db / 10.0)
        stage_temp = np.asarray(stage_temps[key], dtype=float)
        stage_occupation = thermal_n(stage_temp, freq_ghz)

        if np.isclose(loss, 1.0):
            n_out = np.array(n_in, copy=True)
        else:
            n_out = n_in / loss + (1.0 - 1.0 / loss) * stage_occupation

        n_eff[key] = n_out
        t_eff[key] = n_to_teff(n_out, freq_ghz)
        n_in = n_out

    return n_eff, t_eff


def render_metric_box(title: str, value: str, help_text: str = "", tone: str = "default"):
    allowed_tones = {"default", "base", "cooling", "warming", "sensor-floor"}
    tone_class = f" metric-card--{tone}" if tone in allowed_tones and tone != "default" else ""
    st.markdown(
        f"""
        <div class="metric-card{tone_class}">
            <div class="metric-title">{title}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-help">{help_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_chip(label: str, value: Optional[float]) -> str:
    return f'<span class="status-chip" style="background:{chip_color(value)}; color:white;">{label}: {fmt_state(value)}</span>'


def duty_cycle_percent(df: pd.DataFrame) -> Optional[float]:
    if df.empty:
        return None
    values = pd.to_numeric(df["value"], errors="coerce").dropna()
    if values.empty:
        return None
    return float((values >= 0.5).mean() * 100.0)


def count_state_starts(df: pd.DataFrame, lookback_hours: int = 24) -> Optional[int]:
    if df.empty:
        return None

    recent = df.copy()
    recent["ts_eastern"] = pd.to_datetime(recent["ts_eastern"], errors="coerce")
    recent["value"] = pd.to_numeric(recent["value"], errors="coerce")
    recent = recent.dropna(subset=["ts_eastern", "value"]).sort_values("ts_eastern")
    if recent.empty:
        return None

    cutoff = recent["ts_eastern"].max() - pd.Timedelta(hours=lookback_hours)
    recent = recent[recent["ts_eastern"] >= cutoff].copy()
    if len(recent) < 2:
        return 0

    prev = recent["value"].shift(1).fillna(0.0)
    starts = ((prev < 0.5) & (recent["value"] >= 0.5)).sum()
    return int(starts)


def time_below_threshold_hours(df: pd.DataFrame, threshold: float) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    vals = df["value"].astype(float).to_numpy()
    ts = pd.to_datetime(df["ts_eastern"]).to_numpy()
    total_h = 0.0
    for i in range(len(df) - 1):
        if vals[i] < threshold:
            dt_h = (ts[i + 1] - ts[i]) / np.timedelta64(1, "h")
            if pd.notna(dt_h) and float(dt_h) >= 0:
                total_h += float(dt_h)
    return total_h


def make_multi_trace_figure(
    series: Dict[str, pd.DataFrame],
    title: str,
    yaxis_title: str,
    log_y: bool = False,
    height: int = 500,
) -> go.Figure:
    fig = go.Figure()

    for key, df in series.items():
        if df.empty:
            continue

        line_width = 4.9 if str(key).startswith("P") else 4.4
        value_suffix = ""
        if key in PRESSURE_KEYS:
            value_suffix = f" {PRESSURE_UNIT}"
        elif key in TEMPERATURE_KEYS:
            value_suffix = " K"
        fig.add_trace(
            go.Scattergl(
                x=df["ts_eastern"],
                y=df["value"],
                mode="lines",
                name=PRETTY_NAMES.get(key, key),
                line=dict(color=COLORS.get(key), width=line_width),
                hovertemplate=f"%{{x|%Y-%m-%d %H:%M}}<br>%{{y}}{value_suffix}<extra>%{{fullData.name}}</extra>",
            )
        )

    fig.update_layout(
        title=dict(
            text=title,
            x=0.02,
            xanchor="left",
            y=0.98,
            yanchor="top",
            font=dict(size=22, color="black"),
        ),
        template="none",
        paper_bgcolor="white",
        plot_bgcolor="white",
        hovermode="x unified",
        autosize=True,
        height=height,
        margin=dict(l=78, r=42, t=145, b=92),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.12,
            xanchor="left",
            x=0,
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=15, color="black"),
            itemwidth=84,
            tracegroupgap=10,
        ),
        font=dict(color="black"),
        xaxis=dict(
            title=dict(text="Time", standoff=22, font=dict(size=18, color="black")),
            tickformat="%b %d\n%H:%M",
            tickfont=dict(size=15, color="black"),
            showgrid=False,
            zeroline=False,
            showline=True,
            linewidth=1.4,
            linecolor="black",
            ticks="outside",
            ticklen=7,
            tickwidth=1.4,
            tickcolor="black",
            automargin=True,
        ),
        yaxis=dict(
            title=dict(text=yaxis_title, standoff=22, font=dict(size=18, color="black")),
            tickfont=dict(size=15, color="black"),
            showgrid=False,
            zeroline=False,
            showline=True,
            linewidth=1.4,
            linecolor="black",
            ticks="outside",
            ticklen=7,
            tickwidth=1.4,
            tickcolor="black",
            automargin=True,
        ),
    )

    if log_y:
        fig.update_yaxes(type="log")

    return fig


if not st.session_state.ok:
    render_login_page()
    st.stop()


with st.sidebar:
    logo = safe_image(asset_path("logo.png"))
    cassini = safe_image(asset_path("cassini.png"))

    if logo is not None:
        st.image(logo)
    st.markdown("## Cassini")
    st.caption("BlueFors live monitor")

    if cassini is not None:
        st.image(cassini)

    hours = st.selectbox(
        "Time window",
        AVAILABLE_WINDOWS,
        index=AVAILABLE_WINDOWS.index(DEFAULT_HOURS),
        help="Used for all plots. Default is the last two days.",
    )

    auto_refresh_seconds = st.selectbox(
        "Auto-refresh",
        AUTO_REFRESH_SECONDS,
        index=AUTO_REFRESH_SECONDS.index(60),
        format_func=format_refresh_label,
        help="Refreshes the live data in place while this tab stays open, so you can keep watching the latest point without logging in again.",
    )

    st.markdown("---")
    st.caption("FitzLab • Dartmouth Engineering")
    st.markdown("### API endpoint")
    st.code(API_BASE, language=None)
    st.markdown("[FitzLab Website](https://sites.google.com/view/fitzlab/home)")
    st.markdown("[Main Designer: Juan Salcedo](https://www.linkedin.com/in/jussalcedoga/)")

def render_dashboard_page():
    logo = safe_image(asset_path("logo.png"))
    cassini = safe_image(asset_path("cassini.png"))

    api_ok = True
    api_error = None
    latest = {}
    metrics = {}
    histories = {}
    health = {}
    stale_snapshot = False

    try:
        with st.spinner("Loading live cryostat data..."):
            latest, metrics, histories, health = load_dashboard(hours)

        cached_dashboard = st.session_state.get("last_good_dashboard")
        if cached_dashboard is not None:
            if len(cached_dashboard) == 4:
                cached_latest, cached_metrics, cached_histories, cached_health = cached_dashboard
            else:
                cached_latest, cached_metrics, cached_histories = cached_dashboard
                cached_health = {}
            latest = merge_records(latest, cached_latest)
            metrics = merge_records(metrics, cached_metrics)
            histories = merge_histories(histories, cached_histories)
            health = merge_records(health, cached_health)

        latest = synthesize_latest_snapshot(latest, metrics, histories, health)
        if all(is_missing(latest.get(key)) for key in HISTORY_KEYS):
            raise RuntimeError("No live telemetry values were returned by the API.")
        st.session_state["last_good_dashboard"] = (latest, metrics, histories, health)
    except Exception as exc:
        cached_dashboard = st.session_state.get("last_good_dashboard")
        if cached_dashboard is not None:
            if len(cached_dashboard) == 4:
                latest, metrics, histories, health = cached_dashboard
            else:
                latest, metrics, histories = cached_dashboard
                health = {}
            stale_snapshot = True
            api_error = str(exc)
        else:
            api_ok = False
            api_error = str(exc)

    st.markdown('<div class="hero-top-spacer"></div>', unsafe_allow_html=True)

    hero_cols = st.columns([0.18, 0.58, 0.24])
    with hero_cols[0]:
        if logo is not None:
            st.image(logo, width=150)
    with hero_cols[1]:
        st.markdown('<div class="hero-kicker">FitzLab • Dartmouth Engineering</div>', unsafe_allow_html=True)
        st.markdown('<div class="hero-title">Cassini BlueFors Dashboard</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="hero-copy">Live BlueFors temperatures, pressures, pump states, and cooldown history for Cassini.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="hero-meta">Auto-refresh: {format_refresh_label(auto_refresh_seconds)}</div>',
            unsafe_allow_html=True,
        )
    with hero_cols[2]:
        if cassini is not None:
            st.image(cassini, use_container_width=True)

    st.markdown('<div class="hero-spacer"></div>', unsafe_allow_html=True)

    if not api_ok:
        st.error("Could not load data from the API.")
        st.code(api_error)
        st.info("Check that the API, the tunnel, and the secrets are all aligned.")
        st.stop()

    if auto_refresh_seconds > 0:
        st.markdown(
            f'<div class="refresh-note">The dashboard refreshes automatically every {auto_refresh_seconds} seconds while this tab stays open, so you can watch the latest database point without logging in again.</div>',
            unsafe_allow_html=True,
        )

    if stale_snapshot:
        st.caption("Showing the last successful refresh because the latest API request failed.")

    latest_ts = latest.get("ts_eastern")
    if latest_ts:
        st.caption(f"Last available data point in database: {latest_ts}")
    else:
        st.caption("Last available data point unavailable")

    state_bar = "".join(
        [
            status_chip("Pulse Tube", latest.get("pulse_tube")),
            status_chip("Turbo", latest.get("turbo_1")),
            status_chip("Scroll 1", latest.get("scroll_1")),
            status_chip("Scroll 2", latest.get("scroll_2")),
        ]
    )
    st.markdown(state_bar, unsafe_allow_html=True)

    temp_hist = {key: histories.get(key, empty_history_df()) for key in TEMPERATURE_KEYS}
    press_hist = {key: histories.get(key, empty_history_df()) for key in PRESSURE_KEYS}
    flow_hist = {key: histories.get(key, empty_history_df()) for key in FLOW_KEYS}
    state_hist = {key: histories.get(key, empty_history_df()) for key in STATE_KEYS}

    mxc_recent = temp_hist["T_MXC"]
    duty_cycle = {key: duty_cycle_percent(state_hist[key]) for key in STATE_KEYS}
    state_starts = {key: count_state_starts(state_hist[key], lookback_hours=24) for key in STATE_KEYS}
    fridge_status, fridge_status_help, fridge_status_tone = fridge_state(latest, histories)
    below_20mk_h = time_below_threshold_hours(mxc_recent, threshold=0.020)
    total_below_20mk = metrics.get("hours_below_20mK_total")
    total_below_20mk_help = "Available over the full record"
    if is_missing(total_below_20mk):
        total_below_20mk = below_20mk_h
        total_below_20mk_help = f"Reported over the available {hours}-hour window"

    tabs = st.tabs(["Overview", "Temperatures", "Pressures", "Effective EM Environment", "Operations"])

    with tabs[0]:
        st.markdown("### Current state")
        st.markdown(
            f'<div class="section-caption">Live fridge summary using the latest available database row, followed by recent {hours}-hour trends.</div>',
            unsafe_allow_html=True,
        )

        row1 = st.columns(5, gap="medium")
        with row1[0]:
            render_metric_box("Mixing Chamber", fmt_temp(latest.get("T_MXC"), always_mk=True), "Current MXC temperature")
        with row1[1]:
            render_metric_box("Still", fmt_temp(latest.get("T_Still"), always_mk=True), "Current still temperature")
        with row1[2]:
            render_metric_box("Fridge state", fridge_status, fridge_status_help, tone=fridge_status_tone)
        with row1[3]:
            render_metric_box("P1", fmt_pressure(latest.get("P1")), f"Latest pressure gauge P1 [{PRESSURE_UNIT}]")
        with row1[4]:
            render_metric_box("Flow", fmt_flow(latest.get("Flow")), "Latest flow value")

        st.write("")
        row2 = st.columns(4, gap="large")
        with row2[0]:
            render_metric_box("Pulse Tube Hours", fmt_hours(latest.get("total_hours_pulse_tube")), "Cumulative hardware counter")
        with row2[1]:
            render_metric_box("Turbo Hours", fmt_hours(latest.get("total_hours_turbo_1")), "Cumulative hardware counter")
        with row2[2]:
            render_metric_box("Scroll 1 Hours", fmt_hours(latest.get("total_hours_scroll_1")), "Cumulative hardware counter")
        with row2[3]:
            render_metric_box("Scroll 2 Hours", fmt_hours(latest.get("total_hours_scroll_2")), "Cumulative hardware counter")

        st.write("")
        st.markdown(f"### {hours}-hour summary")
        st.markdown(
            '<div class="section-caption">Grouped time-domain plots with thicker traces, readable legends, and explicit time labeling.</div>',
            unsafe_allow_html=True,
        )

        temp_scale_overview = st.radio(
            "Temperature axis scale",
            ["Linear", "Log"],
            horizontal=True,
            key="temp_scale_overview",
        )
        fig_temp = make_multi_trace_figure(
            temp_hist,
            title=f"Temperature channels, last {hours} h",
            yaxis_title="Temperature [K]",
            log_y=temp_scale_overview == "Log",
            height=540,
        )
        st.plotly_chart(
            fig_temp,
            theme=None,
            use_container_width=True,
            config={"displaylogo": False, "responsive": True},
        )

        st.write("")

        fig_press = make_multi_trace_figure(
            press_hist,
            title=f"Pressure gauges, last {hours} h",
            yaxis_title=f"Pressure [{PRESSURE_UNIT}]",
            log_y=True,
            height=560,
        )
        st.plotly_chart(
            fig_press,
            theme=None,
            use_container_width=True,
            config={"displaylogo": False, "responsive": True},
        )

    with tabs[1]:
        st.markdown("### Temperature monitoring")
        st.markdown(
            '<div class="section-caption">All cryogenic stages are shown together for direct comparison and fast diagnosis.</div>',
            unsafe_allow_html=True,
        )

        temp_scale = st.radio(
            "Temperature axis scale",
            ["Linear", "Log"],
            horizontal=True,
            key="temp_scale_temperatures",
        )
        fig_temp = make_multi_trace_figure(
            temp_hist,
            title=f"Temperature channels, last {hours} h",
            yaxis_title="Temperature [K]",
            log_y=temp_scale == "Log",
            height=580,
        )
        st.plotly_chart(
            fig_temp,
            theme=None,
            use_container_width=True,
            config={"displaylogo": False, "responsive": True},
        )

        st.write("")

        temp_cols = st.columns(4, gap="large")
        for col, key in zip(temp_cols, TEMPERATURE_KEYS):
            with col:
                render_metric_box(
                    PRETTY_NAMES[key],
                    fmt_temp(latest.get(key), always_mk=key in {"T_Still", "T_MXC"}),
                    "Latest value",
                )

        st.write("")

        cold_cols = st.columns(4, gap="large")
        with cold_cols[0]:
            render_metric_box("Time below 20 mK", fmt_hours(below_20mk_h), f"Reported over the last {hours} h")
        with cold_cols[1]:
            render_metric_box("Total below 20 mK", fmt_hours(total_below_20mk), total_below_20mk_help)
        with cold_cols[2]:
            render_metric_box("Fridge state", fridge_status, fridge_status_help, tone=fridge_status_tone)
        with cold_cols[3]:
            render_metric_box("Latest MXC point", fmt_temp(latest.get("T_MXC"), always_mk=True), "Last available MXC point")

    with tabs[2]:
        st.markdown("### Pressure monitoring")
        st.markdown(
            '<div class="section-caption">All pressure gauges are grouped together with logarithmic scaling for readability across orders of magnitude.</div>',
            unsafe_allow_html=True,
        )

        fig_press = make_multi_trace_figure(
            press_hist,
            title=f"Pressure gauges, last {hours} h",
            yaxis_title=f"Pressure [{PRESSURE_UNIT}]",
            log_y=True,
            height=600,
        )
        st.plotly_chart(
            fig_press,
            theme=None,
            use_container_width=True,
            config={"displaylogo": False, "responsive": True},
        )

        st.write("")

        pcols = st.columns(6, gap="small")
        for col, key in zip(pcols, PRESSURE_KEYS):
            with col:
                render_metric_box(key, fmt_pressure(latest.get(key)), f"Latest value [{PRESSURE_UNIT}]")

        st.write("")

        fig_flow = make_multi_trace_figure(
            flow_hist,
            title=f"Flow, last {hours} h",
            yaxis_title="Flow",
            log_y=False,
            height=450,
        )
        st.plotly_chart(
            fig_flow,
            theme=None,
            use_container_width=True,
            config={"displaylogo": False, "responsive": True},
        )

    with tabs[3]:
        st.markdown("### Effective EM Environment")
        st.markdown(
            '<div class="section-caption">Effective microwave temperature from room temperature through the attenuator chain, using the live BlueFors stage temperatures.</div>',
            unsafe_allow_html=True,
        )

        em_config_top = st.columns([1.1, 1.1, 1.4], gap="medium")
        with em_config_top[0]:
            em_freq_ghz = st.number_input(
                "Reference frequency [GHz]",
                min_value=1.0,
                max_value=20.0,
                value=float(EM_DEFAULT_FREQ_GHZ),
                step=0.1,
                key="em_reference_frequency_ghz",
            )
        with em_config_top[1]:
            em_room_temp_k = st.number_input(
                "Room input [K]",
                min_value=1.0,
                max_value=400.0,
                value=float(EM_ROOM_TEMP_DEFAULT_K),
                step=1.0,
                key="em_room_temperature_k",
            )
        with em_config_top[2]:
            plot_em_history = st.checkbox(
                "Plot evolution over time",
                value=False,
                key="em_plot_history",
                help="Use the current attenuation settings to replay the effective line temperature over the selected dashboard window.",
            )

        em_atten_cols = st.columns(4, gap="medium")
        em_stage_attens: Dict[str, float] = {}
        for col, key in zip(em_atten_cols, EM_STAGE_KEYS):
            with col:
                em_stage_attens[key] = st.number_input(
                    f"{PRETTY_NAMES[key]} atten. [dB]",
                    min_value=0.0,
                    max_value=60.0,
                    value=float(EM_DEFAULT_ATTEN_DB[key]),
                    step=1.0,
                    key=f"em_atten_{key}",
                )

        st.caption(
            "Model: "
            f"{em_room_temp_k:.0f} K room source -> "
            f"50 K ({em_stage_attens['T_50K']:.0f} dB) -> "
            f"4 K ({em_stage_attens['T_4K']:.0f} dB) -> "
            f"Still ({em_stage_attens['T_Still']:.0f} dB) -> "
            f"MXC ({em_stage_attens['T_MXC']:.0f} dB), evaluated at {em_freq_ghz:.1f} GHz."
        )
        if np.isclose(em_stage_attens["T_50K"], 0.0):
            st.caption(
                "With 0 dB at 50 K, the line is not thermalized there, so the effective line temperature can stay close to the room source until the first colder attenuator."
            )
        st.caption("Cards report the effective line temperature after each stage. The local plate temperature and attenuation are shown beneath each card.")

        em_latest_stage_temps: Dict[str, float] = {}
        em_missing_keys = []
        for key in EM_STAGE_KEYS:
            value = freshest_value(latest, temp_hist, key)
            if is_missing(value):
                em_missing_keys.append(PRETTY_NAMES[key])
            else:
                em_latest_stage_temps[key] = float(value)

        if em_missing_keys:
            st.info("Not enough live temperature data to evaluate: " + ", ".join(em_missing_keys))
        else:
            _, em_teff_latest = compute_em_chain(
                em_latest_stage_temps,
                em_stage_attens,
                em_freq_ghz,
                room_temp_k=em_room_temp_k,
            )

            em_metric_cols = st.columns(4, gap="large")
            for col, key in zip(em_metric_cols, EM_STAGE_KEYS):
                with col:
                    render_metric_box(
                        f"After {PRETTY_NAMES[key]}",
                        fmt_em_temp(float(np.asarray(em_teff_latest[key]).reshape(-1)[-1])),
                        f"Plate {fmt_temp(em_latest_stage_temps[key])} • local atten {em_stage_attens[key]:.0f} dB",
                    )

            st.write("")
            with st.expander("Model assumptions", expanded=False):
                st.markdown(
                    "This tab follows the same stage-by-stage thermal beam-splitter model as the reference implementation. "
                    "Each attenuator partially transmits the incoming thermal occupation according to its linear loss and adds noise set by the most recent local stage temperature, then the resulting occupation is converted into an effective temperature at the selected frequency."
                )

            if plot_em_history:
                em_history_series = compute_em_history(
                    temp_hist,
                    latest,
                    em_stage_attens,
                    em_freq_ghz,
                    room_temp_k=em_room_temp_k,
                )
                if em_history_series:
                    em_scale = st.radio(
                        "Effective EM axis scale",
                        ["Linear", "Log"],
                        horizontal=True,
                        key="em_environment_scale",
                    )
                    fig_em = make_multi_trace_figure(
                        em_history_series,
                        title=f"Effective EM environment, last {hours} h",
                        yaxis_title="Effective temperature [K]",
                        log_y=em_scale == "Log",
                        height=560,
                    )
                    st.plotly_chart(
                        fig_em,
                        theme=None,
                        use_container_width=True,
                        config={"displaylogo": False, "responsive": True},
                    )
                else:
                    st.info("Effective EM history is unavailable for the selected time window.")

            st.caption("For further reference visit this repo: https://github.com/mvwf/qublitz")

    with tabs[4]:
        st.markdown("### Operations summary")
        st.markdown(
            '<div class="section-caption">Routine-maintenance indicators from current machine states, runtime counters, and recent history.</div>',
            unsafe_allow_html=True,
        )

        row1 = st.columns(4, gap="large")
        with row1[0]:
            render_metric_box("Pulse Tube", fmt_state(latest.get("pulse_tube")), "Current machine state")
        with row1[1]:
            render_metric_box("Turbo", fmt_state(latest.get("turbo_1")), "Current machine state")
        with row1[2]:
            render_metric_box("Scroll 1", fmt_state(latest.get("scroll_1")), "Current machine state")
        with row1[3]:
            render_metric_box("Scroll 2", fmt_state(latest.get("scroll_2")), "Current machine state")

        st.write("")

        row2 = st.columns(4, gap="large")
        with row2[0]:
            render_metric_box("Pulse Tube Hours", fmt_hours(latest.get("total_hours_pulse_tube")), "Cumulative counter")
        with row2[1]:
            render_metric_box("Turbo Hours", fmt_hours(latest.get("total_hours_turbo_1")), "Cumulative counter")
        with row2[2]:
            render_metric_box("Scroll 1 Hours", fmt_hours(latest.get("total_hours_scroll_1")), "Cumulative counter")
        with row2[3]:
            render_metric_box("Scroll 2 Hours", fmt_hours(latest.get("total_hours_scroll_2")), "Cumulative counter")

        st.write("")

        row3 = st.columns(4, gap="large")
        with row3[0]:
            render_metric_box("Pulse Tube Duty", fmt_percent(duty_cycle["pulse_tube"]), f"On-time over the last {hours} h")
        with row3[1]:
            render_metric_box("Turbo Duty", fmt_percent(duty_cycle["turbo_1"]), f"On-time over the last {hours} h")
        with row3[2]:
            render_metric_box("Scroll 1 Duty", fmt_percent(duty_cycle["scroll_1"]), f"On-time over the last {hours} h")
        with row3[3]:
            render_metric_box("Scroll 2 Duty", fmt_percent(duty_cycle["scroll_2"]), f"On-time over the last {hours} h")

        st.write("")

        row4 = st.columns(4, gap="large")
        with row4[0]:
            render_metric_box("Pulse Tube Starts (24 h)", fmt_count(coalesce_value(metrics.get("pulse_tube_starts_24h"), state_starts["pulse_tube"])), "Transitions from off to on")
        with row4[1]:
            render_metric_box("Turbo Starts (24 h)", fmt_count(coalesce_value(metrics.get("turbo_1_starts_24h"), state_starts["turbo_1"])), "Transitions from off to on")
        with row4[2]:
            render_metric_box("Scroll 1 Starts (24 h)", fmt_count(coalesce_value(metrics.get("scroll_1_starts_24h"), state_starts["scroll_1"])), "Transitions from off to on")
        with row4[3]:
            render_metric_box("Scroll 2 Starts (24 h)", fmt_count(coalesce_value(metrics.get("scroll_2_starts_24h"), state_starts["scroll_2"])), "Transitions from off to on")

        st.write("")

        fig_state = make_multi_trace_figure(
            state_hist,
            title=f"State timeline, last {hours} h",
            yaxis_title="State",
            log_y=False,
            height=500,
        )
        fig_state.update_yaxes(range=[-0.1, 1.1], tickvals=[0, 1])
        st.plotly_chart(
            fig_state,
            theme=None,
            use_container_width=True,
            config={"displaylogo": False, "responsive": True},
        )

        st.write("")
        st.info(
            f"The most recent database row is timestamped {latest_ts if latest_ts else 'unavailable'}. "
            "If this drifts far behind wall-clock time, the sync job or the upstream parquet update may be lagging."
        )


if auto_refresh_seconds > 0 and hasattr(st, "fragment"):
    @st.fragment(run_every=f"{auto_refresh_seconds}s")
    def live_dashboard_fragment():
        render_dashboard_page()

    live_dashboard_fragment()
else:
    render_dashboard_page()
