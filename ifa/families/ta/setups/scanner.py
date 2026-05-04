"""Run all setups across all contexts; return the full Candidate list."""
from __future__ import annotations

import logging
from typing import Iterable

from ifa.families.ta.setups import SETUPS
from ifa.families.ta.setups.base import Candidate, SetupContext

log = logging.getLogger(__name__)


def scan(contexts: Iterable[SetupContext]) -> list[Candidate]:
    """Run every setup against every context. Multiple hits per stock are kept.

    M9.7: enrich each Candidate's evidence with sector context (role / phase /
    quality) from its SetupContext, so downstream ranker + repo can access
    SmartMoney sector info without re-querying.
    """
    candidates: list[Candidate] = []
    n_ctx = 0
    for ctx in contexts:
        n_ctx += 1
        for setup_name, setup_fn in SETUPS.items():
            try:
                result = setup_fn(ctx)
            except Exception as e:
                log.warning("%s failed for %s: %s", setup_name, ctx.ts_code, e)
                continue
            if result is not None:
                # Inject sector context (mutable evidence dict)
                if isinstance(result.evidence, dict):
                    result.evidence["sector_role"] = ctx.sector_role
                    result.evidence["sector_cycle_phase"] = ctx.sector_cycle_phase
                    result.evidence["sector_quality"] = ctx.sector_quality
                candidates.append(result)
    log.info("scanned %d contexts → %d candidates", n_ctx, len(candidates))
    return candidates
