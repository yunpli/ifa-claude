"""O3 涨停封单结构 — fully sealed limit-up with strong seal-to-float ratio.

Triggers (all):
  · KPL status today = 'T' (fully sealed, not 炸板)
  · seal_ratio (lu_bid_vol*pct_chg / float) >= 1.0%
  · MA20 >= MA60 (avoid sealed limits in clearly-down markets)

Score:
  base 0.5
  + up to 0.20 continuous = clip((seal_ratio - 1.0) / 4.0, 0, 1)
  + up to 0.10 if 5d super-large flow is also positive (>=0.5%)
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def O3_LIMIT_SEAL_STRENGTH(ctx: SetupContext) -> Candidate | None:
    seal_ratio_min = setup_param("O3_LIMIT_SEAL_STRENGTH", "seal_ratio_min", 1.0)

    if ctx.kpl_status_today != "T" or ctx.kpl_seal_ratio_today is None:
        return None
    if ctx.kpl_seal_ratio_today < seal_ratio_min:
        return None
    if ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None or ctx.ma_qfq_20 < ctx.ma_qfq_60:
        return None

    triggers = ["limit_sealed", "seal_ratio>=1%", "uptrend_stack"]
    score = 0.5

    strength = max(0.0, min(1.0, (ctx.kpl_seal_ratio_today - 1.0) / 4.0))
    score += 0.20 * strength
    if strength >= 0.5:
        triggers.append("strong_seal")

    if (ctx.super_large_net_buy_5d_pct or 0) >= 0.5:
        score += 0.10
        triggers.append("inst_inflow_aligned")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="O3_LIMIT_SEAL_STRENGTH",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "kpl_seal_ratio": ctx.kpl_seal_ratio_today,
            "kpl_status": ctx.kpl_status_today,
        },
    )
