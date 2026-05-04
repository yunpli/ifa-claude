"""TA regime classifier auto-validation via data-derived oracle.

Encodes the heuristic rules from user-annotated golden set (10 days, 2026-Q1)
as an oracle function over RegimeContext, then compares system classifier
output across a larger date window. Surfaces disagreements for review.

Run:
    uv run python scripts/ta_regime_oracle_check.py [--start 2025-09-01] [--end 2026-04-30]
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter
from datetime import date

from sqlalchemy import text

from ifa.core.calendar import trading_days_between
from ifa.core.db import get_engine
from ifa.families.ta.regime.classifier import (
    RegimeContext, classify_regime,
    _sse_ma20_rising, _sse_ma5_above_ma20, _up_down_ratio,
)
from ifa.families.ta.regime.loader import load_regime_context

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def oracle_regime(ctx: RegimeContext) -> str | None:
    """Hand-coded expected regime from objective market signals.

    Returns None when the signals are too ambiguous to assert anything.
    Rules in priority order — first match wins.
    """
    # 1. Catastrophic breadth → distribution_risk
    if ctx.n_limit_down is not None and ctx.n_limit_down >= 50:
        return "distribution_risk"
    if (ctx.n_down is not None and ctx.n_down > 4500
            and ctx.n_limit_down is not None and ctx.n_limit_down > 30):
        return "distribution_risk"

    # 2. Mania / extreme breadth up → emotional_climax
    if (ctx.n_limit_up is not None and ctx.n_limit_up >= 120
            and ctx.consecutive_lb_high is not None and ctx.consecutive_lb_high >= 7):
        return "emotional_climax"

    # 3. Strong breadth-positive day → early_risk_on
    if (ctx.n_limit_up is not None and ctx.n_limit_up >= 70
            and ctx.n_up is not None and ctx.n_up >= 4000):
        return "early_risk_on"

    # 4. Bad breadth → cooldown
    if ctx.n_limit_down is not None and ctx.n_limit_down >= 25:
        return "cooldown"
    if ctx.n_down is not None and ctx.n_down > 3700:
        return "cooldown"
    udr = _up_down_ratio(ctx)
    if (udr is not None and udr < 0.7
            and ctx.n_down is not None and ctx.n_down > 3000):
        return "cooldown"

    # 5. MA structure for the rest
    rising = _sse_ma20_rising(ctx)
    above = _sse_ma5_above_ma20(ctx)

    if rising is True and above is True and udr is not None and udr > 1.3:
        return "trend_continuation"
    # weak_rebound: STRICT — only when MA20 falling AND breadth bouncing strong.
    if rising is False and above is False and udr is not None and udr > 1.5:
        return "weak_rebound"

    # 6. Default: ambiguous days are range_bound
    return "range_bound"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2025-09-01")
    p.add_argument("--end", default="2026-04-30")
    args = p.parse_args()

    engine = get_engine()
    days = trading_days_between(engine, date.fromisoformat(args.start),
                                date.fromisoformat(args.end))
    log.info("checking %d trade days from %s to %s", len(days), args.start, args.end)

    n_match = 0
    n_disagree = 0
    n_oracle_none = 0
    disagreements: list[dict] = []

    for d in days:
        ctx = load_regime_context(engine, d)
        if ctx.n_up is None:    # no market_state_daily for this day
            continue
        sys_result = classify_regime(ctx)
        oracle = oracle_regime(ctx)
        if oracle is None:
            n_oracle_none += 1
            continue
        if sys_result.regime == oracle:
            n_match += 1
        else:
            n_disagree += 1
            disagreements.append({
                "d": d,
                "sys": sys_result.regime,
                "oracle": oracle,
                "lu": ctx.n_limit_up,
                "ld": ctx.n_limit_down,
                "up": ctx.n_up,
                "down": ctx.n_down,
                "lb_high": ctx.consecutive_lb_high,
                "udr": (ctx.n_up / max(ctx.n_down, 1)) if (ctx.n_up and ctx.n_down) else None,
            })

    log.info("=" * 60)
    log.info(f"match: {n_match}  disagree: {n_disagree}  oracle_ambiguous: {n_oracle_none}")
    rate = n_match / max(n_match + n_disagree, 1) * 100
    log.info(f"agreement rate: {rate:.1f}%")
    log.info("=" * 60)

    if disagreements:
        # Disagreement summary by (sys, oracle) pair
        pairs = Counter((d["sys"], d["oracle"]) for d in disagreements)
        log.info("\nDisagreement pairs (sys → oracle):")
        for (s, o), n in pairs.most_common():
            log.info(f"  {s:25} → {o:25}  {n}")

        # Show first 20 example days
        log.info("\nFirst 20 disagreements:")
        for d in disagreements[:20]:
            udr_s = f"{d['udr']:.2f}" if d['udr'] is not None else "-"
            log.info(f"  {d['d']}  sys={d['sys']:23} oracle={d['oracle']:23} "
                     f"lu={d['lu']} ld={d['ld']} up={d['up']} down={d['down']} udr={udr_s}")


if __name__ == "__main__":
    main()
