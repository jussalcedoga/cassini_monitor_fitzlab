from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from PIL import Image
import numpy as np

API_BASE = st.secrets["api_base"].rstrip("/")
API_KEY = st.secrets["api_key"]
DASHBOARD_PASSWORD = st.secrets["dashboard_password"]

st.set_page_config(
    page_title="Cassini BlueFors Dashboard",
    page_icon="🪐",
    layout="wide",
    initial_sidebar_state="expanded",
)

HEADERS = {"X-API-Key": API_KEY}
BOSTON_TZ = "America/New_York"

# ----------------------------
# Style
# ----------------------------
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
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------------------
# Password gate
# ----------------------------
if "ok" not in st.session_state:
    st.session_state.ok = False

if not st.session_state.ok:
    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        st.markdown("## Cassini BlueFors Dashboard")
        pwd = st.text_input("Password", type="password")
        if st.button("Enter"):
            if pwd == DASHBOARD_PASSWORD:
                st.session_state.ok = True
                st.rerun()
            else:
                st.error("Wrong password")
    st.stop()

# ----------------------------
# Constants
# ----------------------------
FRONTEND_DIR = Path(__file__).resolve().parent

DEFAULT_HOURS = 48
AVAILABLE_WINDOWS = [6, 12, 24, 48, 72]

TEMPERATURE_KEYS = ["T_50K", "T_4K", "T_Still", "T_MXC"]
PRESSURE_KEYS = ["P1", "P2", "P3", "P4", "P5", "P6"]
FLOW_KEYS = ["Flow"]
STATE_KEYS = ["pulse_tube", "turbo_1", "scroll_1", "scroll_2"]

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

# BlueFors-like temperature colors
color_map = {
    "T_50K": "#FE2A2A",
    "T_4K": "#54D400",
    "T_Still": "#FECB00",
    "T_MXC": "#0065FF",
}

COLORS = {
    "T_50K": color_map["T_50K"],
    "T_4K": color_map["T_4K"],
    "T_Still": color_map["T_Still"],
    "T_MXC": color_map["T_MXC"],
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

# ----------------------------
# Helpers
# ----------------------------
def safe_image(path: Path):
    try:
        return Image.open(path)
    except Exception:
        return None

def fmt_temp(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
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
        return "0"
    if abs(v) < 1e-2 or abs(v) >= 1e3:
        return f"{v:.2e}"
    if abs(v) < 1:
        return f"{v:.4f}"
    if abs(v) < 100:
        return f"{v:.3f}"
    return f"{v:.1f}"

def fmt_flow(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):.4f}"

def fmt_hours(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):,.1f} h"

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

@st.cache_data(show_spinner=False, ttl=20)
def fetch_json(path: str, params: Optional[dict] = None):
    r = requests.get(f"{API_BASE}{path}", headers=HEADERS, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()

@st.cache_data(show_spinner=False, ttl=20)
def fetch_history_df(key: str, hours: int) -> pd.DataFrame:
    payload = fetch_json(f"/history/{key}", params={"hours": hours})
    df = pd.DataFrame(payload.get("points", []))
    if df.empty:
        return pd.DataFrame(columns=["ts_eastern", "value"])
    df["ts_eastern"] = pd.to_datetime(df["ts_eastern"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["ts_eastern"]).sort_values("ts_eastern").copy()
    return df

@st.cache_data(show_spinner=False, ttl=20)
def fetch_many(keys: Tuple[str, ...], hours: int) -> Dict[str, pd.DataFrame]:
    return {k: fetch_history_df(k, hours) for k in keys}

def latest_value(df: pd.DataFrame) -> Optional[float]:
    if df.empty:
        return None
    v = df["value"].iloc[-1]
    return None if pd.isna(v) else float(v)

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

        fig.add_trace(
            go.Scatter(
                x=df["ts_eastern"],
                y=df["value"],
                mode="lines",
                name=PRETTY_NAMES.get(key, key),
                line=dict(color=COLORS.get(key), width=line_width),
                hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y}<extra>%{fullData.name}</extra>",
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
        autosize=False,
        width=1020,
        height=height,
        margin=dict(
            l=78,
            r=42,
            t=145,
            b=92,
        ),
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
            title=dict(
                text="Time",
                standoff=22,
                font=dict(size=18, color="black"),
            ),
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
            title=dict(
                text=yaxis_title,
                standoff=22,
                font=dict(size=18, color="black"),
            ),
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

# ----------------------------
# Sidebar
# ----------------------------
with st.sidebar:
    logo = safe_image(FRONTEND_DIR / "logo.png")
    cassini = safe_image(FRONTEND_DIR / "cassini.png")

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

    st.markdown("---")
    st.markdown("### API endpoint")
    st.code(API_BASE, language=None)

# ----------------------------
# Connectivity and data
# ----------------------------
api_ok = True
api_error = None
health = None
latest = {}
metrics = {}

try:
    health = fetch_json("/health")
    latest = fetch_json("/latest")
    metrics = fetch_json("/metrics")
except Exception as e:
    api_ok = False
    api_error = str(e)

st.title("Cassini BlueFors Dashboard")

if not api_ok:
    st.error("Could not load data from the API.")
    st.code(api_error)
    st.info("Check that the API, the tunnel, and the secrets are all aligned.")
    st.stop()

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

temp_hist = fetch_many(tuple(TEMPERATURE_KEYS), hours)
press_hist = fetch_many(tuple(PRESSURE_KEYS), hours)
flow_hist = fetch_many(tuple(FLOW_KEYS), hours)
state_hist = fetch_many(tuple(STATE_KEYS), hours)

mxc_recent = temp_hist["T_MXC"]

tabs = st.tabs(["Overview", "Temperatures", "Pressures", "Operations"])

# ----------------------------
# Overview
# ----------------------------
with tabs[0]:
    st.markdown("### Current state")
    st.markdown(
        '<div class="section-caption">Live fridge summary using the latest available database row, followed by recent two-day trends.</div>',
        unsafe_allow_html=True,
    )

    row1 = st.columns(4, gap="large")
    with row1[0]:
        render_metric_box("Mixing Chamber", fmt_temp(metrics.get("T_MXC")), "Current MXC temperature")
    with row1[1]:
        render_metric_box("Still", fmt_temp(metrics.get("T_Still")), "Current still temperature")
    with row1[2]:
        render_metric_box("P1", fmt_pressure(metrics.get("P1")), "Latest pressure gauge P1")
    with row1[3]:
        render_metric_box("Flow", fmt_flow(metrics.get("Flow")), "Latest flow value")

    st.write("")
    row2 = st.columns(4, gap="large")
    with row2[0]:
        render_metric_box("Pulse Tube Hours", fmt_hours(metrics.get("total_hours_pulse_tube")), "Cumulative hardware counter")
    with row2[1]:
        render_metric_box("Turbo Hours", fmt_hours(metrics.get("total_hours_turbo_1")), "Cumulative hardware counter")
    with row2[2]:
        render_metric_box("Scroll 1 Hours", fmt_hours(metrics.get("total_hours_scroll_1")), "Cumulative hardware counter")
    with row2[3]:
        render_metric_box("Scroll 2 Hours", fmt_hours(metrics.get("total_hours_scroll_2")), "Cumulative hardware counter")

    st.write("")
    st.markdown("### Two-day summary")
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
    temp_log_y_overview = temp_scale_overview == "Log"

    fig_temp = make_multi_trace_figure(
        temp_hist,
        title=f"Temperature channels, last {hours} h",
        yaxis_title="Temperature [K]",
        log_y=temp_log_y_overview,
        height=540,
    )
    st.plotly_chart(fig_temp, theme=None)

    st.write("")

    fig_press = make_multi_trace_figure(
        press_hist,
        title=f"Pressure gauges, last {hours} h",
        yaxis_title="Pressure [arb.]",
        log_y=True,
        height=560,
    )
    st.plotly_chart(fig_press, theme=None)

# ----------------------------
# Temperatures
# ----------------------------
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
    temp_log_y = temp_scale == "Log"

    fig_temp = make_multi_trace_figure(
        temp_hist,
        title=f"Temperature channels, last {hours} h",
        yaxis_title="Temperature [K]",
        log_y=temp_log_y,
        height=580,
    )
    st.plotly_chart(fig_temp, theme=None)

    st.write("")

    temp_cols = st.columns(4, gap="large")
    for col, key in zip(temp_cols, TEMPERATURE_KEYS):
        with col:
            render_metric_box(PRETTY_NAMES[key], fmt_temp(latest.get(key)), "Latest value")

    st.write("")

    below_20mk_h = time_below_threshold_hours(mxc_recent, threshold=0.020)
    c1, c2, c3 = st.columns(3, gap="large")
    with c1:
        render_metric_box("Time below 20 mK", fmt_hours(below_20mk_h), f"Estimated over the last {hours} h")
    with c2:
        current_mxc = latest.get("T_MXC")
        cold_state = "Cold" if current_mxc is not None and float(current_mxc) < 0.020 else "Not yet cold"
        render_metric_box("Current cold state", cold_state, "Uses MXC < 20 mK")
    with c3:
        render_metric_box("Latest MXC point", fmt_temp(latest.get("T_MXC")), "Last available point")

# ----------------------------
# Pressures
# ----------------------------
with tabs[2]:
    st.markdown("### Pressure monitoring")
    st.markdown(
        '<div class="section-caption">All pressure gauges are grouped together with logarithmic scaling for readability across orders of magnitude.</div>',
        unsafe_allow_html=True,
    )

    fig_press = make_multi_trace_figure(
        press_hist,
        title=f"Pressure gauges, last {hours} h",
        yaxis_title="Pressure [arb.]",
        log_y=True,
        height=600,
    )
    st.plotly_chart(fig_press, theme=None)

    st.write("")

    pcols = st.columns(6, gap="small")
    for col, key in zip(pcols, PRESSURE_KEYS):
        with col:
            render_metric_box(key, fmt_pressure(latest.get(key)), "Latest value")

    st.write("")

    fig_flow = make_multi_trace_figure(
        flow_hist,
        title=f"Flow, last {hours} h",
        yaxis_title="Flow",
        log_y=False,
        height=450,
    )
    st.plotly_chart(fig_flow, theme=None)

# ----------------------------
# Operations
# ----------------------------
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
        render_metric_box("Pulse Tube Hours", fmt_hours(metrics.get("total_hours_pulse_tube")), "Cumulative counter")
    with row2[1]:
        render_metric_box("Turbo Hours", fmt_hours(metrics.get("total_hours_turbo_1")), "Cumulative counter")
    with row2[2]:
        render_metric_box("Scroll 1 Hours", fmt_hours(metrics.get("total_hours_scroll_1")), "Cumulative counter")
    with row2[3]:
        render_metric_box("Scroll 2 Hours", fmt_hours(metrics.get("total_hours_scroll_2")), "Cumulative counter")

    st.write("")

    fig_state = make_multi_trace_figure(
        state_hist,
        title=f"State timeline, last {hours} h",
        yaxis_title="State",
        log_y=False,
        height=500,
    )
    fig_state.update_yaxes(range=[-0.1, 1.1], tickvals=[0, 1])
    st.plotly_chart(fig_state, theme=None)

    st.write("")
    st.info(
        f"The most recent database row is timestamped {latest_ts if latest_ts else 'unavailable'}. "
        "If this drifts far behind wall-clock time, the sync job or the upstream parquet update may be lagging."
    )
