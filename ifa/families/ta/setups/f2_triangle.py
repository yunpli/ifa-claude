"""F2 triangle — converging highs and lows, breakout.

Heuristic: split last 20 days into early (first 10) and late (last 10) halves.
Range should contract: late_range < 0.6 * early_range. Today closes above the
late half's high (upside breakout) or below late half's low (we only fire on
upside here — let downside be a separate signal if needed).

Triggers (all):
  · MA20 > MA60                                                — uptrend
  · early_range = max(highs[-20:-10]) - min(lows[-20:-10])
  · late_range  = max(highs[-10:])    - min(lows[-10:])
  · late_range / early_range < 0.6                             — converging
  · ctx.close_today > max(highs[-10:-1])                       — upside breakout

Score:
  base 0.5
  + 0.2 if regime in {trend_continuation, early_risk_on}
  + 0.2 if late_range / early_range < 0.4   — strong contraction
  + 0.1 if volume_ratio >= 1.5
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def F2_TRIANGLE(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or len(ctx.highs) < 21 or len(ctx.lows) < 21):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    contraction_max = setup_param("F2_TRIANGLE", "contraction_max", 0.6)

    early_range = max(ctx.highs[-20:-10]) - min(ctx.lows[-20:-10])
    late_range = max(ctx.highs[-10:]) - min(ctx.lows[-10:])
    if early_range <= 0:
        return None
    contraction = late_range / early_range
    if contraction >= contraction_max:
        return None

    if ctx.close_today <= max(ctx.highs[-10:-1]):
        return None

    triggers = ["uptrend_stack", "range_contracting", "upside_breakout"]
    score = 0.5

    # Continuous: 收敛强度 — 0.6→0, 0.1→full
    contraction_strength = max(0.0, min(1.0, (0.6 - contraction) / 0.5))
    score += 0.20 * contraction_strength
    if contraction_strength >= 0.4:
        triggers.append("strong_contraction")

    # Continuous: 量能突破
    if ctx.volume_ratio is not None:
        vol_strength = max(0.0, min(1.0, (ctx.volume_ratio - 1.0) / 1.5))
        score += 0.10 * vol_strength
        if vol_strength >= 0.3:
            triggers.append("volume_breakout")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="F2_TRIANGLE",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "early_range": early_range,
            "late_range": late_range,
            "contraction_ratio": contraction,
        },
    )
