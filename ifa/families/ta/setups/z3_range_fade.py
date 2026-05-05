"""Z3 横盘 fade-rally — sell into spike inside a sideways range.

Designed for 震荡 / range_bound regime where T1/T3 (breakout-continuation) lose money.

Logic:
  · 60-day box: max-min within 25% (i.e. no clear trend, mean-reverting environment)
  · 20-day max not exceeded today (no real breakout)
  · Today's pct_chg ≥ +threshold (spike up)
  · close near 60d top quartile of the range → mean-reversion candidate

Position-tracker handles this: ATR-based -0.8 ATR pullback entry catches the fade-back.

Score (mean-reversion, NOT directional veto — feeds long pool but with
appropriate ATR offset):
  base 0.5
  + up to 0.20 = clip((today_pct - threshold) / spread, 0, 1) — bigger spike, stronger fade
  + 0.10 if RSI(6) ≥ 70 (overbought confirmation)
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def Z3_RANGE_FADE(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or len(ctx.closes) < 60
            or len(ctx.highs) < 60 or len(ctx.lows) < 60):
        return None

    box_max_pct = setup_param("Z3_RANGE_FADE", "box_max_pct", 0.25)
    today_min_pct = setup_param("Z3_RANGE_FADE", "today_min_pct", 4.0)
    near_top_quartile = setup_param("Z3_RANGE_FADE", "near_top_quartile", 0.75)
    rsi_overbought = setup_param("Z3_RANGE_FADE", "rsi_overbought", 70)

    box_high = max(ctx.highs[-60:])
    box_low = min(ctx.lows[-60:])
    if box_high <= 0:
        return None
    box_range = (box_high - box_low) / box_high
    if box_range > box_max_pct:
        return None  # Trending, not range-bound

    # Today's pct_chg from previous close
    if len(ctx.closes) < 2:
        return None
    today_pct = (ctx.close_today / ctx.closes[-2] - 1.0) * 100
    if today_pct < today_min_pct:
        return None

    # Did we break the 20-day high? If yes, this is breakout territory, not fade.
    if ctx.close_today > max(ctx.highs[-20:-1]):
        return None

    # Are we in the upper quartile of the 60d box?
    box_pos = (ctx.close_today - box_low) / max(box_high - box_low, 1e-9)
    if box_pos < near_top_quartile:
        return None

    triggers = ["range_bound_60d", "spike_up_today", "near_box_top"]
    score = 0.5

    spike_strength = max(0.0, min(1.0, (today_pct - today_min_pct) / 4.0))
    score += 0.20 * spike_strength
    if spike_strength >= 0.5:
        triggers.append("strong_spike")

    if ctx.rsi_qfq_6 is not None and ctx.rsi_qfq_6 >= rsi_overbought:
        score += 0.10
        triggers.append("rsi_overbought")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="Z3_RANGE_FADE",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "today_pct": today_pct,
            "box_high": box_high,
            "box_low": box_low,
            "box_range_pct": box_range * 100,
            "box_position_pct": box_pos * 100,
            "rsi6": ctx.rsi_qfq_6,
        },
    )
