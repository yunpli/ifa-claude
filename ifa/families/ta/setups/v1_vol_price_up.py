"""V1 volume-price expansion — both rising together.

Triggers (all):
  · 5-day return >= 5%
  · today's volume_ratio >= 1.5                — volume expansion
  · MA20 > MA60                                  — uptrend backdrop

Score:
  base 0.5
  + 0.2 if regime in {trend_continuation, early_risk_on}
  + 0.2 if 5-day return >= 10%                    — strong move
  + 0.1 if volume_ratio >= 2.0                    — exceptional volume
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def V1_VOL_PRICE_UP(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or ctx.volume_ratio is None or len(ctx.closes) < 6):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    ret_5d_min = setup_param("V1_VOL_PRICE_UP", "ret_5d_min", 0.05)
    vol_ratio_min = setup_param("V1_VOL_PRICE_UP", "vol_ratio_min", 1.5)

    ret_5d = ctx.close_today / ctx.closes[-6] - 1.0
    if ret_5d < ret_5d_min:
        return None
    if ctx.volume_ratio < vol_ratio_min:
        return None

    triggers = ["5d_ret>=5%", "vol_ratio>=1.5", "uptrend_stack"]
    score = 0.5

    # ATR-normalized price strength: 5d move / (5 × ATR_pct)
    if ctx.atr_pct_20d and ctx.atr_pct_20d > 0:
        atr_units = (ret_5d * 100) / (5 * ctx.atr_pct_20d)
        price_strength = max(0.0, min(1.0, (atr_units - 0.5) / 1.0))
    else:
        price_strength = max(0.0, min(1.0, (ret_5d - 0.05) / 0.10))
    score += 0.20 * price_strength
    if price_strength >= 0.5:
        triggers.append("strong_5d_return")

    # Cross-sectional 量能 — 今日全市场 rank ≥ top 30% = full bonus
    if ctx.volume_ratio_rank is not None:
        vol_strength = max(0.0, min(1.0, (ctx.volume_ratio_rank - 0.7) / 0.3))
    else:
        vol_strength = max(0.0, min(1.0, (ctx.volume_ratio - 1.5) / 2.0))
    score += 0.10 * vol_strength
    if vol_strength >= 0.25:
        triggers.append("volume_exceptional")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="V1_VOL_PRICE_UP",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "ret_5d_pct": ret_5d * 100,
            "volume_ratio": ctx.volume_ratio,
        },
    )
