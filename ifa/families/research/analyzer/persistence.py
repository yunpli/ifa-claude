"""Persist FactorResult lists to research.factor_value.

Design choices:
  · Idempotent upsert keyed on (ts_code, factor_name, period). Re-running
    smoketest does not duplicate rows; computed_at is refreshed.
  · UNKNOWN factors are persisted too — the absence of a value is itself
    information for peer scans (so we know we tried and got nothing) and lets
    downstream code distinguish "not yet computed" from "computed and missing".
  · `value` is stored as Numeric(24,6); for booleans/categoricals (e.g.
    AUDIT_STANDARD) value=NULL and the status carries the verdict.
  · Notes + history go to JSONB so peer scans don't pay for them but reports
    can re-use the same row.

Public API:
  · persist_factor_results(engine, ts_code, results) → int (rows written)
  · persist_all_families(engine, ts_code, results_by_family) → int
  · load_factor_value(engine, ts_code, *, factor_name=None, period=None)
       → list[dict]   (audit / debugging)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.research.analyzer.factors import FactorResult

log = logging.getLogger(__name__)


def persist_factor_results(
    engine: Engine,
    ts_code: str,
    results: Iterable[FactorResult],
) -> int:
    """Upsert a batch of FactorResult rows. Returns count actually persisted.

    Each result becomes one row in research.factor_value. Period falls back to
    empty string so the PK is still well-defined for factors without a period
    concept (e.g. governance categoricals).
    """
    rows = []
    for r in results:
        rows.append({
            "ts_code": ts_code,
            "factor_name": r.spec.name,
            "period": r.period or "",
            "family": r.spec.family,
            "value": _to_numeric(r.value),
            "unit": r.spec.unit,
            "status": r.status.value,
            "direction": r.spec.direction,
            "peer_percentile": _to_numeric(r.peer_percentile),
            "peer_rank": r.peer_rank[0] if r.peer_rank else None,
            "peer_total": r.peer_rank[1] if r.peer_rank else None,
            "notes": json.dumps(r.notes, ensure_ascii=False) if r.notes else None,
            "history": json.dumps({"values": r.history,
                                   "periods": r.history_periods},
                                  ensure_ascii=False) if r.history else None,
            "computed_at": datetime.now(tz=timezone.utc),
        })
    if not rows:
        return 0

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO research.factor_value (
                    ts_code, factor_name, period, family, value, unit, status,
                    direction, peer_percentile, peer_rank, peer_total,
                    notes, history, computed_at
                ) VALUES (
                    :ts_code, :factor_name, :period, :family, :value, :unit, :status,
                    :direction, :peer_percentile, :peer_rank, :peer_total,
                    CAST(:notes AS JSONB), CAST(:history AS JSONB), :computed_at
                )
                ON CONFLICT (ts_code, factor_name, period) DO UPDATE SET
                    family          = EXCLUDED.family,
                    value           = EXCLUDED.value,
                    unit            = EXCLUDED.unit,
                    status          = EXCLUDED.status,
                    direction       = EXCLUDED.direction,
                    peer_percentile = EXCLUDED.peer_percentile,
                    peer_rank       = EXCLUDED.peer_rank,
                    peer_total      = EXCLUDED.peer_total,
                    notes           = EXCLUDED.notes,
                    history         = EXCLUDED.history,
                    computed_at     = EXCLUDED.computed_at
            """),
            rows,
        )
    return len(rows)


def persist_all_families(
    engine: Engine,
    ts_code: str,
    results_by_family: dict[str, list[FactorResult]],
) -> int:
    total = 0
    for results in results_by_family.values():
        total += persist_factor_results(engine, ts_code, results)
    return total


def load_factor_value(
    engine: Engine,
    ts_code: str,
    *,
    factor_name: str | None = None,
    period: str | None = None,
) -> list[dict]:
    """Read back persisted factor rows. Useful for audit and tests."""
    sql = """
        SELECT ts_code, factor_name, period, family, value, unit, status,
               direction, peer_percentile, peer_rank, peer_total,
               notes, history, computed_at
        FROM research.factor_value
        WHERE ts_code = :tc
    """
    params: dict = {"tc": ts_code}
    if factor_name is not None:
        sql += " AND factor_name = :fn"
        params["fn"] = factor_name
    if period is not None:
        sql += " AND period = :p"
        params["p"] = period
    sql += " ORDER BY family, factor_name"

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return [dict(r._mapping) for r in rows]


# ─── Internals ────────────────────────────────────────────────────────────────

def _to_numeric(v: object) -> Decimal | None:
    """Coerce factor values to Numeric-friendly Decimal; None passthrough."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return None
