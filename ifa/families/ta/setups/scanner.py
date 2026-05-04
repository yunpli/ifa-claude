"""Run all setups across all contexts; return long + warning candidate lists."""
from __future__ import annotations

import logging
from typing import Iterable

from ifa.families.ta.setups import SETUPS, WARNING_SETUPS
from ifa.families.ta.setups.base import Candidate, SetupContext

log = logging.getLogger(__name__)


def scan(contexts: Iterable[SetupContext]) -> tuple[list[Candidate], list[Candidate]]:
    """Run setups across contexts; route by warning-vs-long pool.

    Routing (M10 P0.1):
      · WARNING setups (D1/D2/D3) — run on EVERY liquid stock, including
        those failing Layer-1 sector filter. Output → warnings list.
      · LONG setups (T/P/R/F/V/S/C/O/Z/E)  — run only on stocks where
        ctx.in_long_universe is True. Output → long candidates list.

    Returns (long_candidates, warning_candidates).

    M9.7: enrich each Candidate's evidence with sector context (role / phase /
    quality) from its SetupContext, so downstream ranker + repo can access
    SmartMoney sector info without re-querying.
    """
    long_cands: list[Candidate] = []
    warn_cands: list[Candidate] = []
    n_ctx = 0
    for ctx in contexts:
        n_ctx += 1
        for setup_name, setup_fn in SETUPS.items():
            is_warning = setup_name in WARNING_SETUPS
            if not is_warning and not ctx.in_long_universe:
                continue
            try:
                result = setup_fn(ctx)
            except Exception as e:
                log.warning("%s failed for %s: %s", setup_name, ctx.ts_code, e)
                continue
            if result is None:
                continue
            if isinstance(result.evidence, dict):
                result.evidence["sector_role"] = ctx.sector_role
                result.evidence["sector_cycle_phase"] = ctx.sector_cycle_phase
                result.evidence["sector_quality"] = ctx.sector_quality
                result.evidence["in_long_universe"] = ctx.in_long_universe
            if is_warning:
                warn_cands.append(result)
            else:
                long_cands.append(result)
    log.info("scanned %d contexts → %d long + %d warning candidates",
             n_ctx, len(long_cands), len(warn_cands))
    return long_cands, warn_cands
