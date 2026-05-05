"""Liquidity and slippage risk model for Stock Edge."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class LiquiditySlippageProfile:
    available: bool
    reason: str
    avg_amount_yuan: float | None
    estimated_slippage_bps: float | None
    capacity_score: float
    turnover_risk: float
    volatility_risk: float
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_liquidity_slippage_profile(
    daily_bars: pd.DataFrame,
    daily_basic: pd.DataFrame | None,
    *,
    params: dict[str, Any],
) -> LiquiditySlippageProfile:
    """Estimate capacity/slippage pressure from local daily data."""
    if not params.get("enabled", True):
        return _missing("liquidity slippage disabled")
    if daily_bars.empty or "amount" not in daily_bars.columns:
        return _missing("缺少日成交额，无法估算滑点。")
    window = int(params.get("avg_amount_window", 20))
    amount = pd.to_numeric(daily_bars.sort_values("trade_date")["amount"], errors="coerce").dropna().tail(window)
    if amount.empty:
        return _missing("日成交额不可用。")
    # TuShare raw_daily.amount is usually in thousand CNY. If values already look
    # like yuan, the scale still remains conservative.
    avg_amount_yuan = float(amount.mean()) * 1000.0
    good = float(params.get("good_avg_amount_yuan", 300_000_000))
    minimum = float(params.get("min_avg_amount_yuan", 50_000_000))
    participation = float(params.get("participation_rate", 0.01))
    base_bps = float(params.get("base_slippage_bps", 8.0))
    scale_bps = float(params.get("slippage_scale_bps", 35.0))
    capacity_score = _clip(math.log1p(avg_amount_yuan / max(minimum, 1.0)) / math.log1p(good / max(minimum, 1.0)), 0.0, 1.25)
    estimated_slippage = base_bps + scale_bps * math.sqrt(max(participation, 0.0) / max(avg_amount_yuan / good, 0.05))
    turnover_risk = _turnover_risk(daily_basic, params)
    volatility_risk = _volatility_risk(daily_bars, params)
    score = 0.28 * (capacity_score - 0.65) - 0.22 * turnover_risk - 0.18 * volatility_risk - 0.10 * _clip((estimated_slippage - 20.0) / 60.0, 0.0, 1.0)
    return LiquiditySlippageProfile(
        available=True,
        reason="已完成流动性/滑点风险估算。",
        avg_amount_yuan=round(avg_amount_yuan, 2),
        estimated_slippage_bps=round(estimated_slippage, 2),
        capacity_score=round(capacity_score, 4),
        turnover_risk=round(turnover_risk, 4),
        volatility_risk=round(volatility_risk, 4),
        score=round(_clip(score, -0.40, 0.28), 4),
    )


def _turnover_risk(daily_basic: pd.DataFrame | None, params: dict[str, Any]) -> float:
    if daily_basic is None or not hasattr(daily_basic, "empty") or daily_basic.empty:
        return 0.25
    col = "turnover_rate_f" if "turnover_rate_f" in daily_basic.columns else "turnover_rate"
    if col not in daily_basic.columns:
        return 0.25
    turnover = pd.to_numeric(daily_basic[col], errors="coerce").dropna().tail(10)
    if turnover.empty:
        return 0.25
    avg = float(turnover.mean())
    low = float(params.get("turnover_low_pct", 0.5))
    high = float(params.get("turnover_high_pct", 18.0))
    low_risk = _clip((low - avg) / max(low, 1e-6), 0.0, 1.0)
    high_risk = _clip((avg - high) / max(high, 1e-6), 0.0, 1.0)
    return max(low_risk, high_risk)


def _volatility_risk(daily_bars: pd.DataFrame, params: dict[str, Any]) -> float:
    ordered = daily_bars.sort_values("trade_date")
    if not {"high", "low", "close"}.issubset(ordered.columns):
        return 0.25
    close = pd.to_numeric(ordered["close"], errors="coerce")
    high = pd.to_numeric(ordered["high"], errors="coerce")
    low = pd.to_numeric(ordered["low"], errors="coerce")
    rng = ((high - low) / close.replace(0, pd.NA)).dropna().tail(20)
    if rng.empty:
        return 0.25
    scale = float(params.get("volatility_penalty_scale_pct", 8.0)) / 100.0
    return _clip(float(rng.mean()) / max(scale, 1e-6), 0.0, 1.5)


def _missing(reason: str) -> LiquiditySlippageProfile:
    return LiquiditySlippageProfile(
        available=False,
        reason=reason,
        avg_amount_yuan=None,
        estimated_slippage_bps=None,
        capacity_score=0.0,
        turnover_risk=0.0,
        volatility_risk=0.0,
        score=0.0,
    )


def _clip(value: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0
    return max(low, min(high, value))
