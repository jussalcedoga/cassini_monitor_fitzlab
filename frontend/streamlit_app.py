from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
import plotly.express as px
import streamlit as st

API_BASE = st.secrets["api_base"].rstrip("/")
API_KEY = st.secrets["api_key"]
DASHBOARD_PASSWORD = st.secrets["dashboard_password"]
REQUEST_TIMEOUT_SECONDS = 20

HISTORY_PLOTS = [
    ("T_Still", "Still Temperature", False),
    ("T_MXC", "MXC Temperature", False),
    ("P1", "P1", True),
    ("P2", "P2", True),
    ("P3", "P3", True),
    ("P4", "P4", True),
    ("P5", "P5", True),
    ("P6", "P6", True),
    ("Flow", "Flow", False),
    ("pulse_tube", "Pulse Tube State", False),
    ("turbo_1", "Turbo State", False),
    ("scroll_1", "Scroll 1 State", False),
    ("scroll_2", "Scroll 2 State", False),
]

st.set_page_config(page_title="Cassini BlueFors Dashboard", layout="wide")

if "ok" not in st.session_state:
    st.session_state.ok = False

if not st.session_state.ok:
    pwd = st.text_input("Password", type="password")
    if st.button("Enter"):
        if pwd == DASHBOARD_PASSWORD:
            st.session_state.ok = True
            st.rerun()
        else:
            st.error("Wrong password")
    st.stop()

headers = {"X-API-Key": API_KEY}


def _fetch_json(path, **params):
    r = requests.get(
        f"{API_BASE}{path}",
        headers=headers,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=30, show_spinner=False)
def load_dashboard(hours: int):
    metrics = _fetch_json("/metrics")
    histories = {}

    with ThreadPoolExecutor(max_workers=min(8, len(HISTORY_PLOTS))) as pool:
        futures = {
            pool.submit(_fetch_json, f"/history/{key}", hours=hours): key
            for key, _, _ in HISTORY_PLOTS
        }
        for future in as_completed(futures):
            key = futures[future]
            histories[key] = future.result().get("points", [])

    return metrics, histories


st.title("Cassini BlueFors Dashboard")
hours = st.sidebar.selectbox("Window (hours)", [1, 3, 6, 12, 24, 48, 72, 168], index=4)

try:
    metrics, histories = load_dashboard(hours)
except requests.RequestException as exc:
    st.error(f"Could not load dashboard data from {API_BASE}: {exc}")
    st.stop()

st.subheader("Key metrics")
c1, c2, c3, c4 = st.columns(4)
c1.metric("T Still", metrics.get("T_Still"))
c2.metric("T MXC", metrics.get("T_MXC"))
c3.metric("P1", metrics.get("P1"))
c4.metric("Flow", metrics.get("Flow"))

c5, c6, c7, c8 = st.columns(4)
c5.metric("PT hours", metrics.get("total_hours_pulse_tube"))
c6.metric("Turbo hours", metrics.get("total_hours_turbo_1"))
c7.metric("Scroll 1 hours", metrics.get("total_hours_scroll_1"))
c8.metric("Scroll 2 hours", metrics.get("total_hours_scroll_2"))


def plot_key(points, title, log_y=False):
    df = pd.DataFrame(points)
    if df.empty:
        st.info(f"No data for {title}")
        return
    df["ts_eastern"] = pd.to_datetime(df["ts_eastern"])
    fig = px.line(df, x="ts_eastern", y="value", title=title)
    if log_y:
        fig.update_yaxes(type="log")
    st.plotly_chart(fig, use_container_width=True)


st.subheader("Temperatures")
a, b = st.columns(2)
with a:
    plot_key(histories["T_Still"], "Still Temperature")
with b:
    plot_key(histories["T_MXC"], "MXC Temperature")

st.subheader("Pressures")
p1, p2, p3 = st.columns(3)
with p1:
    plot_key(histories["P1"], "P1", log_y=True)
with p2:
    plot_key(histories["P2"], "P2", log_y=True)
with p3:
    plot_key(histories["P3"], "P3", log_y=True)

p4, p5, p6 = st.columns(3)
with p4:
    plot_key(histories["P4"], "P4", log_y=True)
with p5:
    plot_key(histories["P5"], "P5", log_y=True)
with p6:
    plot_key(histories["P6"], "P6", log_y=True)

st.subheader("Flow and machine state")
x1, x2, x3, x4 = st.columns(4)
with x1:
    plot_key(histories["Flow"], "Flow")
with x2:
    plot_key(histories["pulse_tube"], "Pulse Tube State")
with x3:
    plot_key(histories["turbo_1"], "Turbo State")
with x4:
    plot_key(histories["scroll_1"], "Scroll 1 State")

st.subheader("Additional Machine State")
plot_key(histories["scroll_2"], "Scroll 2 State")
