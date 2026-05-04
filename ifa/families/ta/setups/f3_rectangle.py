"""F3 rectangle — horizontal range, breakout.

Triggers (all):
  · last 15 days form a rectangle:
        box_high = max(highs[-15:-1]); box_low = min(lows[-15:-1])
        (box_high - box_low) / box_high <= 8%                  — tight box
  · today's close > box_high                                    — upside break
  · MA20 > MA60                                                  — uptrend backdrop

Score:
  base 0.5
  + 0.2 if regime == "trend_continuation"
  + 0.2 if (box_high - box_low) / box_high <= 5%                — very tight
  + 0.1 if volume_ratio >= 1.5
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def F3_RECTANGLE(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or len(ctx.highs) < 16 or len(ctx.lows) < 16):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    box_high = max(ctx.highs[-15:-1])
    box_low = min(ctx.lows[-15:-1])
    if box_high <= 0:
        return None
    box_range_pct = (box_high - box_low) / box_high
    if box_range_pct > 0.08:
        return None

    if ctx.close_today <= box_high:
        return None

    triggers = ["uptrend_stack", "rectangle_box", "upside_breakout"]
    score = 0.5
    if ctx.regime == "trend_continuation":
        score += 0.2
        triggers.append("regime_tailwind")
    if box_range_pct <= 0.05:
        score += 0.2
        triggers.append("very_tight_box")
    if ctx.volume_ratio is not None and ctx.volume_ratio >= 1.5:
        score += 0.1
        triggers.append("volume_breakout")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="F3_RECTANGLE",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "box_high": box_high,
            "box_low": box_low,
            "box_range_pct": box_range_pct * 100,
        },
    )
