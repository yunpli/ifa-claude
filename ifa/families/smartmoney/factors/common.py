"""共用数学工具 — all factor modules import from here.

Design principles:
  - All functions operate on plain Python / numpy / pandas primitives.
  - No SQLAlchemy / DB calls here; callers load data, pass series/arrays.
  - M1-safe: avoid large in-memory loads; all windows are bounded by caller.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


# ── Rolling statistics ────────────────────────────────────────────────────────

def rolling_mean(values: pd.Series | list[float], window: int) -> float:
    """Return the rolling mean of the last ``window`` observations.

    Returns NaN when fewer values than ``window`` are available.
    """
    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return float("nan")
    tail = arr[-window:]
    return float(np.nanmean(tail))


def rolling_std(values: pd.Series | list[float], window: int) -> float:
    """Return the rolling standard deviation of the last ``window`` values.

    Returns NaN when < 2 non-NaN values.
    """
    arr = np.asarray(values, dtype=float)
    tail = arr[-window:]
    valid = tail[~np.isnan(tail)]
    if len(valid) < 2:
        return float("nan")
    return float(np.std(valid, ddof=1))


# ── Percentile & z-score ──────────────────────────────────────────────────────

def percentile_rank(series: pd.Series | list[float], value: float) -> float:
    """Return the percentile rank (0.0–1.0) of ``value`` within ``series``.

    Uses "less than or equal" (CDF) definition: what fraction of the series
    is <= value.  Returns NaN if series is empty.
    """
    arr = np.asarray(series, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) == 0:
        return float("nan")
    return float(np.mean(arr <= value))


def z_score(series: pd.Series | list[float], value: float) -> float:
    """Standard z-score of ``value`` relative to ``series``.

    Returns NaN when the series std is ~0.
    """
    arr = np.asarray(series, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return float("nan")
    mu = float(np.mean(arr))
    sigma = float(np.std(arr, ddof=1))
    if sigma < 1e-12:
        return float("nan")
    return (value - mu) / sigma


# ── Winsorization ─────────────────────────────────────────────────────────────

def winsorize(
    series: pd.Series,
    lower: float = 0.02,
    upper: float = 0.98,
) -> pd.Series:
    """Clip extreme values at the given quantile boundaries.

    Operates in-place on a copy; NaN values are preserved.
    """
    lo = series.quantile(lower)
    hi = series.quantile(upper)
    return series.clip(lower=lo, upper=hi)


def winsorize_array(
    arr: np.ndarray,
    lower: float = 0.02,
    upper: float = 0.98,
) -> np.ndarray:
    """Numpy array version of winsorize; NaN-safe."""
    valid = arr[~np.isnan(arr)]
    if len(valid) == 0:
        return arr.copy()
    lo = np.nanpercentile(arr, lower * 100)
    hi = np.nanpercentile(arr, upper * 100)
    return np.clip(arr, lo, hi)


# ── Min–max normalization ─────────────────────────────────────────────────────

def minmax_normalize(series: pd.Series) -> pd.Series:
    """Map series to [0, 1].  Returns 0.5 when min == max."""
    lo, hi = series.min(), series.max()
    if hi - lo < 1e-12:
        return pd.Series(0.5, index=series.index)
    return (series - lo) / (hi - lo)


# ── DB helper: load a date-indexed scalar series from the DB ──────────────────

def load_scalar_series(
    engine: Engine,
    sql: str,
    params: dict[str, Any],
    *,
    date_col: str = "trade_date",
    value_col: str = "value",
    n_days: int = 60,
) -> pd.Series:
    """Execute ``sql`` and return a pd.Series indexed by trade_date (date).

    The query should return at most ``n_days`` rows sorted ascending by date.
    Values are cast to float; NaN replaces NULL.

    Args:
        engine:    SQLAlchemy engine.
        sql:       Raw SQL string using SQLAlchemy :param style.
        params:    Query parameters dict.
        date_col:  Column name for dates.
        value_col: Column name for values.
        n_days:    Soft cap passed to SQL; callers should embed a LIMIT /
                   date-range filter using this.
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    dates = [r[0] if isinstance(r[0], dt.date) else dt.date.fromisoformat(str(r[0]))
             for r in rows]
    values = [float(r[1]) if r[1] is not None else float("nan") for r in rows]
    return pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float).sort_index()


def load_panel_df(
    engine: Engine,
    sql: str,
    params: dict[str, Any],
    *,
    date_col: str = "trade_date",
    key_col: str = "ts_code",
) -> pd.DataFrame:
    """Load a panel (date × key → multiple value columns) from the DB.

    Returns a DataFrame with MultiIndex (date, key) or a flat DataFrame
    depending on use.  Caller pivots as needed.
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=list(rows[0]._fields))


# ── Factor normalization ──────────────────────────────────────────────────────

def cross_sectional_rank(series: pd.Series) -> pd.Series:
    """Return percentile ranks (0–1) of each element across the full series.

    Suitable for cross-sectional factor normalization within one trade date.
    NaN values map to NaN.
    """
    return series.rank(pct=True, na_option="keep")


def sigmoid(x: float | np.ndarray, *, scale: float = 1.0) -> float | np.ndarray:
    """Sigmoid function, optionally scaled: 1 / (1 + exp(-scale * x))."""
    return 1.0 / (1.0 + np.exp(-scale * np.asarray(x, dtype=float)))


# ── Consecutive count helper ──────────────────────────────────────────────────

def consecutive_positive(values: list[float | None]) -> int:
    """Count trailing consecutive positive (> 0) values.

    E.g. [1, -1, 2, 3] → 2   (last two are positive)
         [1, 2, 3]     → 3
         [-1, 0, -2]   → 0
    """
    count = 0
    for v in reversed(values):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            break
        if v > 0:
            count += 1
        else:
            break
    return count


def positive_ratio(values: list[float | None], window: int) -> float:
    """Fraction of the last ``window`` observations that are strictly > 0.

    NaN / None are counted as non-positive.
    Returns NaN when no observations.
    """
    tail = values[-window:] if len(values) >= window else values
    if not tail:
        return float("nan")
    pos = sum(1 for v in tail if v is not None and not (isinstance(v, float) and np.isnan(v)) and v > 0)
    return pos / len(tail)
