"""Persisted daily risk veto facts for Stock Edge diagnostics.

This builder normalizes existing PIT-safe TA risk sources into
`stock.risk_veto_daily`.  It does not introduce a new production risk policy:
hard veto remains limited to suspension and hard/critical blacklist severities,
while limit and soft blacklist rows are persisted as soft risk evidence.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

LOGIC_VERSION = "risk_veto_v1"


def backfill_risk_veto_daily(
    engine: Engine,
    *,
    start: dt.date,
    end: dt.date,
    logic_version: str = LOGIC_VERSION,
    ts_code: str | None = None,
) -> dict[str, Any]:
    if end < start:
        raise ValueError("end must be >= start")
    params = {"start": start, "end": end, "logic_version": logic_version, "ts_code": ts_code}
    with engine.begin() as conn:
        result = conn.execute(text(_UPSERT_SQL), params)
        row_count = int(result.rowcount or 0)
        summary = conn.execute(text("""
            SELECT count(*) AS persisted_rows,
                   count(*) FILTER (WHERE hard_veto) AS hard_rows,
                   min(trade_date) AS min_date,
                   max(trade_date) AS max_date
            FROM stock.risk_veto_daily
            WHERE trade_date BETWEEN :start AND :end
              AND logic_version=:logic_version
              AND (CAST(:ts_code AS text) IS NULL OR ts_code=CAST(:ts_code AS text))
        """), params).mappings().one()
    return {
        "logic_version": logic_version,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "ts_code": ts_code,
        "upserted_rows": row_count,
        "persisted_rows": int(summary["persisted_rows"] or 0),
        "hard_rows": int(summary["hard_rows"] or 0),
        "min_date": summary["min_date"].isoformat() if summary["min_date"] else None,
        "max_date": summary["max_date"].isoformat() if summary["max_date"] else None,
    }


_UPSERT_SQL = """
WITH blacklist AS (
    SELECT
        trade_date,
        ts_code,
        CASE
            WHEN lower(COALESCE(severity, '')) IN ('hard', 'critical', 'high', 'severe') THEN 'hard_blacklist'
            ELSE 'soft_blacklist'
        END AS veto_category,
        lower(COALESCE(severity, '')) IN ('hard', 'critical', 'high', 'severe') AS hard_veto,
        severity,
        'ta.blacklist_daily' AS source_table,
        trade_date AS source_date,
        COALESCE(reason, ann_title) AS reason,
        jsonb_build_object('reason', reason, 'ann_title', ann_title) AS evidence_json
    FROM ta.blacklist_daily
    WHERE trade_date BETWEEN :start AND :end
      AND (CAST(:ts_code AS text) IS NULL OR ts_code=CAST(:ts_code AS text))
),
suspend AS (
    SELECT
        trade_date,
        ts_code,
        'suspension' AS veto_category,
        true AS hard_veto,
        suspend_type AS severity,
        'ta.suspend_daily' AS source_table,
        trade_date AS source_date,
        suspend_type AS reason,
        jsonb_build_object('suspend_type', suspend_type, 'suspend_timing', suspend_timing) AS evidence_json
    FROM ta.suspend_daily
    WHERE trade_date BETWEEN :start AND :end
      AND (CAST(:ts_code AS text) IS NULL OR ts_code=CAST(:ts_code AS text))
),
limit_events AS (
    SELECT
        trade_date,
        ts_code,
        'limit_event' AS veto_category,
        false AS hard_veto,
        "limit" AS severity,
        'ta.stk_limit_daily' AS source_table,
        trade_date AS source_date,
        "limit" AS reason,
        jsonb_build_object(
            'name', name,
            'pct_chg_pct', pct_chg_pct,
            'fc_ratio', fc_ratio,
            'fl_ratio', fl_ratio,
            'fd_amount_yuan', fd_amount_yuan,
            'open_times', open_times,
            'limit', "limit"
        ) AS evidence_json
    FROM ta.stk_limit_daily
    WHERE trade_date BETWEEN :start AND :end
      AND (CAST(:ts_code AS text) IS NULL OR ts_code=CAST(:ts_code AS text))
),
unioned AS (
    SELECT * FROM blacklist
    UNION ALL
    SELECT * FROM suspend
    UNION ALL
    SELECT * FROM limit_events
)
INSERT INTO stock.risk_veto_daily (
    trade_date, ts_code, veto_category, hard_veto, severity,
    source_table, source_date, reason, logic_version, evidence_json, updated_at
)
SELECT
    trade_date, ts_code, veto_category, hard_veto, severity,
    source_table, source_date, reason, :logic_version, evidence_json, now()
FROM unioned
ON CONFLICT (trade_date, ts_code, veto_category, source_table, source_date, logic_version) DO UPDATE SET
    hard_veto=EXCLUDED.hard_veto,
    severity=EXCLUDED.severity,
    reason=EXCLUDED.reason,
    evidence_json=EXCLUDED.evidence_json,
    updated_at=now()
"""
