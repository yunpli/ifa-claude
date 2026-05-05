"""R4 关键支撑反弹 — bounce off MA60 / multi-touch support.

Designed for "回踩支撑止跌反弹" pattern that's NOT the same as MA20-pullback (P1).
Targets the deeper retracement, mean-reversion bounce typical of choppy / range
regimes.

Logic:
  · Stock previously touched MA60 from above (or fell to a multi-touch low) within last 5 days
  · Today's close ≥ ma_qfq_60 × 1.005 — bounced back above support
  · Today's pct_chg ≥ +1.5% — actual bounce (not consolidation)
  · NOT a deep downtrend: closes[-1] ≥ closes[-60] × 0.85 (≤15% from 60d high)

Score:
  base 0.5
  + up to 0.20 = clip((bounce_pct - 1.5) / 3.0, 0, 1)
  + up to 0.10 if MA60 touched in last 3 days (tight test)
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def R4_SUPPORT_BOUNCE(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_60 is None
            or len(ctx.closes) < 60 or len(ctx.lows) < 5):
        return None

    above_ma60_min_x = setup_param("R4_SUPPORT_BOUNCE", "above_ma60_min_x", 1.005)
    today_pct_min = setup_param("R4_SUPPORT_BOUNCE", "today_pct_min", 1.5)
    drop_floor_x = setup_param("R4_SUPPORT_BOUNCE", "drop_floor_x", 0.85)
    touch_lookback_days = setup_param("R4_SUPPORT_BOUNCE", "touch_lookback_days", 5)

    # Today must close above MA60 with margin
    if ctx.close_today < above_ma60_min_x * ctx.ma_qfq_60:
        return None

    # Recent touch / breach of MA60
    recent_lows = ctx.lows[-touch_lookback_days:]
    if min(recent_lows) > ctx.ma_qfq_60:
        return None  # never reached support

    # Today's actual bounce
    if len(ctx.closes) < 2:
        return None
    today_pct = (ctx.close_today / ctx.closes[-2] - 1.0) * 100
    if today_pct < today_pct_min:
        return None

    # Not a catastrophic drop
    if ctx.close_today < ctx.closes[-60] * drop_floor_x:
        return None

    triggers = ["touched_ma60", "above_support_today", "bounce_today"]
    score = 0.5

    bounce_strength = max(0.0, min(1.0, (today_pct - today_pct_min) / 3.0))
    score += 0.20 * bounce_strength
    if bounce_strength >= 0.5:
        triggers.append("strong_bounce")

    # Was MA60 touched in last 3 days? Tighter test = better setup
    if min(ctx.lows[-3:]) <= ctx.ma_qfq_60:
        score += 0.10
        triggers.append("tight_support_test")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="R4_SUPPORT_BOUNCE",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "ma60": ctx.ma_qfq_60,
            "today_pct": today_pct,
            "lowest_5d": min(recent_lows),
        },
    )
