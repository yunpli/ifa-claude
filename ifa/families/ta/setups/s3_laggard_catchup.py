"""S3 laggard catch-up — strong sector, stock had lagged, today catches up.

Triggers (all):
  · sw_l2_pct_change >= 2% (today)              — L2 strong today
  · stock 20-day return < sw_l2_pct_change       (proxy: stock lagged sector recently)
        — we approximate by comparing stock_20d_ret to 0 if peer 20d not available
        — minimal version: stock 20d return <= 5%
  · today's stock return >= 3%                   — catching up
  · MA20 > MA60                                   — sector uptrend backdrop

Score:
  base 0.5
  + 0.2 if regime in {sector_rotation, early_risk_on}
  + 0.2 if stock 20d return <= 0 (was actually negative — true laggard)
  + 0.1 if volume_ratio >= 1.5
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def S3_LAGGARD_CATCHUP(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or ctx.sw_l2_pct_change is None or len(ctx.closes) < 21):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    l2_min = setup_param("S3_LAGGARD_CATCHUP", "l2_pct_min", 2.0)
    laggard_max = setup_param("S3_LAGGARD_CATCHUP", "stock_20d_max_pct", 5.0)
    today_min = setup_param("S3_LAGGARD_CATCHUP", "today_ret_min_pct", 3.0)

    if ctx.sw_l2_pct_change < l2_min:
        return None

    stock_20d_ret_pct = (ctx.close_today / ctx.closes[-21] - 1.0) * 100
    if stock_20d_ret_pct > laggard_max:
        return None

    today_ret_pct = (ctx.close_today / ctx.closes[-2] - 1.0) * 100
    if today_ret_pct < today_min:
        return None

    triggers = ["uptrend_stack", "L2>=2%", "stock_was_laggard", "catchup_today"]
    score = 0.5

    # Continuous: 滞涨程度 — 5%→0, -10%→full（越负越是真滞涨）
    laggard_strength = max(0.0, min(1.0, (5.0 - stock_20d_ret_pct) / 15.0))
    score += 0.20 * laggard_strength
    if laggard_strength >= 0.5:
        triggers.append("true_laggard")

    # Continuous: 量能
    if ctx.volume_ratio is not None:
        vol_strength = max(0.0, min(1.0, (ctx.volume_ratio - 1.0) / 1.5))
        score += 0.10 * vol_strength
        if vol_strength >= 0.3:
            triggers.append("volume_confirmation")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="S3_LAGGARD_CATCHUP",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "stock_20d_ret_pct": stock_20d_ret_pct,
            "today_ret_pct": today_ret_pct,
            "sw_l2_pct": ctx.sw_l2_pct_change,
        },
    )
