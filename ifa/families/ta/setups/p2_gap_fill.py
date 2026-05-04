"""P2 gap fill — pullback fills a recent up-gap, holds.

Logic: scan last 20 days for an up-gap day (today's low > prev day's high);
if today's price came back into that gap range and held above the gap bottom
(the prior high), candidate fires.

Triggers (all):
  · MA20 > MA60                                  — uptrend
  · exists day i in last 20: low[i] > high[i-1] (up-gap), gap_top = low[i]
  · today_low <= gap_top                         — pulled into gap
  · close >= prev_day_high (gap bottom)          — held the gap bottom

Score:
  base 0.5
  + 0.2 if regime == "trend_continuation"
  + 0.2 if close >= gap_top                      — full reclaim
  + 0.1 if today's bar bounced (close > today_low + 0.5*(high-low))
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def P2_GAP_FILL(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or len(ctx.highs) < 21 or len(ctx.lows) < 21):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    gap_top = None        # = low[i] of the gap day
    gap_bottom = None     # = high[i-1] (the prior day's high)
    for i in range(-20, 0):
        if i == -1:
            break
        if ctx.lows[i] > ctx.highs[i - 1]:
            gap_top = ctx.lows[i]
            gap_bottom = ctx.highs[i - 1]
    if gap_top is None or gap_bottom is None:
        return None

    today_low = ctx.lows[-1]
    today_high = ctx.highs[-1]
    if today_low > gap_top:                  # haven't filled
        return None
    if ctx.close_today < gap_bottom:         # broke through gap, no defense
        return None

    triggers = ["uptrend_stack", "gap_filled", "above_gap_bottom"]
    score = 0.5

    if ctx.regime == "trend_continuation":
        score += 0.2
        triggers.append("regime_tailwind")
    if ctx.close_today >= gap_top:
        score += 0.2
        triggers.append("full_reclaim")
    if today_high > today_low and ctx.close_today > today_low + 0.5 * (today_high - today_low):
        score += 0.1
        triggers.append("upper_half_close")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="P2_GAP_FILL",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "gap_top": gap_top,
            "gap_bottom": gap_bottom,
            "today_low": today_low,
            "today_high": today_high,
        },
    )
