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
        padding-top: 1rem;
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
        height: 3.1rem;
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
    if df.empty or "value" not in df:
        return None
    values = pd.to_numeric(df["value"], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


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


def fridge_state(latest: dict, histories: Dict[str, pd.DataFrame]) -> tuple[str, str]:
    mxc = coalesce_value(latest.get("T_MXC"), latest_history_value(histories.get("T_MXC", empty_history_df())))
    still = coalesce_value(latest.get("T_Still"), latest_history_value(histories.get("T_Still", empty_history_df())))
    pulse_tube = coalesce_value(latest.get("pulse_tube"), latest_history_value(histories.get("pulse_tube", empty_history_df())))
    turbo = coalesce_value(latest.get("turbo_1"), latest_history_value(histories.get("turbo_1", empty_history_df())))
    scroll_1 = coalesce_value(latest.get("scroll_1"), latest_history_value(histories.get("scroll_1", empty_history_df())))
    scroll_2 = coalesce_value(latest.get("scroll_2"), latest_history_value(histories.get("scroll_2", empty_history_df())))
    slope = recent_temperature_slope(histories.get("T_MXC", empty_history_df()), lookback_hours=3)

    if is_missing(mxc):
        return "Unknown", "MXC history unavailable"

    mxc = float(mxc)
    still_value = None if is_missing(still) else float(still)
    pumps_on = any(
        not is_missing(value) and float(value) >= 0.5
        for value in (pulse_tube, turbo, scroll_1, scroll_2)
    )
    slope_threshold = 0.001 if mxc < 0.2 else 0.01

    if mxc < 0.02 and (slope is None or abs(slope) <= slope_threshold):
        return "Cooled", "MXC is below 20 mK and stable"

    if slope is not None and slope <= -slope_threshold:
        return "Cooling down", "MXC is trending colder"

    if slope is not None and slope >= slope_threshold:
        return "Warming up", "MXC is trending warmer"

    if pumps_on and mxc >= 0.02:
        return "Cooling down", "Cooling hardware is active above base temperature"

    if not pumps_on and (mxc >= 0.02 or (still_value is not None and still_value > 1.0)):
        return "Warming up", "Cooling hardware is not fully engaged"

    return ("Cooled", "Cryostat is holding near its base temperature") if mxc < 0.05 else ("Cooling down", "Cryostat is settling toward a colder state")


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


def render_metric_box(title: str, value: str, help_text: str = ""):
    st.markdown(
        f"""
        <div class="metric-card">
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
    fridge_status, fridge_status_help = fridge_state(latest, histories)
    below_20mk_h = time_below_threshold_hours(mxc_recent, threshold=0.020)
    total_below_20mk = metrics.get("hours_below_20mK_total")
    total_below_20mk_help = "Available over the full record"
    if is_missing(total_below_20mk):
        total_below_20mk = below_20mk_h
        total_below_20mk_help = f"Reported over the available {hours}-hour window"

    tabs = st.tabs(["Overview", "Temperatures", "Pressures", "Operations"])

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
            render_metric_box("Fridge state", fridge_status, fridge_status_help)
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
            render_metric_box("Fridge state", fridge_status, fridge_status_help)
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
