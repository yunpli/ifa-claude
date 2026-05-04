"""P3 tight consolidation after rally — coiled spring.

Triggers (all):
  · prior 20d gain (close[-1] / close[-21] - 1) >= 10%
  · last 5d range / last 5d max <= 5%        (tight box)
  · MA20 > MA60                              — uptrend backdrop
  · today's close >= min(closes[-5:])        — hasn't broken down

Score:
  base 0.5
  + 0.2 if regime == "trend_continuation"
  + 0.2 if last 5d range / last 5d max <= 3%   (very tight)
  + 0.1 if today's volume_ratio is not None and volume_ratio < 0.8 (volume drying up)
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def P3_TIGHT_CONSOLIDATION(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or len(ctx.closes) < 21 or len(ctx.highs) < 5 or len(ctx.lows) < 5):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    prior_gain = ctx.closes[-1] / ctx.closes[-21] - 1.0
    if prior_gain < 0.10:
        return None

    box_high = max(ctx.highs[-5:])
    box_low = min(ctx.lows[-5:])
    box_range_pct = (box_high - box_low) / box_high if box_high > 0 else 1.0
    if box_range_pct > 0.05:
        return None

    if ctx.close_today < min(ctx.closes[-5:]):
        return None

    triggers = ["prior_20d_gain>=10%", "tight_5d_box<=5%", "uptrend_stack"]
    score = 0.5

    # Continuous: 箱体紧密度 — 5%→0, 1%→full
    tightness = max(0.0, min(1.0, (0.05 - box_range_pct) / 0.04))
    score += 0.20 * tightness
    if tightness >= 0.5:
        triggers.append("very_tight_box")

    # Continuous: 缩量程度 — 1.0×→0, 0.4×→full
    if ctx.volume_ratio is not None:
        drying_strength = max(0.0, min(1.0, (1.0 - ctx.volume_ratio) / 0.6))
        score += 0.10 * drying_strength
        if drying_strength >= 0.3:
            triggers.append("volume_drying")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="P3_TIGHT_CONSOLIDATION",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "prior_20d_gain_pct": prior_gain * 100,
            "box_high_5d": box_high,
            "box_low_5d": box_low,
            "box_range_pct": box_range_pct * 100,
            "volume_ratio": ctx.volume_ratio,
        },
    )
