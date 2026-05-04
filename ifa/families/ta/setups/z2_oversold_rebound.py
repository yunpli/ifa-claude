"""Z2 短期超卖反弹 — RSI-driven oversold bounce in non-bear setup.

Triggers (all):
  · RSI(6) <= 25                                  — oversold
  · 5d return <= -5%                              — actually fallen
  · Today close > today open proxy (close >= prev_close * 0.99) — first sign of stabilization
  · MA60 stable: closes[-1] >= closes[-60] * 0.85 — not in catastrophic downtrend

Score:
  base 0.5
  + up to 0.20 continuous = clip((25 - RSI) / 15, 0, 1)
  + up to 0.10 if today's volume_ratio >= 1.2 (capitulation/turn volume)
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def Z2_OVERSOLD_REBOUND(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.rsi_qfq_6 is None or len(ctx.closes) < 60):
        return None

    rsi_max = setup_param("Z2_OVERSOLD_REBOUND", "rsi_max", 25)
    ret_5d_max_pct = setup_param("Z2_OVERSOLD_REBOUND", "ret_5d_max_pct", -5.0)
    ma60_drop_max_x = setup_param("Z2_OVERSOLD_REBOUND", "ma60_drop_max_x", 0.85)

    if ctx.rsi_qfq_6 > rsi_max:
        return None
    ret_5d = (ctx.close_today / ctx.closes[-6] - 1.0) * 100
    if ret_5d > ret_5d_max_pct:
        return None
    if ctx.close_today < ctx.closes[-2] * 0.99:
        return None
    if ctx.close_today < ctx.closes[-60] * ma60_drop_max_x:
        return None

    triggers = ["rsi_oversold", "5d_drawdown", "stabilizing"]
    score = 0.5

    rsi_strength = max(0.0, min(1.0, (rsi_max - ctx.rsi_qfq_6) / max(rsi_max * 0.6, 1)))
    score += 0.20 * rsi_strength
    if rsi_strength >= 0.5:
        triggers.append("deeply_oversold")

    if (ctx.volume_ratio or 0) >= 1.2:
        score += 0.10
        triggers.append("turn_volume")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="Z2_OVERSOLD_REBOUND",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "rsi6": ctx.rsi_qfq_6,
            "ret_5d_pct": ret_5d,
            "volume_ratio": ctx.volume_ratio,
        },
    )
