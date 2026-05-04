"""T1 breakout — trend continuation through MA20.

Trigger conditions (all must hold):
  · close > MA20         — above mid-term trend
  · MA20 > MA60          — mid-term above long-term (uptrend stack)
  · close > close 20 trade days ago    — 20-day net gain
  · today's close >= max(close[-20:-1])   — actual breakout (new 20d high)

Score:
  base 0.5
  + 0.2 if close ≥ 1.02 * ma20   (decisively above, not just touching)
  + 0.2 if regime in {trend_continuation, early_risk_on}   (regime tailwind)
  + 0.1 if volume_ratio is not None and volume_ratio >= 1.5   (volume confirmation)

Evidence:
  · close, ma20, ma60, close_20d_ago, prior_20d_high
  · gain_20d_pct, vol_ratio
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def T1_BREAKOUT(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None):
        return None
    if len(ctx.closes) < 21:
        return None

    close_20d_ago = ctx.closes[-21]
    prior_20d_high = max(ctx.closes[-21:-1])

    if not (ctx.close_today > ctx.ma_qfq_20):
        return None
    if not (ctx.ma_qfq_20 > ctx.ma_qfq_60):
        return None
    if not (ctx.close_today > close_20d_ago):
        return None
    if not (ctx.close_today >= prior_20d_high):
        return None

    triggers = ["close>ma20", "ma20>ma60", "20d_breakout"]
    score = 0.5

    if ctx.close_today >= 1.02 * ctx.ma_qfq_20:
        score += 0.2
        triggers.append("decisive_above_ma20")

    if ctx.regime in ("trend_continuation", "early_risk_on"):
        score += 0.2
        triggers.append("regime_tailwind")

    if ctx.volume_ratio is not None and ctx.volume_ratio >= 1.5:
        score += 0.1
        triggers.append("volume_confirmation")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="T1_BREAKOUT",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "ma20": ctx.ma_qfq_20,
            "ma60": ctx.ma_qfq_60,
            "close_20d_ago": close_20d_ago,
            "prior_20d_high": prior_20d_high,
            "gain_20d_pct": (ctx.close_today / close_20d_ago - 1.0) * 100,
            "volume_ratio": ctx.volume_ratio,
            "regime": ctx.regime,
        },
    )
