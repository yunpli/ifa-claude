"""D3 流星线 — bearish shooting-star candle at top.

Triggers (all):
  · Prior 20d return >= 15% (must be near top)
  · Today: long upper shadow (upper / total_range >= 0.6)
  · Small real body (|close - open| / total_range <= 0.3)
  · Today's close <= prior close (no real continuation)

Score (bearish):
  base 0.5
  + up to 0.20 continuous = clip((upper_ratio - 0.6) / 0.3, 0, 1)
  + up to 0.10 if volume_ratio >= 1.5 (climax)
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def D3_SHOOTING_STAR(ctx: SetupContext) -> Candidate | None:
    if ctx.close_today is None or len(ctx.closes) < 21 or not ctx.highs or not ctx.lows:
        return None
    high_t, low_t, close_t = ctx.highs[-1], ctx.lows[-1], ctx.close_today
    prev_close = ctx.closes[-2]
    # use prev_close as proxy for open since open isn't in context fields uniformly; estimate body via |close - prev|
    body = abs(close_t - prev_close)
    total_range = high_t - low_t
    if total_range <= 0:
        return None
    upper_shadow = high_t - max(close_t, prev_close)
    if upper_shadow <= 0:
        return None
    upper_ratio = upper_shadow / total_range
    if upper_ratio < 0.6:
        return None
    body_ratio = body / total_range
    if body_ratio > 0.3:
        return None
    if close_t > prev_close:
        return None
    ret_20d = (close_t / ctx.closes[-21] - 1.0) * 100
    if ret_20d < 15.0:
        return None

    triggers = ["shooting_star", "post_strong_runup", "no_continuation"]
    score = 0.5
    strength = max(0.0, min(1.0, (upper_ratio - 0.6) / 0.3))
    score += 0.20 * strength
    if strength >= 0.5:
        triggers.append("long_upper_shadow")
    if (ctx.volume_ratio or 0) >= 1.5:
        score += 0.10
        triggers.append("climax_volume")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="D3_SHOOTING_STAR",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": close_t,
            "upper_shadow_ratio": upper_ratio,
            "body_ratio": body_ratio,
            "ret_20d_pct": ret_20d,
        },
    )
