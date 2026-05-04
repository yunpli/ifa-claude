"""S1 sector resonance — stock + L1 + L2 all up together.

Triggers (all):
  · sw_l1_pct_change >= 1%
  · sw_l2_pct_change >= 1.5%               — L2 stronger than L1
  · today's stock return (close vs prev close) >= 2%
  · MA20 > MA60                             — uptrend backdrop

Score:
  base 0.5
  + 0.2 if regime in {trend_continuation, early_risk_on, sector_rotation}
  + 0.2 if sw_l2_pct_change >= 3%           — L2 leading
  + 0.1 if today's stock return >= 5%
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def S1_SECTOR_RESONANCE(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or ctx.sw_l1_pct_change is None or ctx.sw_l2_pct_change is None
            or len(ctx.closes) < 2):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    if ctx.sw_l1_pct_change < 1.0 or ctx.sw_l2_pct_change < 1.5:
        return None

    stock_ret = (ctx.close_today / ctx.closes[-2] - 1.0) * 100
    if stock_ret < 2.0:
        return None

    triggers = ["uptrend_stack", "L1>=1%", "L2>=1.5%", "stock_ret>=2%"]
    score = 0.5

    # Continuous: L2 板块涨幅强度 — 1.5%→0, 5%→full
    l2_strength = max(0.0, min(1.0, (ctx.sw_l2_pct_change - 1.5) / 3.5))
    score += 0.20 * l2_strength
    if l2_strength >= 0.4:
        triggers.append("L2_leading")

    # Continuous: 个股涨幅强度 — 2%→0, 8%→full
    stock_strength = max(0.0, min(1.0, (stock_ret - 2.0) / 6.0))
    score += 0.10 * stock_strength
    if stock_strength >= 0.5:
        triggers.append("stock_strong")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="S1_SECTOR_RESONANCE",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "stock_ret_pct": stock_ret,
            "sw_l1_pct": ctx.sw_l1_pct_change,
            "sw_l2_pct": ctx.sw_l2_pct_change,
        },
    )
