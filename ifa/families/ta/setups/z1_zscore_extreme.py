"""Z1 极端 z-score 反转 — mean-reversion on cross-sectional + temporal extremes.

Logic:
  · Compute 20d daily returns; today's z-score = (today_pct - mean) / std.
  · |z| >= 2.0 marks an extreme (statistical outlier).
  · Two flavors via sign:
       z <= -2.0 + uptrend backdrop (MA20>=MA60) → long mean-reversion candidate
       z >= +2.0 + post-runup (20d ret >= 15%)   → exhaustion warning

Score:
  base 0.5
  + up to 0.20 continuous = clip((|z| - 2.0) / 1.5, 0, 1)
  + up to 0.10 if RSI confirms (oversold for long, overbought for short)
"""
from __future__ import annotations

import math

from ifa.families.ta.setups.base import Candidate, SetupContext


def Z1_ZSCORE_EXTREME(ctx: SetupContext) -> Candidate | None:
    closes = ctx.closes
    if len(closes) < 22 or ctx.close_today is None:
        return None
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(-20, 0) if closes[i - 1]]
    if len(rets) < 18:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    today_ret = closes[-1] / closes[-2] - 1.0
    z = (today_ret - mean) / sd
    if abs(z) < 2.0:
        return None

    direction = "long" if z <= -2.0 else "short"
    if direction == "long":
        if ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None or ctx.ma_qfq_20 < ctx.ma_qfq_60:
            return None
    else:
        ret_20d = (closes[-1] / closes[-21] - 1.0) * 100
        if ret_20d < 15.0:
            return None

    triggers = [f"z_extreme_{direction}", f"|z|>={abs(z):.1f}"]
    score = 0.5
    strength = max(0.0, min(1.0, (abs(z) - 2.0) / 1.5))
    score += 0.20 * strength
    if strength >= 0.5:
        triggers.append("very_extreme")
    if ctx.rsi_qfq_6 is not None:
        if direction == "long" and ctx.rsi_qfq_6 <= 25:
            score += 0.10
            triggers.append("rsi_oversold")
        elif direction == "short" and ctx.rsi_qfq_6 >= 75:
            score += 0.10
            triggers.append("rsi_overbought")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="Z1_ZSCORE_EXTREME",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": closes[-1],
            "z_score": z,
            "today_ret_pct": today_ret * 100,
            "direction": direction,
        },
    )
