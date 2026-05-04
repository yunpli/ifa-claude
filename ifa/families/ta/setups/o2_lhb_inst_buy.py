"""O2 龙虎榜机构净买入 — institutional seats appear on LHB with strong net buy.

Triggers (all):
  · Today's LHB net_amount / float_values >= 0.5%
  · At least one institutional seat with net_buy > 0 today (lhb_inst_buy_days_5d>=1)

Score:
  base 0.5
  + up to 0.20 continuous strength = clip((pct_float - 0.5) / 2.0, 0, 1)
  + up to 0.10 bonus when 5d institutional buying is persistent (>=2 days)
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def O2_LHB_INST_BUY(ctx: SetupContext) -> Candidate | None:
    pct_float_min = setup_param("O2_LHB_INST_BUY", "pct_float_min", 0.5)
    inst_days_min = setup_param("O2_LHB_INST_BUY", "inst_days_min", 1)

    pct_float = ctx.lhb_net_buy_pct_float_today
    if pct_float is None or pct_float < pct_float_min:
        return None
    if not ctx.lhb_inst_buy_days_5d or ctx.lhb_inst_buy_days_5d < inst_days_min:
        return None

    triggers = ["lhb_net_buy>=0.5%float", "lhb_inst_seat_today"]
    score = 0.5

    strength = max(0.0, min(1.0, (pct_float - 0.5) / 2.0))
    score += 0.20 * strength
    if strength >= 0.5:
        triggers.append("lhb_strong_net_buy")

    if ctx.lhb_inst_buy_days_5d >= 2:
        score += 0.10
        triggers.append("lhb_inst_persistent")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="O2_LHB_INST_BUY",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "lhb_net_buy_pct_float": pct_float,
            "lhb_inst_buy_days_5d": ctx.lhb_inst_buy_days_5d,
        },
    )
