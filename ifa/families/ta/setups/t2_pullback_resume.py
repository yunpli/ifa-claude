"""T2 pullback-resume — uptrend pullback to MA20, resuming up.

Triggers (all):
  · MA20 > MA60                            — established uptrend
  · min(low[-5:]) <= 1.02 * ma20           — touched MA20 within last 5 days
  · close > ma_qfq_5                       — back above MA5 today
  · close >= close[-2]                     — closing higher than yesterday

Score:
  base 0.5
  + 0.2 if regime == "trend_continuation"
  + 0.2 if min(low[-5:]) <= ma20 (actual touch, not just near)
  + 0.1 if rsi_qfq_6 is not None and 30 <= rsi_qfq_6 <= 60   (oversold-ish)
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def T2_PULLBACK_RESUME(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_5 is None
            or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None):
        return None
    if len(ctx.closes) < 6 or len(ctx.lows) < 5:
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    touch_max_x = setup_param("T2_PULLBACK_RESUME", "ma20_touch_max_x", 1.02)
    rsi_lower = setup_param("T2_PULLBACK_RESUME", "rsi_lower", 30)
    rsi_upper = setup_param("T2_PULLBACK_RESUME", "rsi_upper", 60)

    recent_low = min(ctx.lows[-5:])
    if recent_low > touch_max_x * ctx.ma_qfq_20:
        return None
    if ctx.close_today <= ctx.ma_qfq_5:
        return None
    if ctx.close_today < ctx.closes[-2]:
        return None

    triggers = ["uptrend_stack", "touched_ma20", "back_above_ma5"]
    score = 0.5

    touch_strength = max(0.0, min(1.0,
        (touch_max_x - recent_low / ctx.ma_qfq_20) / max(touch_max_x - 0.98, 1e-6)))
    score += 0.20 * touch_strength
    if touch_strength >= 0.5:
        triggers.append("actual_ma20_touch")

    if ctx.rsi_qfq_6 is not None:
        rsi_mid = (rsi_lower + rsi_upper) / 2
        rsi_strength = max(0.0, min(1.0,
            1 - abs(ctx.rsi_qfq_6 - rsi_mid) / max((rsi_upper - rsi_lower) / 2, 1)))
        score += 0.10 * rsi_strength
        if rsi_strength >= 0.5:
            triggers.append("rsi_balanced")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="T2_PULLBACK_RESUME",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "ma5": ctx.ma_qfq_5,
            "ma20": ctx.ma_qfq_20,
            "ma60": ctx.ma_qfq_60,
            "recent_low_5d": recent_low,
            "rsi6": ctx.rsi_qfq_6,
        },
    )
