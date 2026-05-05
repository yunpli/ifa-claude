"""Price-action statistical profiles for Stock Edge."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PriceActionProfile:
    available: bool
    reason: str
    sample_count: int
    trend_quality_score: float
    trend_slope_20d_pct: float | None
    trend_r2: float | None
    candle_reversal_score: float
    latest_close_location: float | None
    lower_shadow_ratio: float | None
    upper_shadow_ratio: float | None
    volume_price_divergence_score: float
    amount_trend_10d_pct: float | None
    price_trend_10d_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_price_action_profile(daily_bars: pd.DataFrame, *, params: dict[str, Any]) -> PriceActionProfile:
    """Build continuous price-action scores from daily OHLCV bars."""
    if not params.get("enabled", True):
        return _missing("price action disabled")
    df = _prepare(daily_bars)
    min_rows = int(params.get("min_rows", 40))
    if len(df) < min_rows:
        return _missing(f"日线样本 {len(df)} 根，低于 price-action 底线 {min_rows} 根。")
    trend_score, slope_20d, r2 = _trend_quality(df, params)
    candle_score, close_loc, lower_shadow, upper_shadow = _candle_reversal(df, params)
    divergence_score, amount_trend, price_trend = _volume_price_divergence(df, params)
    return PriceActionProfile(
        available=True,
        reason="已完成日线趋势质量、K线反转和量价背离画像。",
        sample_count=len(df),
        trend_quality_score=round(trend_score, 4),
        trend_slope_20d_pct=round(slope_20d, 4) if slope_20d is not None else None,
        trend_r2=round(r2, 4) if r2 is not None else None,
        candle_reversal_score=round(candle_score, 4),
        latest_close_location=round(close_loc, 4) if close_loc is not None else None,
        lower_shadow_ratio=round(lower_shadow, 4) if lower_shadow is not None else None,
        upper_shadow_ratio=round(upper_shadow, 4) if upper_shadow is not None else None,
        volume_price_divergence_score=round(divergence_score, 4),
        amount_trend_10d_pct=round(amount_trend, 4) if amount_trend is not None else None,
        price_trend_10d_pct=round(price_trend, 4) if price_trend is not None else None,
    )


def _prepare(daily_bars: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "open", "high", "low", "close"}
    if daily_bars.empty or not required.issubset(daily_bars.columns):
        return pd.DataFrame()
    df = daily_bars.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    for col in ["open", "high", "low", "close", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["trade_date", "open", "high", "low", "close"])
    return df.sort_values("trade_date").reset_index(drop=True)


def _trend_quality(df: pd.DataFrame, params: dict[str, Any]) -> tuple[float, float | None, float | None]:
    lookback = int(params.get("trend_lookback", 60))
    tail = df.tail(max(10, min(len(df), lookback))).copy()
    close = tail["close"].astype(float)
    if len(close) < 10 or (close <= 0).any():
        return 0.0, None, None
    y = close.map(math.log).to_list()
    x = list(range(len(y)))
    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    denom = sum((v - x_mean) ** 2 for v in x)
    if denom <= 0:
        return 0.0, None, None
    slope = sum((xv - x_mean) * (yv - y_mean) for xv, yv in zip(x, y, strict=True)) / denom
    fitted = [y_mean + slope * (xv - x_mean) for xv in x]
    ss_tot = sum((yv - y_mean) ** 2 for yv in y)
    ss_res = sum((yv - fv) ** 2 for yv, fv in zip(y, fitted, strict=True))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    slope_20d_pct = (math.exp(slope * 20.0) - 1.0) * 100.0
    good_slope = float(params.get("good_trend_slope_20d_pct", 12.0))
    bad_slope = float(params.get("bad_trend_slope_20d_pct", -8.0))
    overheat = float(params.get("overheat_slope_20d_pct", 35.0))
    positive = 0.34 * _tanh_scaled(slope_20d_pct, max(good_slope, 1e-6)) * max(r2, 0.0)
    weak = 0.20 * _sigmoid((bad_slope - slope_20d_pct) / 4.0)
    hot = 0.16 * _sigmoid((slope_20d_pct - overheat) / 5.0)
    return _clip(positive - weak - hot, -0.30, 0.36), slope_20d_pct, r2


def _candle_reversal(df: pd.DataFrame, params: dict[str, Any]) -> tuple[float, float | None, float | None, float | None]:
    latest = df.iloc[-1]
    high = float(latest["high"])
    low = float(latest["low"])
    open_ = float(latest["open"])
    close = float(latest["close"])
    span = high - low
    if span <= 0:
        return 0.0, None, None, None
    close_loc = (close - low) / span
    lower_shadow = (min(open_, close) - low) / span
    upper_shadow = (high - max(open_, close)) / span
    recent = df.tail(max(6, int(params.get("reversal_context_days", 20))))
    high20 = float(recent["high"].max())
    low20 = float(recent["low"].min())
    drawdown = close / high20 - 1.0 if high20 > 0 else 0.0
    bounce = close / low20 - 1.0 if low20 > 0 else 0.0
    bullish_tail = lower_shadow * close_loc * _sigmoid((abs(drawdown) * 100.0 - 6.0) / 4.0)
    bearish_tail = upper_shadow * (1.0 - close_loc) * _sigmoid((bounce * 100.0 - 15.0) / 5.0)
    score = float(params.get("candle_amplitude", 0.42)) * (bullish_tail - bearish_tail)
    return _clip(score, -0.28, 0.30), close_loc, lower_shadow, upper_shadow


def _volume_price_divergence(df: pd.DataFrame, params: dict[str, Any]) -> tuple[float, float | None, float | None]:
    if "amount" not in df.columns:
        return 0.0, None, None
    lookback = int(params.get("divergence_lookback", 10))
    tail = df.tail(max(lookback + 1, 5))
    amount = tail["amount"].astype(float)
    close = tail["close"].astype(float)
    if len(tail) < 5 or amount.iloc[0] <= 0 or close.iloc[0] <= 0:
        return 0.0, None, None
    amount_trend = (float(amount.tail(3).mean()) / max(float(amount.head(3).mean()), 1e-9) - 1.0) * 100.0
    price_trend = (float(close.iloc[-1]) / float(close.iloc[0]) - 1.0) * 100.0
    confirm = _tanh_scaled(price_trend, 8.0) * _tanh_scaled(amount_trend, 40.0)
    divergence_penalty = 0.0
    if price_trend > 8.0 and amount_trend < -20.0:
        divergence_penalty = 0.22 * _sigmoid((price_trend - 8.0) / 4.0) * _sigmoid((-amount_trend - 20.0) / 10.0)
    if price_trend < -6.0 and amount_trend > 20.0:
        divergence_penalty += 0.18 * _sigmoid((-price_trend - 6.0) / 4.0) * _sigmoid((amount_trend - 20.0) / 10.0)
    score = 0.22 * confirm - divergence_penalty
    return _clip(score, -0.30, 0.28), amount_trend, price_trend


def _sigmoid(x: float) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        x = 0.0
    if not math.isfinite(x):
        x = 0.0
    if x >= 40:
        return 1.0
    if x <= -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _tanh_scaled(value: float, scale: float) -> float:
    try:
        value = float(value)
        scale = float(scale)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value) or not math.isfinite(scale):
        return 0.0
    return math.tanh(value / max(scale, 1e-6))


def _clip(value: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0
    return max(low, min(high, value))


def _missing(reason: str) -> PriceActionProfile:
    return PriceActionProfile(
        available=False,
        reason=reason,
        sample_count=0,
        trend_quality_score=0.0,
        trend_slope_20d_pct=None,
        trend_r2=None,
        candle_reversal_score=0.0,
        latest_close_location=None,
        lower_shadow_ratio=None,
        upper_shadow_ratio=None,
        volume_price_divergence_score=0.0,
        amount_trend_10d_pct=None,
        price_trend_10d_pct=None,
    )
