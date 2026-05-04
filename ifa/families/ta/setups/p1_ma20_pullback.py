"""P1 MA20 pullback — orderly retest of MA20 in uptrend.

Triggers (all):
  · MA20 > MA60                            — uptrend
  · today's low <= 1.01 * ma20             — actually touched/pierced MA20
  · close >= ma20                           — closed back above (defended)
  · close < close[-5]                       — net down over 5 days (pullback)
  · volume_ratio is None or volume_ratio < 1.2   — orderly (not panic)

Score:
  base 0.5
  + 0.2 if regime == "trend_continuation"
  + 0.2 if close >= ma_qfq_5             (close back above MA5 too)
  + 0.1 if rsi_qfq_6 is not None and rsi_qfq_6 <= 50
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def P1_MA20_PULLBACK(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None
            or ctx.ma_qfq_60 is None or not ctx.lows):
        return None
    if len(ctx.closes) < 6:
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    today_low = ctx.lows[-1]
    if today_low > 1.01 * ctx.ma_qfq_20:
        return None
    if ctx.close_today < ctx.ma_qfq_20:
        return None
    if ctx.close_today >= ctx.closes[-6]:    # not actually pulling back
        return None
    if ctx.volume_ratio is not None and ctx.volume_ratio >= 1.2:
        return None

    triggers = ["uptrend_stack", "touched_ma20", "defended_close", "net_pullback_5d"]
    score = 0.5

    if ctx.regime == "trend_continuation":
        score += 0.2
        triggers.append("regime_tailwind")
    if ctx.ma_qfq_5 is not None and ctx.close_today >= ctx.ma_qfq_5:
        score += 0.2
        triggers.append("above_ma5")
    if ctx.rsi_qfq_6 is not None and ctx.rsi_qfq_6 <= 50:
        score += 0.1
        triggers.append("rsi_oversold_room")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="P1_MA20_PULLBACK",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "today_low": today_low,
            "ma20": ctx.ma_qfq_20,
            "ma60": ctx.ma_qfq_60,
            "close_5d_ago": ctx.closes[-6],
            "volume_ratio": ctx.volume_ratio,
        },
    )
