"""F1 flag pattern — strong pole, tight downward consolidation, ready to break.

Triggers (all):
  · pole: closes[-15] / closes[-21] - 1 >= 0.10        — 5-day strong move 15-20 days back
  · flag (last 10 days): max-min range / max <= 7%     — tight consolidation
  · slight downward drift in flag: closes[-1] < closes[-10] AND closes[-1] >= closes[-10] * 0.95
  · today's close >= max(closes[-10:-1])               — about to break out
  · MA20 > MA60                                         — uptrend backdrop

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

    pole = ctx.closes[-15] / ctx.closes[-21] - 1.0
    if pole < 0.10:
        return None

    flag_window = ctx.closes[-10:]
    flag_high = max(flag_window)
    flag_low = min(flag_window)
    flag_range_pct = (flag_high - flag_low) / flag_high if flag_high > 0 else 1.0
    if flag_range_pct > 0.07:
        return None

    if ctx.closes[-1] >= ctx.closes[-10]:
        return None
    if ctx.closes[-1] < ctx.closes[-10] * 0.95:
        return None

    if ctx.close_today < max(ctx.closes[-10:-1]):
        return None

    triggers = ["pole>=10%", "tight_flag<=7%", "downward_drift", "near_breakout"]
    score = 0.5
    if ctx.regime in ("trend_continuation", "early_risk_on"):
        score += 0.2
        triggers.append("regime_tailwind")
    if pole >= 0.15:
        score += 0.2
        triggers.append("strong_pole")
    if ctx.volume_ratio is not None and ctx.volume_ratio >= 1.3:
        score += 0.1
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
