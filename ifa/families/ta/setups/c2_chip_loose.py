"""C2 chip looseness — wide chip distribution + high winner rate, distribution risk.

Triggers (all):
  · chip_concentration_pct >= 25%               — wide distribution
  · chip_winner_rate_pct >= 80%                  — most chips in profit (sell pressure)
  · 20-day return >= 15%                          — already had a big run

Score:
  base 0.5
  + 0.2 if regime in {emotional_climax, distribution_risk}
  + 0.2 if chip_winner_rate_pct >= 90%            — extreme profit taking pressure
  + 0.1 if 20-day return >= 30%                   — extreme run already

Note: This is a *risk warning* setup, not a buy signal. Consumers should
treat it as a sell/avoid candidate.
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def C2_CHIP_LOOSE(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None
            or ctx.chip_concentration_pct is None
            or ctx.chip_winner_rate_pct is None
            or len(ctx.closes) < 21):
        return None

    if ctx.chip_concentration_pct < 25.0:
        return None
    if ctx.chip_winner_rate_pct < 80.0:
        return None

    ret_20d = (ctx.close_today / ctx.closes[-21] - 1.0) * 100
    if ret_20d < 15.0:
        return None

    triggers = ["chip_loose>=25%", "winner_rate>=80%", "20d_ret>=15%"]
    score = 0.5

    # Continuous: 盈利盘比例 — 80%→0, 100%→full
    winner_strength = max(0.0, min(1.0, (ctx.chip_winner_rate_pct - 80.0) / 20.0))
    score += 0.20 * winner_strength
    if winner_strength >= 0.5:
        triggers.append("extreme_winner_rate")

    # Continuous: 累计涨幅 — 15%→0, 50%+→full
    run_strength = max(0.0, min(1.0, (ret_20d - 15.0) / 35.0))
    score += 0.10 * run_strength
    if run_strength >= 0.4:
        triggers.append("extreme_run")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="C2_CHIP_LOOSE",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "chip_concentration_pct": ctx.chip_concentration_pct,
            "chip_winner_rate_pct": ctx.chip_winner_rate_pct,
            "ret_20d_pct": ret_20d,
            "warning": "distribution_risk_candidate",
        },
    )
