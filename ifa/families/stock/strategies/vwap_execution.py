"""Intraday VWAP reclaim and volume-profile support strategies."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class VWAPExecutionProfile:
    available: bool
    reason: str
    sample_count: int
    reclaim_days: int
    reclaim_rate: float | None
    latest_close_vs_vwap_pct: float | None
    latest_reclaimed: bool | None
    volume_profile_support_score: float
    vwap_reclaim_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_vwap_execution_profile(intraday_bars: pd.DataFrame | None, *, params: dict[str, Any]) -> VWAPExecutionProfile:
    """Score cost-zone support and VWAP reclaim from local intraday bars."""
    if not params.get("enabled", True):
        return _missing("vwap execution disabled")
    df = _prepare(intraday_bars)
    min_rows = int(params.get("min_rows", 40))
    if len(df) < min_rows:
        return _missing(f"分钟线 {len(df)} 条，低于 VWAP 执行画像底线 {min_rows} 条。")
    day_col = _day_column(df)
    if day_col is None:
        return _missing("分钟线缺少 trade_time/trade_date。")
    day_stats = []
    for _, day in df.groupby(day_col, sort=True):
        stat = _day_stat(day)
        if stat:
            day_stats.append(stat)
    min_days = int(params.get("min_days", 3))
    if len(day_stats) < min_days:
        return _missing(f"有效分钟交易日 {len(day_stats)} 天，低于 {min_days} 天。")
    latest = day_stats[-1]
    reclaim_days = sum(1 for row in day_stats if row["reclaimed"])
    reclaim_rate = reclaim_days / len(day_stats)
    latest_close_vs_vwap = latest["close_vs_vwap_pct"]
    support_score = _volume_profile_support_score(latest, params)
    reclaim_score = _vwap_reclaim_score(latest, reclaim_rate, params)
    return VWAPExecutionProfile(
        available=True,
        reason="已完成分钟 VWAP 收复和成交密集支撑评分。",
        sample_count=len(day_stats),
        reclaim_days=reclaim_days,
        reclaim_rate=round(reclaim_rate, 4),
        latest_close_vs_vwap_pct=round(latest_close_vs_vwap, 4),
        latest_reclaimed=bool(latest["reclaimed"]),
        volume_profile_support_score=round(support_score, 4),
        vwap_reclaim_score=round(reclaim_score, 4),
    )


def _prepare(intraday_bars: pd.DataFrame | None) -> pd.DataFrame:
    if intraday_bars is None or intraday_bars.empty:
        return pd.DataFrame()
    required = {"high", "low", "close"}
    if not required.issubset(intraday_bars.columns):
        return pd.DataFrame()
    df = intraday_bars.copy()
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["high", "low", "close"]).reset_index(drop=True)


def _day_column(df: pd.DataFrame) -> str | None:
    for col in ["trade_time", "datetime", "trade_date"]:
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce")
            if parsed.notna().any():
                df[col] = parsed.dt.date
                return col
    return None


def _day_stat(day: pd.DataFrame) -> dict[str, Any] | None:
    if day.empty:
        return None
    volume = _volume_proxy(day)
    total_volume = float(volume.sum())
    if total_volume <= 0:
        return None
    typical = (day["high"] + day["low"] + day["close"]) / 3.0
    vwap = float((typical * volume).sum() / total_volume)
    close = float(day.iloc[-1]["close"])
    low = float(day["low"].min())
    high = float(day["high"].max())
    if close <= 0 or vwap <= 0 or high <= low:
        return None
    below_vwap = bool((day["low"] <= vwap).any())
    reclaimed = below_vwap and close >= vwap
    close_loc = (close - low) / (high - low)
    lower_share = float(volume.loc[typical <= close].sum() / total_volume)
    upper_share = float(volume.loc[typical > close].sum() / total_volume)
    return {
        "vwap": vwap,
        "close": close,
        "close_vs_vwap_pct": (close / vwap - 1.0) * 100.0,
        "reclaimed": reclaimed,
        "close_loc": close_loc,
        "lower_volume_share": lower_share,
        "upper_volume_share": upper_share,
    }


def _volume_profile_support_score(stat: dict[str, Any], params: dict[str, Any]) -> float:
    lower_share = float(stat["lower_volume_share"])
    upper_share = float(stat["upper_volume_share"])
    close_loc = float(stat["close_loc"])
    close_vs_vwap = float(stat["close_vs_vwap_pct"])
    score = 0.24 * (lower_share - upper_share) + 0.14 * (close_loc - 0.5)
    score += 0.08 * _clip(close_vs_vwap / max(float(params.get("close_vwap_scale_pct", 4.0)), 1e-6), -1.0, 1.0)
    return _clip(score, -0.30, 0.34)


def _vwap_reclaim_score(stat: dict[str, Any], reclaim_rate: float, params: dict[str, Any]) -> float:
    close_vs_vwap = float(stat["close_vs_vwap_pct"])
    score = 0.18 * (reclaim_rate - 0.5)
    score += 0.22 * _clip(close_vs_vwap / max(float(params.get("close_vwap_scale_pct", 4.0)), 1e-6), -1.0, 1.0)
    if stat["reclaimed"]:
        score += 0.08
    return _clip(score, -0.30, 0.34)


def _volume_proxy(df: pd.DataFrame) -> pd.Series:
    if "vol" in df.columns and df["vol"].notna().any():
        return df["vol"].fillna(0.0).clip(lower=0.0)
    if "amount" in df.columns and df["amount"].notna().any():
        return df["amount"].fillna(0.0).clip(lower=0.0)
    return pd.Series([1.0] * len(df), index=df.index)


def _clip(value: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0
    return max(low, min(high, value))


def _missing(reason: str) -> VWAPExecutionProfile:
    return VWAPExecutionProfile(
        available=False,
        reason=reason,
        sample_count=0,
        reclaim_days=0,
        reclaim_rate=None,
        latest_close_vs_vwap_pct=None,
        latest_reclaimed=None,
        volume_profile_support_score=0.0,
        vwap_reclaim_score=0.0,
    )
