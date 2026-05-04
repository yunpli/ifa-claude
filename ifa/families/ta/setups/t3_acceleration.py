"""T3 acceleration — full MA stack, MACD rising.

Triggers (all):
  · close > ma5 > ma10 > ma20 > ma60      — perfect bullish stack
  · macd_dif_qfq > macd_dea_qfq           — MACD golden zone
  · macd_qfq > 0                          — histogram positive
  · 5-day return >= 5%                    — visible acceleration

Score:
  base 0.5
  + 0.2 if regime in {trend_continuation, early_risk_on}
  + 0.2 if 5-day return >= 10%             — strong acceleration
  + 0.1 if volume_ratio >= 1.3
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def T3_ACCELERATION(ctx: SetupContext) -> Candidate | None:
    needed = (ctx.close_today, ctx.ma_qfq_5, ctx.ma_qfq_10,
              ctx.ma_qfq_20, ctx.ma_qfq_60,
              ctx.macd_qfq, ctx.macd_dea_qfq, ctx.macd_dif_qfq)
    if any(v is None for v in needed):
        return None
    if len(ctx.closes) < 6:
        return None

    if not (ctx.close_today > ctx.ma_qfq_5 > ctx.ma_qfq_10
            > ctx.ma_qfq_20 > ctx.ma_qfq_60):
        return None
    if ctx.macd_dif_qfq <= ctx.macd_dea_qfq:
        return None
    if ctx.macd_qfq <= 0:
        return None

    ret_5d = ctx.close_today / ctx.closes[-6] - 1.0
    if ret_5d < 0.05:
        return None

    triggers = ["full_ma_stack", "macd_golden", "macd_positive", "5d_ret>=5%"]
    score = 0.5

    if ctx.regime in ("trend_continuation", "early_risk_on"):
        score += 0.2
        triggers.append("regime_tailwind")
    if ret_5d >= 0.10:
        score += 0.2
        triggers.append("strong_acceleration")
    if ctx.volume_ratio is not None and ctx.volume_ratio >= 1.3:
        score += 0.1
        triggers.append("volume_confirmation")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="T3_ACCELERATION",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "ma_stack": [ctx.ma_qfq_5, ctx.ma_qfq_10, ctx.ma_qfq_20, ctx.ma_qfq_60],
            "macd": ctx.macd_qfq,
            "macd_dif": ctx.macd_dif_qfq,
            "macd_dea": ctx.macd_dea_qfq,
            "ret_5d_pct": ret_5d * 100,
        },
    )
