"""T+0 uplift replay for A-share base-position execution."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class T0UpliftProfile:
    available: bool
    reason: str
    source: str
    sample_count: int
    avg_intraday_range_pct: float | None
    avg_reversal_capture_pct: float | None
    success_rate: float | None
    avg_uplift_pct: float | None
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_t0_uplift_profile(
    intraday_bars: pd.DataFrame | None,
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
) -> T0UpliftProfile:
    """Estimate whether high-sell/low-buy base-position T+0 can add value."""
    if not params.get("enabled", True):
        return _missing("t0 uplift disabled")
    intraday = _prepare_intraday(intraday_bars)
    if not intraday.empty and _date_column(intraday):
        profile = _from_intraday(intraday, params)
        if profile.available:
            return profile
    daily = _prepare_daily(daily_bars)
    if not daily.empty:
        return _from_daily_proxy(daily, params)
    return _missing("分钟线和日线都不足，无法评估 T+0 增益。")


def _from_intraday(df: pd.DataFrame, params: dict[str, Any]) -> T0UpliftProfile:
    date_col = _date_column(df)
    if not date_col:
        return _missing("分钟线缺少日期字段。")
    grouped = df.groupby(date_col, sort=True)
    min_days = int(params.get("min_intraday_days", 3))
    rows = []
    for _, day in grouped:
        if len(day) < int(params.get("min_bars_per_day", 20)):
            continue
        day = day.sort_values(date_col).copy()
        open_ = float(day.iloc[0]["open"]) if "open" in day.columns else float(day.iloc[0]["close"])
        close = float(day.iloc[-1]["close"])
        high = float(day["high"].max())
        low = float(day["low"].min())
        if open_ <= 0 or low <= 0:
            continue
        range_pct = (high / low - 1.0) * 100.0
        high_pos = int(day["high"].idxmax())
        low_pos = int(day["low"].idxmin())
        high_before_low = high_pos < low_pos
        close_retrace = max(0.0, (high / close - 1.0) * 100.0) if close > 0 else 0.0
        low_reclaim = max(0.0, (close / low - 1.0) * 100.0)
        if high_before_low:
            capture = min(range_pct, close_retrace + low_reclaim)
        else:
            capture = min(range_pct, 0.45 * range_pct)
        rows.append((range_pct, capture, high_before_low or range_pct >= float(params.get("success_range_pct", 3.0))))
    if len(rows) < min_days:
        return _missing(f"有效分钟交易日 {len(rows)} 天，低于 {min_days} 天。")
    return _profile_from_rows(rows, params=params, source="duckdb.intraday_5min", reason="已用分钟线评估底仓 T+0 增益。")


def _from_daily_proxy(df: pd.DataFrame, params: dict[str, Any]) -> T0UpliftProfile:
    min_days = int(params.get("min_daily_days", 20))
    tail = df.tail(max(min_days, int(params.get("daily_window", 30))))
    rows = []
    for _, row in tail.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        open_ = float(row["open"])
        close = float(row["close"])
        if low <= 0 or open_ <= 0:
            continue
        range_pct = (high / low - 1.0) * 100.0
        upper_retrace = max(0.0, (high / max(close, 1e-9) - 1.0) * 100.0)
        lower_reclaim = max(0.0, (close / low - 1.0) * 100.0)
        body_chop = 1.0 - min(abs(close / open_ - 1.0) * 100.0 / max(range_pct, 1e-9), 1.0)
        capture = min(range_pct, (upper_retrace + lower_reclaim) * (0.45 + 0.35 * body_chop))
        rows.append((range_pct, capture, capture >= float(params.get("success_capture_pct", 1.2))))
    if len(rows) < min_days:
        return _missing(f"日线代理有效样本 {len(rows)} 天，低于 {min_days} 天。")
    return _profile_from_rows(rows, params=params, source="smartmoney.raw_daily", reason="分钟线不足，已用日线振幅代理评估 T+0 增益。")


def _profile_from_rows(rows: list[tuple[float, float, bool]], *, params: dict[str, Any], source: str, reason: str) -> T0UpliftProfile:
    ranges = [row[0] for row in rows]
    captures = [row[1] for row in rows]
    success = [row[2] for row in rows]
    avg_range = sum(ranges) / len(ranges)
    avg_capture = sum(captures) / len(captures)
    success_rate = sum(1 for item in success if item) / len(success)
    cost = float(params.get("roundtrip_cost_pct", 0.18))
    avg_uplift = max(-cost, avg_capture - cost)
    target = max(float(params.get("good_uplift_pct", 1.5)), 1e-6)
    range_target = max(float(params.get("good_range_pct", 3.5)), 1e-6)
    score = 0.34 * _clip(avg_uplift / target, -1.0, 1.5) + 0.20 * _clip((avg_range - range_target) / range_target, -1.0, 1.0) + 0.20 * (success_rate - 0.5)
    score = _clip(score, -0.35, 0.42)
    return T0UpliftProfile(
        available=True,
        reason=reason,
        source=source,
        sample_count=len(rows),
        avg_intraday_range_pct=round(avg_range, 4),
        avg_reversal_capture_pct=round(avg_capture, 4),
        success_rate=round(success_rate, 4),
        avg_uplift_pct=round(avg_uplift, 4),
        score=round(score, 4),
    )


def _prepare_intraday(intraday_bars: pd.DataFrame | None) -> pd.DataFrame:
    if intraday_bars is None or intraday_bars.empty:
        return pd.DataFrame()
    required = {"high", "low", "close"}
    if not required.issubset(intraday_bars.columns):
        return pd.DataFrame()
    df = intraday_bars.copy()
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["high", "low", "close"]).reset_index(drop=True)


def _prepare_daily(daily_bars: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close"}
    if daily_bars.empty or not required.issubset(daily_bars.columns):
        return pd.DataFrame()
    df = daily_bars.copy()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).sort_values("trade_date").reset_index(drop=True)


def _date_column(df: pd.DataFrame) -> str | None:
    for col in ["trade_date", "trade_time", "datetime"]:
        if col in df.columns:
            values = pd.to_datetime(df[col], errors="coerce")
            df[col] = values.dt.date
            return col
    return None


def _clip(value: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0
    return max(low, min(high, value))


def _missing(reason: str) -> T0UpliftProfile:
    return T0UpliftProfile(
        available=False,
        reason=reason,
        source="",
        sample_count=0,
        avg_intraday_range_pct=None,
        avg_reversal_capture_pct=None,
        success_rate=None,
        avg_uplift_pct=None,
        score=0.0,
    )
