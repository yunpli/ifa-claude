"""Intraday VWAP and volume-profile features for Stock Edge."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class IntradayProfile:
    available: bool
    reason: str
    rows: int
    vwap: float | None
    close: float | None
    close_vs_vwap_pct: float | None
    support_price: float | None
    pressure_price: float | None
    lower_volume_share: float | None
    upper_volume_share: float | None
    concentration: float | None
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_intraday_profile(intraday_bars: pd.DataFrame | None, *, params: dict[str, Any]) -> IntradayProfile:
    """Build VWAP/volume-profile support evidence from local intraday bars."""
    if not params.get("enabled", True):
        return _missing("intraday profile disabled")
    if intraday_bars is None or intraday_bars.empty:
        return _missing("没有本地分钟线。")
    required = {"high", "low", "close"}
    if not required.issubset(intraday_bars.columns):
        return _missing("分钟线缺少 high/low/close 字段。")
    df = intraday_bars.copy()
    for col in ["high", "low", "close", "vol", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["high", "low", "close"])
    min_rows = int(params.get("min_rows", 40))
    if len(df) < min_rows:
        return _missing(f"分钟线只有 {len(df)} 条，低于 {min_rows} 条。")
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    volume = _volume_proxy(df)
    total_volume = float(volume.sum())
    if total_volume <= 0:
        return _missing("分钟线成交量不可用。")
    vwap = float((typical * volume).sum() / total_volume)
    close = float(df.iloc[-1]["close"])
    close_vs_vwap_pct = (close / vwap - 1.0) * 100.0 if vwap > 0 else 0.0
    support_band = float(params.get("vwap_support_band_pct", 3.0)) / 100.0
    pressure_band = float(params.get("vwap_pressure_band_pct", 4.0)) / 100.0
    lower = df[typical <= close * (1.0 - support_band)]
    upper = df[typical >= close * (1.0 + pressure_band)]
    support_price = _weighted_price(lower, volume.loc[lower.index])
    pressure_price = _weighted_price(upper, volume.loc[upper.index])
    lower_share = float(volume.loc[lower.index].sum() / total_volume) if not lower.empty else 0.0
    upper_share = float(volume.loc[upper.index].sum() / total_volume) if not upper.empty else 0.0
    concentration = _concentration(typical, volume)
    score = _score_profile(
        close_vs_vwap_pct=close_vs_vwap_pct,
        lower_share=lower_share,
        upper_share=upper_share,
        concentration=concentration,
        params=params,
    )
    return IntradayProfile(
        available=True,
        reason="已完成分钟 VWAP / 成交密集区画像。",
        rows=len(df),
        vwap=round(vwap, 4),
        close=round(close, 4),
        close_vs_vwap_pct=round(close_vs_vwap_pct, 4),
        support_price=round(support_price, 4) if support_price else None,
        pressure_price=round(pressure_price, 4) if pressure_price else None,
        lower_volume_share=round(lower_share, 4),
        upper_volume_share=round(upper_share, 4),
        concentration=round(concentration, 4),
        score=round(score, 4),
    )


def _volume_proxy(df: pd.DataFrame) -> pd.Series:
    if "vol" in df.columns and df["vol"].notna().any():
        return df["vol"].fillna(0.0).clip(lower=0.0)
    if "amount" in df.columns and df["amount"].notna().any():
        return df["amount"].fillna(0.0).clip(lower=0.0)
    return pd.Series([1.0] * len(df), index=df.index)


def _weighted_price(rows: pd.DataFrame, volume: pd.Series) -> float | None:
    if rows.empty:
        return None
    typical = (rows["high"] + rows["low"] + rows["close"]) / 3.0
    total = float(volume.sum())
    if total <= 0:
        return float(typical.mean())
    return float((typical * volume).sum() / total)


def _concentration(price: pd.Series, volume: pd.Series) -> float:
    total = float(volume.sum())
    if total <= 0 or price.empty:
        return 0.0
    buckets = pd.qcut(price.rank(method="first"), q=min(10, max(2, len(price) // 10)), duplicates="drop")
    grouped = volume.groupby(buckets, observed=False).sum()
    return float(grouped.max() / total) if not grouped.empty else 0.0


def _score_profile(
    *,
    close_vs_vwap_pct: float,
    lower_share: float,
    upper_share: float,
    concentration: float,
    params: dict[str, Any],
) -> float:
    scale = max(float(params.get("close_vwap_scale_pct", 4.0)), 1e-6)
    good_conc = float(params.get("concentration_good", 0.42))
    crowded = float(params.get("concentration_crowded", 0.70))
    score = 0.20 * max(-1.0, min(1.0, close_vs_vwap_pct / scale))
    score += 0.20 * (lower_share - upper_share)
    score += 0.12 * max(0.0, min(1.0, concentration / max(good_conc, 1e-6)))
    score -= 0.20 * max(0.0, (concentration - crowded) / max(1.0 - crowded, 1e-6))
    return max(-0.35, min(0.35, score))


def _missing(reason: str) -> IntradayProfile:
    return IntradayProfile(
        available=False,
        reason=reason,
        rows=0,
        vwap=None,
        close=None,
        close_vs_vwap_pct=None,
        support_price=None,
        pressure_price=None,
        lower_volume_share=None,
        upper_volume_share=None,
        concentration=None,
        score=0.0,
    )
