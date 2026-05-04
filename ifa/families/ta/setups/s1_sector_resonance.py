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
from ifa.families.ta.setups._params import setup_param


def S1_SECTOR_RESONANCE(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or ctx.sw_l1_pct_change is None or ctx.sw_l2_pct_change is None
            or len(ctx.closes) < 2):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    l1_min = setup_param("S1_SECTOR_RESONANCE", "l1_pct_min", 1.0)
    l2_min = setup_param("S1_SECTOR_RESONANCE", "l2_pct_min", 1.5)
    stock_min = setup_param("S1_SECTOR_RESONANCE", "stock_ret_min_pct", 2.0)

    if ctx.sw_l1_pct_change < l1_min or ctx.sw_l2_pct_change < l2_min:
        return None

    stock_ret = (ctx.close_today / ctx.closes[-2] - 1.0) * 100
    if stock_ret < stock_min:
        return None

    triggers = ["uptrend_stack", "L1>=1%", "L2>=1.5%", "stock_ret>=2%"]
    score = 0.5

    # Continuous: L2 板块涨幅强度 — 1.5%→0, 5%→full
    l2_strength = max(0.0, min(1.0, (ctx.sw_l2_pct_change - 1.5) / 3.5))
    score += 0.20 * l2_strength
    if l2_strength >= 0.4:
        triggers.append("L2_leading")

    # Cross-sectional 个股涨幅 rank — 今日全市场 top 20% 才算 full
    if ctx.today_pct_chg_rank is not None:
        stock_strength = max(0.0, min(1.0, (ctx.today_pct_chg_rank - 0.7) / 0.25))
    else:
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
