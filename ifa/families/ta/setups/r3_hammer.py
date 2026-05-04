"""R3 hammer reversal — long lower shadow after a downtrend.

Today's candle: lower-shadow >= 2x body, body in upper third, after a
20-day downtrend.

Triggers (all):
  · 20-day return (close[-1] / close[-21] - 1) <= -8%   — downtrend
  · today's high - today's low > 0
  · |close - open| / (high - low) <= 0.35              — small body
  · body in upper 1/3 of bar:
        min(close, open) - low >= 2 * |close - open|   — long lower shadow
  · close > open                                        — bullish hammer (or doji-ish)

Note: we don't have today's open in SetupContext — derive open as
close[-2] (previous close) under "open ~ prev close" assumption. This
is a simplification; a stricter version would add `open_today` to ctx.

Score:
  base 0.5
  + 0.2 if regime in {cooldown, weak_rebound}
  + 0.2 if 20-day return <= -15% (deeper drop = stronger reversal candidate)
  + 0.1 if volume_ratio >= 1.5
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def R3_HAMMER(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or len(ctx.closes) < 21
            or not ctx.highs or not ctx.lows):
        return None

    ret_20d = ctx.close_today / ctx.closes[-21] - 1.0
    if ret_20d > -0.08:
        return None

    today_high = ctx.highs[-1]
    today_low = ctx.lows[-1]
    if today_high <= today_low:
        return None

    open_proxy = ctx.closes[-2]    # yesterday's close ≈ today's open
    body = abs(ctx.close_today - open_proxy)
    bar_range = today_high - today_low
    if body / bar_range > 0.35:
        return None

    body_low = min(ctx.close_today, open_proxy)
    lower_shadow = body_low - today_low
    if lower_shadow < 2 * body:
        return None
    if ctx.close_today < open_proxy:
        return None

    triggers = ["downtrend_20d<=-8%", "small_body", "long_lower_shadow", "bullish_close"]
    score = 0.5

    # Continuous: 跌幅深度 — -8%→0, -25%→full
    drop_strength = max(0.0, min(1.0, (-ret_20d - 0.08) / 0.17))
    score += 0.20 * drop_strength
    if drop_strength >= 0.4:
        triggers.append("deep_drop")

    # Continuous: 量能确认
    if ctx.volume_ratio is not None:
        vol_strength = max(0.0, min(1.0, (ctx.volume_ratio - 1.0) / 1.5))
        score += 0.10 * vol_strength
        if vol_strength >= 0.3:
            triggers.append("volume_confirmation")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="R3_HAMMER",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "open_proxy": open_proxy,
            "high": today_high,
            "low": today_low,
            "body": body,
            "lower_shadow": lower_shadow,
            "ret_20d_pct": ret_20d * 100,
        },
    )
