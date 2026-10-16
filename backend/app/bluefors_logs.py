from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

READING_COLUMNS = [
    "ts_eastern",
    "P1",
    "P2",
    "P3",
    "P4",
    "P5",
    "P6",
    "T_50K",
    "T_4K",
    "T_Still",
    "T_MXC",
    "Flow",
    "total_hours_scroll_1",
    "total_hours_scroll_2",
    "total_hours_turbo_1",
    "total_hours_pulse_tube",
    "scroll_1",
    "scroll_2",
    "turbo_1",
    "pulse_tube",
    "source_file",
]
VALUE_COLUMNS = READING_COLUMNS[1:-1]

TEMPERATURE_FILE_MAP = {
    "ch1 t": "T_50K",
    "ch2 t": "T_4K",
    "ch5 t": "T_Still",
    "ch6 t": "T_MXC",
}

PRESSURE_CHANNEL_MAP = {
    "CH1": "P1",
    "CH2": "P2",
    "CH3": "P3",
    "CH4": "P4",
    "CH5": "P5",
    "CH6": "P6",
}

SIMPLE_VALUE_PREFIXES = {
    "flowmeter": "Flow",
}

KNOWN_PREFIXES = (
    *TEMPERATURE_FILE_MAP,
    *SIMPLE_VALUE_PREFIXES,
    "channels ",
    "maxigauge ",
    "status_",
    "heaters ",
    "ch1 r",
    "ch2 r",
    "ch5 r",
    "ch6 r",
)

TS_FORMAT = "%d-%m-%y %H:%M:%S"


def _is_known_name(name: str) -> bool:
    lower = name.lower()
    if not lower.endswith(".log"):
        return False
    return lower[:-4].strip().startswith(KNOWN_PREFIXES)


def is_known_log_file(path: Path) -> bool:
    return _is_known_name(path.name)


def _known_entries(day_dir: Path) -> list[os.DirEntry]:
    # os.scandir returns size/mtime from the directory listing on Windows,
    # so signatures cost one listing per folder instead of one stat per file.
    try:
        with os.scandir(day_dir) as it:
            return sorted(
                (entry for entry in it if entry.is_file() and _is_known_name(entry.name)),
                key=lambda entry: entry.name,
            )
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return []


def known_log_files(day_dir: Path) -> list[Path]:
    return [Path(entry.path) for entry in _known_entries(day_dir)]


def day_directories(root: Path) -> list[Path]:
    if not root.exists():
        return []

    if _known_entries(root):
        return [root]

    with os.scandir(root) as it:
        children = sorted(Path(entry.path) for entry in it if entry.is_dir())
    return [child for child in children if _known_entries(child)]


def day_signature(day_dir: Path) -> tuple[int, int]:
    total_size = 0
    latest_mtime_ns = 0
    for entry in _known_entries(day_dir):
        stat = entry.stat()
        total_size += stat.st_size
        latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
    return total_size, latest_mtime_ns


def _read_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        text = handle.read()

    lines = text.splitlines()
    # BlueFors appends one line per sample. If the writer is mid-line when we
    # read, the last line is truncated (e.g. "2.935" instead of "2.935215e+02");
    # drop it and pick it up on the next pass.
    if lines and not text.endswith(("\n", "\r")):
        lines.pop()
    return [line.split(",") for line in lines if line.strip()]


def _parse_ts(dates: list[str], times: list[str]) -> pd.DatetimeIndex:
    stamps = [f"{d.strip()} {t.strip()}" for d, t in zip(dates, times)]
    return pd.DatetimeIndex(pd.to_datetime(stamps, format=TS_FORMAT, errors="coerce"))


def _to_float(value: str) -> float:
    text = value.strip()
    if not text:
        return np.nan
    try:
        return float(text)
    except ValueError:
        return np.nan


def _frame(dates: list[str], times: list[str], columns: dict[str, list[float]]) -> pd.DataFrame:
    if not dates:
        return pd.DataFrame()
    df = pd.DataFrame({"ts_eastern": _parse_ts(dates, times), **columns})
    return df[df["ts_eastern"].notna()]


def _pairs(payload: list[str]) -> dict[str, float]:
    return {payload[idx].strip().lower(): _to_float(payload[idx + 1]) for idx in range(0, len(payload) - 1, 2)}


def _simple_value_frame(path: Path, column: str) -> pd.DataFrame:
    dates, times, values = [], [], []
    for row in _read_rows(path):
        if len(row) < 3:
            continue
        dates.append(row[0])
        times.append(row[1])
        values.append(_to_float(row[2]))
    return _frame(dates, times, {column: values})


def _channels_frame(path: Path) -> pd.DataFrame:
    dates, times = [], []
    cols: dict[str, list[float]] = {"turbo_1": [], "scroll_1": [], "scroll_2": [], "pulse_tube": []}
    for row in _read_rows(path):
        if len(row) < 4:
            continue
        payload = row[2:]
        if len(payload) % 2 == 1:
            payload = payload[1:]
        kv = _pairs(payload)

        pulse_tube = kv.get("pulsetube", np.nan)
        if np.isnan(pulse_tube):
            pulse_tube = kv.get("compressor", np.nan)

        dates.append(row[0])
        times.append(row[1])
        cols["turbo_1"].append(kv.get("turbo1", np.nan))
        cols["scroll_1"].append(kv.get("scroll1", np.nan))
        cols["scroll_2"].append(kv.get("scroll2", np.nan))
        cols["pulse_tube"].append(pulse_tube)
    return _frame(dates, times, cols)


def _status_frame(path: Path) -> pd.DataFrame:
    dates, times, turbo_status = [], [], []
    for row in _read_rows(path):
        if len(row) < 4:
            continue
        payload = row[2:]
        if len(payload) % 2 == 1:
            continue
        dates.append(row[0])
        times.append(row[1])
        turbo_status.append(_pairs(payload).get("tc400pumpstatn", np.nan))
    return _frame(dates, times, {"turbo_1_status": turbo_status})


def _maxigauge_frame(path: Path) -> pd.DataFrame:
    dates, times = [], []
    cols: dict[str, list[float]] = {column: [] for column in PRESSURE_CHANNEL_MAP.values()}
    for row in _read_rows(path):
        if len(row) < 8:
            continue
        payload = row[2:]
        record = dict.fromkeys(cols, np.nan)
        for idx in range(0, len(payload), 6):
            chunk = payload[idx : idx + 6]
            if len(chunk) < 4:
                continue
            column = PRESSURE_CHANNEL_MAP.get(chunk[0].strip().upper())
            if column is not None:
                record[column] = _to_float(chunk[3])
        dates.append(row[0])
        times.append(row[1])
        for column, value in record.items():
            cols[column].append(value)
    return _frame(dates, times, cols)


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=READING_COLUMNS[:-1])

    df = df.sort_values("ts_eastern").drop_duplicates("ts_eastern", keep="last").reset_index(drop=True)

    if "turbo_1" not in df and "turbo_1_status" in df:
        df["turbo_1"] = df["turbo_1_status"]
    elif "turbo_1" in df and "turbo_1_status" in df:
        df["turbo_1"] = df["turbo_1"].combine_first(df["turbo_1_status"])

    if "turbo_1_status" in df:
        df = df.drop(columns=["turbo_1_status"])

    for column in VALUE_COLUMNS:
        if column not in df.columns:
            df[column] = np.nan
        df[column] = df[column].astype("float64")

    return df[READING_COLUMNS[:-1]]


def build_day_frame(day_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in known_log_files(day_dir):
        stem = path.stem.strip().lower()
        temp_column = next((column for prefix, column in TEMPERATURE_FILE_MAP.items() if stem.startswith(prefix)), None)
        value_column = next((column for prefix, column in SIMPLE_VALUE_PREFIXES.items() if stem.startswith(prefix)), None)

        if temp_column is not None:
            frames.append(_simple_value_frame(path, temp_column))
        elif value_column is not None:
            frames.append(_simple_value_frame(path, value_column))
        elif stem.startswith("channels "):
            frames.append(_channels_frame(path))
        elif stem.startswith("maxigauge "):
            frames.append(_maxigauge_frame(path))
        elif stem.startswith("status_"):
            frames.append(_status_frame(path))

    frames = [frame.drop_duplicates("ts_eastern", keep="last") for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame(columns=READING_COLUMNS)

    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="ts_eastern", how="outer")

    merged = _normalize_frame(merged)
    merged["source_file"] = str(day_dir)
    return merged[READING_COLUMNS]
