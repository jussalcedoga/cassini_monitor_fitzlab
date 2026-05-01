from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

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


def _stem_lower(path: Path) -> str:
    return path.stem.strip().lower()


def is_known_log_file(path: Path) -> bool:
    stem = _stem_lower(path)
    return (
        any(stem.startswith(prefix) for prefix in TEMPERATURE_FILE_MAP)
        or any(stem.startswith(prefix) for prefix in SIMPLE_VALUE_PREFIXES)
        or stem.startswith("channels ")
        or stem.startswith("maxigauge ")
        or stem.startswith("status_")
        or stem.startswith("heaters ")
        or stem.startswith("ch1 r")
        or stem.startswith("ch2 r")
        or stem.startswith("ch5 r")
        or stem.startswith("ch6 r")
        or stem.startswith("ch6 t")
    )


def known_log_files(day_dir: Path) -> list[Path]:
    return sorted(
        path for path in day_dir.glob("*.log") if path.is_file() and is_known_log_file(path)
    )


def day_directories(root: Path) -> list[Path]:
    if not root.exists():
        return []

    direct_logs = known_log_files(root)
    if direct_logs:
        return [root]

    days = []
    for child in sorted(path for path in root.iterdir() if path.is_dir()):
        if known_log_files(child):
            days.append(child)
    return days


def day_signature(day_dir: Path) -> tuple[int, int]:
    files = known_log_files(day_dir)
    if not files:
        return 0, 0

    total_size = 0
    latest_mtime_ns = 0
    for path in files:
        stat = path.stat()
        total_size += stat.st_size
        latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
    return total_size, latest_mtime_ns


def _parse_ts(date_text: str, time_text: str) -> pd.Timestamp:
    return pd.to_datetime(
        f"{date_text.strip()} {time_text.strip()}",
        format="%d-%m-%y %H:%M:%S",
        errors="coerce",
    )


def _to_float(value: str):
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _read_rows(path: Path) -> Iterable[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if row:
                yield row


def _simple_value_frame(path: Path, column: str) -> pd.DataFrame:
    records = []
    for row in _read_rows(path):
        if len(row) < 3:
            continue
        ts = _parse_ts(row[0], row[1])
        value = _to_float(row[2])
        if pd.isna(ts):
            continue
        records.append({"ts_eastern": ts, column: value})
    return pd.DataFrame.from_records(records)


def _channels_frame(path: Path) -> pd.DataFrame:
    records = []
    for row in _read_rows(path):
        if len(row) < 4:
            continue
        ts = _parse_ts(row[0], row[1])
        if pd.isna(ts):
            continue

        payload = row[2:]
        if len(payload) % 2 == 1:
            payload = payload[1:]

        key_values: dict[str, float | None] = {}
        for idx in range(0, len(payload) - 1, 2):
            key = payload[idx].strip().lower()
            key_values[key] = _to_float(payload[idx + 1])

        pulse_tube = key_values.get("pulsetube")
        if pulse_tube is None:
            pulse_tube = key_values.get("compressor")

        records.append(
            {
                "ts_eastern": ts,
                "turbo_1": key_values.get("turbo1"),
                "scroll_1": key_values.get("scroll1"),
                "scroll_2": key_values.get("scroll2"),
                "pulse_tube": pulse_tube,
            }
        )

    return pd.DataFrame.from_records(records)


def _status_frame(path: Path) -> pd.DataFrame:
    records = []
    for row in _read_rows(path):
        if len(row) < 4:
            continue
        ts = _parse_ts(row[0], row[1])
        if pd.isna(ts):
            continue

        payload = row[2:]
        if len(payload) % 2 == 1:
            continue

        key_values: dict[str, float | None] = {}
        for idx in range(0, len(payload) - 1, 2):
            key = payload[idx].strip().lower()
            key_values[key] = _to_float(payload[idx + 1])

        records.append(
            {
                "ts_eastern": ts,
                "turbo_1_status": key_values.get("tc400pumpstatn"),
                "turbo_2_status": key_values.get("tc400pumpstatn_2"),
            }
        )

    return pd.DataFrame.from_records(records)


def _maxigauge_frame(path: Path) -> pd.DataFrame:
    records = []
    for row in _read_rows(path):
        if len(row) < 8:
            continue
        ts = _parse_ts(row[0], row[1])
        if pd.isna(ts):
            continue

        payload = row[2:]
        record = {"ts_eastern": ts}
        for idx in range(0, len(payload), 6):
            chunk = payload[idx : idx + 6]
            if len(chunk) < 4:
                continue
            channel = chunk[0].strip().upper()
            column = PRESSURE_CHANNEL_MAP.get(channel)
            if column is None:
                continue
            record[column] = _to_float(chunk[3])
        records.append(record)

    return pd.DataFrame.from_records(records)


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=READING_COLUMNS[:-1])

    df = df.sort_values("ts_eastern").drop_duplicates("ts_eastern", keep="last").reset_index(drop=True)

    if "turbo_1" not in df and "turbo_1_status" in df:
        df["turbo_1"] = df["turbo_1_status"]
    elif "turbo_1" in df and "turbo_1_status" in df:
        df["turbo_1"] = df["turbo_1"].combine_first(df["turbo_1_status"])

    extra_columns = {"turbo_1_status", "turbo_2_status"}
    for column in extra_columns.intersection(df.columns):
        df = df.drop(columns=[column])

    for column in READING_COLUMNS[:-1]:
        if column not in df.columns:
            df[column] = None

    return df[READING_COLUMNS[:-1]]


def build_day_frame(day_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in known_log_files(day_dir):
        stem = _stem_lower(path)
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

    if not frames:
        return pd.DataFrame(columns=READING_COLUMNS)

    merged = frames[0]
    for frame in frames[1:]:
        if frame.empty:
            continue
        merged = merged.merge(frame, on="ts_eastern", how="outer")

    merged = _normalize_frame(merged)
    merged["source_file"] = str(day_dir)
    return merged[READING_COLUMNS]
