"""O1 机构连续抢筹 — institutional money flowing in 持续多日.

Triggers (all):
  · 5-day cumulative super-large + large net buy / 流通市值 >= 1%
  · uptrend backdrop (MA20 >= MA60)
  · today not deeply red (pct_chg > -3)

Score:
  base 0.5
  + up to 0.20 continuous strength = clip((flow_pct - 1.0) / 4.0, 0, 1)
  + up to 0.10 if also appeared on 龙虎榜 with inst buying
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def O1_INST_PERSISTENT_BUY(ctx: SetupContext) -> Candidate | None:
    if ctx.super_large_net_buy_5d_pct is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None:
        return None
    if ctx.ma_qfq_20 < ctx.ma_qfq_60:
        return None
    if ctx.super_large_net_buy_5d_pct < 1.0:
        return None
    if ctx.today_pct_chg is not None and ctx.today_pct_chg < -3.0:
        return None

    triggers = ["inst_5d_inflow>=1%", "uptrend_stack"]
    score = 0.5

    flow_strength = max(0.0, min(1.0, (ctx.super_large_net_buy_5d_pct - 1.0) / 4.0))
    score += 0.20 * flow_strength
    if flow_strength >= 0.5:
        triggers.append("strong_inst_inflow")

    if (ctx.lhb_inst_buy_days_5d or 0) >= 1:
        score += 0.10
        triggers.append("lhb_inst_buy_recent")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="O1_INST_PERSISTENT_BUY",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "super_large_net_buy_5d_pct": ctx.super_large_net_buy_5d_pct,
            "lhb_inst_buy_days_5d": ctx.lhb_inst_buy_days_5d or 0,
        },
    )
