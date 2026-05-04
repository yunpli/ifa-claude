"""F1 flag pattern — strong pole, then quiet sideways consolidation near the top.

Calibrated rules (after empirical tuning — original was 0 hits):
  · pole: closes[-11] / closes[-21] - 1 >= 0.08     — ≥8% gain in days [-21..-11]
  · flag (last 10 days): range_pct <= 9%            — sideways box
  · flag drift: -8% <= closes[-1] / closes[-10] - 1 <= 3%   — flat or modest pullback
  · today's close >= 70th-percentile of closes[-10:]  — near top of the flag
  · MA20 > MA60                                       — uptrend backdrop

Score:
  base 0.5
  + 0.2 if regime in {trend_continuation, early_risk_on}
  + 0.2 if pole >= 15%
  + 0.1 if volume_ratio >= 1.3
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def F1_FLAG(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or len(ctx.closes) < 22):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    pole = ctx.closes[-11] / ctx.closes[-21] - 1.0
    if pole < 0.08:
        return None

    flag_window = list(ctx.closes[-10:])
    flag_high = max(flag_window)
    flag_low = min(flag_window)
    flag_range_pct = (flag_high - flag_low) / flag_high if flag_high > 0 else 1.0
    if flag_range_pct > 0.09:
        return None

    drift = ctx.closes[-1] / ctx.closes[-10] - 1.0
    if drift < -0.08 or drift > 0.03:
        return None

    p70 = sorted(flag_window)[int(len(flag_window) * 0.7)]
    if ctx.close_today < p70:
        return None

    triggers = ["pole>=8%", "tight_flag<=9%", "near_top_of_flag"]
    score = 0.5

    # Continuous: 旗杆强度 — 8%→0, 25%→full
    pole_strength = max(0.0, min(1.0, (pole - 0.08) / 0.17))
    score += 0.20 * pole_strength
    if pole_strength >= 0.4:
        triggers.append("strong_pole")

    # Continuous: 量能
    if ctx.volume_ratio is not None:
        vol_strength = max(0.0, min(1.0, (ctx.volume_ratio - 1.0) / 1.5))
        score += 0.10 * vol_strength
        if vol_strength >= 0.2:
            triggers.append("volume_confirmation")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="F1_FLAG",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "pole_pct": pole * 100,
            "flag_range_pct": flag_range_pct * 100,
            "flag_high": flag_high,
            "flag_low": flag_low,
        },
    )
