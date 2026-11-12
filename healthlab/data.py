"""Shared read-only access to lab.duckdb and the daily frame."""
import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(os.environ.get("HEALTHLAB_DATA") or Path(__file__).resolve().parent.parent / "data").resolve()


def connect(path=None):
    return duckdb.connect(str(path or ROOT / "lab.duckdb"), read_only=True)


def load_daily(con):
    d = con.execute("SELECT * FROM daily ORDER BY date").df()
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index("date")
    # sleep onset on one axis: 25.5 means 1:30 am after the evening before
    d["bed_clock_adj"] = d.bed_clock.where(d.bed_clock >= 12, d.bed_clock + 24)
    for c in d.columns:
        if c not in ("tz", "tz_conf", "sleep_source", "weekend", "worn_day"):
            d[c] = pd.to_numeric(d[c], errors="coerce").astype(float)  # NaN, never pd.NA
    d["yr"] = d.yr.astype(int)
    d["mo"] = d.mo.astype(int)
    d["dow"] = d.dow.astype(int)
    return d


def json_safe(v):
    if isinstance(v, dict):
        return {k: json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    if isinstance(v, (np.floating, float)):
        return None if np.isnan(v) or np.isinf(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (pd.Timestamp,)):
        return str(v.date())
    return v
