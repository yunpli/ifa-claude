"""One-off: recompute IRM_REPLY_RATE for every stock with cached irm_qa.

Why this exists:
  · Original governance.py looked for r.get('reply') / r.get('answer') —
    Tushare's irm_qa actually uses field 'a'. Result: every stock's
    IRM_REPLY_RATE was incorrectly 100% (all 'unreplied').
  · After the field-name fix, factor_value.IRM_REPLY_RATE rows had to be
    invalidated and recomputed.

This script reads the cached irm_qa JSON directly, computes the rate, and
upserts factor_value. Pure SQL + arithmetic, no Tushare hits, ~1-2 minutes
for the whole market. Idempotent.

Run after fixing the IRM field-name bug, before any rank / industry-view
that mentions IRM_REPLY_RATE.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.core.report.timezones import bjt_now
from ifa.families.research.analyzer.factors import (
    FactorStatus,
    classify_lower_better,
    load_params,
)
from ifa.families.research.analyzer.profitability import SPECS as _PROFIT_SPECS  # noqa: F401
from ifa.families.research.analyzer.governance import SPECS as GOVERNANCE_SPECS

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    engine = get_engine()
    params = load_params()
    p = params.get("governance", {}).get("irm_no_reply_rate_pct", {})
    warn = float(p.get("warning_above", 10.0))
    crit = float(p.get("critical_above", 20.0))

    spec = GOVERNANCE_SPECS["IRM_REPLY_RATE"]
    today = bjt_now().date()

    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT ts_code, response_json
            FROM research.api_cache
            WHERE api_name = 'irm_qa'
              AND json_array_length(response_json) > 0
        """)).fetchall()

    log.info("recomputing IRM_REPLY_RATE for %d stocks", len(rows))

    payloads = []
    for ts_code, qa_list in rows:
        if not qa_list:
            continue
        total = len(qa_list)
        unreplied = sum(
            1 for r in qa_list
            if not (r.get("a") or r.get("reply") or r.get("answer")
                    or r.get("reply_content") or "").strip()
        )
        rate = Decimal(unreplied * 100) / Decimal(total) if total else None
        status = classify_lower_better(
            rate, warning_above=warn, critical_above=crit,
        )
        payloads.append({
            "ts_code": ts_code,
            "factor_name": "IRM_REPLY_RATE",
            "period": "",  # governance categoricals use empty period
            "family": "governance",
            "value": rate,
            "unit": spec.unit,
            "status": status.value,
            "direction": spec.direction,
            "computed_at": bjt_now(),
        })

    if not payloads:
        log.warning("no IRM data — exiting")
        return

    # Some stocks may have multiple period rows from earlier persists; we
    # write one canonical row with period=''.
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO research.factor_value (
                    ts_code, factor_name, period, family, value, unit,
                    status, direction, computed_at
                ) VALUES (
                    :ts_code, :factor_name, :period, :family, :value, :unit,
                    :status, :direction, :computed_at
                )
                ON CONFLICT (ts_code, factor_name, period) DO UPDATE SET
                    family      = EXCLUDED.family,
                    value       = EXCLUDED.value,
                    unit        = EXCLUDED.unit,
                    status      = EXCLUDED.status,
                    direction   = EXCLUDED.direction,
                    computed_at = EXCLUDED.computed_at
            """),
            payloads,
        )

    # Status distribution
    counts: dict[str, int] = {}
    for p in payloads:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    log.info("upserted %d rows; distribution: %s", len(payloads), counts)


if __name__ == "__main__":
    main()
