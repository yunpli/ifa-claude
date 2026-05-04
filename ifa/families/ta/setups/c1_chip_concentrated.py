"""C1 chip concentrated — narrow cost distribution, often before a move.

Triggers (all):
  · chip_concentration_pct is not None
  · chip_concentration_pct <= 15%               — tight chip distribution
  · MA20 > MA60                                  — uptrend backdrop
  · close >= ma_qfq_20                           — price above mid trend

Score:
  base 0.5
  + 0.2 if regime in {trend_continuation, range_bound}
  + 0.2 if chip_concentration_pct <= 10%         — very concentrated
  + 0.1 if chip_winner_rate_pct is not None and 40 <= chip_winner_rate_pct <= 80
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def C1_CHIP_CONCENTRATED(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or ctx.chip_concentration_pct is None):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    conc_max = setup_param("C1_CHIP_CONCENTRATED", "concentration_pct_max", 15.0)

    if ctx.chip_concentration_pct > conc_max:
        return None
    if ctx.close_today < ctx.ma_qfq_20:
        return None

    triggers = ["uptrend_stack", "chip_concentrated<=15%", "above_ma20"]
    score = 0.5

    concentration_strength = max(0.0, min(1.0, (conc_max - ctx.chip_concentration_pct) / max(conc_max - 5.0, 1e-6)))
    score += 0.20 * concentration_strength
    if concentration_strength >= 0.5:
        triggers.append("very_concentrated")

    # Continuous: 盈利盘均衡度 — 60% 处峰值，偏离 ±25 衰减
    if ctx.chip_winner_rate_pct is not None:
        balance_strength = max(0.0, min(1.0, 1 - abs(ctx.chip_winner_rate_pct - 60) / 25))
        score += 0.10 * balance_strength
        if balance_strength >= 0.5:
            triggers.append("balanced_winners")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="C1_CHIP_CONCENTRATED",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "chip_concentration_pct": ctx.chip_concentration_pct,
            "chip_winner_rate_pct": ctx.chip_winner_rate_pct,
        },
    )
