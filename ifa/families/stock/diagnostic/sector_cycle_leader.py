"""Persisted sector-cycle leader rank surface for Stock Edge diagnostics.

The table built here is a PIT diagnostic surface, not a production YAML signal.
It combines already-persisted SME orderflow/state/diffusion rows with the SME
daily SW membership snapshot for the same trade date.  Historical rows can be
recomputed because every output carries `logic_version`; callers should compare
versions instead of silently mixing scoring formulas.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

LOGIC_VERSION = "sector_cycle_leader_v1"


def backfill_sector_cycle_leader_daily(
    engine: Engine,
    *,
    start: dt.date,
    end: dt.date,
    logic_version: str = LOGIC_VERSION,
    l2_code: str | None = None,
) -> dict[str, Any]:
    """Compute and upsert stock.sector_cycle_leader_daily for a date window."""
    if end < start:
        raise ValueError("end must be >= start")
    params = {"start": start, "end": end, "logic_version": logic_version, "l2_code": l2_code}
    with engine.begin() as conn:
        result = conn.execute(text(_UPSERT_SQL), params)
        row_count = int(result.rowcount or 0)
        dates = conn.execute(text("""
            SELECT count(DISTINCT trade_date) AS date_count,
                   min(trade_date) AS min_date,
                   max(trade_date) AS max_date,
                   count(*) AS persisted_rows
            FROM stock.sector_cycle_leader_daily
            WHERE trade_date BETWEEN :start AND :end
              AND logic_version=:logic_version
              AND (CAST(:l2_code AS text) IS NULL OR l2_code=CAST(:l2_code AS text))
        """), params).mappings().one()
    return {
        "logic_version": logic_version,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "l2_code": l2_code,
        "upserted_rows": row_count,
        "persisted_rows": int(dates["persisted_rows"] or 0),
        "date_count": int(dates["date_count"] or 0),
        "min_date": dates["min_date"].isoformat() if dates["min_date"] else None,
        "max_date": dates["max_date"].isoformat() if dates["max_date"] else None,
    }


_UPSERT_SQL = """
WITH base AS (
    SELECT
        f.trade_date,
        f.ts_code,
        m.name,
        m.l1_code,
        m.l1_name,
        m.l2_code,
        m.l2_name,
        f.main_net_yuan,
        f.retail_net_yuan,
        f.amount_yuan,
        f.main_net_ratio,
        f.retail_net_ratio,
        f.quality_flag AS stock_quality_flag,
        so.main_net_ratio AS sector_main_net_ratio,
        so.retail_net_ratio AS sector_retail_net_ratio,
        so.top5_main_net_share,
        so.coverage_ratio,
        so.quality_flag AS sector_quality_flag,
        d.diffusion_score,
        d.diffusion_phase,
        so.price_positive_breadth,
        d.quality_flag AS diffusion_quality_flag,
        s.current_state,
        s.state_score,
        s.risk_flags_json,
        s.quality_flag AS state_quality_flag
    FROM sme.sme_stock_orderflow_daily f
    JOIN sme.sme_sw_member_daily m
      ON m.trade_date=f.trade_date AND m.ts_code=f.ts_code
    LEFT JOIN sme.sme_sector_orderflow_daily so
      ON so.trade_date=f.trade_date AND so.l2_code=m.l2_code
    LEFT JOIN sme.sme_sector_diffusion_daily d
      ON d.trade_date=f.trade_date AND d.l2_code=m.l2_code
    LEFT JOIN sme.sme_sector_state_daily s
      ON s.trade_date=f.trade_date AND s.l2_code=m.l2_code
    WHERE f.trade_date BETWEEN :start AND :end
      AND (CAST(:l2_code AS text) IS NULL OR m.l2_code=CAST(:l2_code AS text))
      AND f.amount_yuan IS NOT NULL
      AND f.amount_yuan > 0
),
scored AS (
    SELECT
        *,
        (
            0.55 * GREATEST(0.0, LEAST(1.0, 0.5 + COALESCE(main_net_ratio, 0.0) * 5.0)) +
            0.25 * GREATEST(0.0, LEAST(1.0, 0.5 + COALESCE(main_net_yuan::float / NULLIF(amount_yuan, 0), 0.0) * 5.0)) +
            0.20 * (1.0 - GREATEST(0.0, LEAST(1.0, 0.5 + COALESCE(retail_net_ratio, 0.0) * 5.0)))
        ) AS stock_score,
        (
            0.34 * GREATEST(0.0, LEAST(1.0, 0.5 + COALESCE(sector_main_net_ratio, 0.0) * 5.0)) +
            0.20 * COALESCE(diffusion_score, 0.5) +
            0.18 * COALESCE(state_score, 0.5) +
            0.14 * COALESCE(price_positive_breadth, 0.5) +
            0.14 * COALESCE(top5_main_net_share, 0.5)
        ) AS sector_score
    FROM base
),
ranked AS (
    SELECT
        *,
        (0.62 * stock_score + 0.38 * sector_score) AS leader_score,
        rank() OVER (
            PARTITION BY trade_date, l2_code
            ORDER BY (0.62 * stock_score + 0.38 * sector_score) DESC NULLS LAST,
                     main_net_yuan DESC NULLS LAST,
                     ts_code
        ) AS rank_in_sector,
        count(*) OVER (PARTITION BY trade_date, l2_code) AS sector_rank_count
    FROM scored
)
INSERT INTO stock.sector_cycle_leader_daily (
    trade_date, ts_code, name, l1_code, l1_name, l2_code, l2_name,
    rank_in_sector, sector_rank_count, leader_score, sector_score, stock_score,
    quality_flag, logic_version, evidence_json, updated_at
)
SELECT
    trade_date, ts_code, name, l1_code, l1_name, l2_code, l2_name,
    rank_in_sector::int, sector_rank_count::int, leader_score, sector_score, stock_score,
    CASE
        WHEN stock_quality_flag <> 'ok' OR COALESCE(sector_quality_flag, 'ok') <> 'ok' THEN 'degraded'
        WHEN diffusion_quality_flag IS NOT NULL AND diffusion_quality_flag <> 'ok' THEN 'degraded'
        WHEN state_quality_flag IS NOT NULL AND state_quality_flag <> 'ok' THEN 'degraded'
        ELSE 'computed'
    END AS quality_flag,
    :logic_version AS logic_version,
    jsonb_build_object(
        'source_tables', jsonb_build_array(
            'sme.sme_stock_orderflow_daily',
            'sme.sme_sw_member_daily',
            'sme.sme_sector_orderflow_daily',
            'sme.sme_sector_diffusion_daily',
            'sme.sme_sector_state_daily'
        ),
        'main_net_yuan', main_net_yuan,
        'retail_net_yuan', retail_net_yuan,
        'amount_yuan', amount_yuan,
        'main_net_ratio', main_net_ratio,
        'retail_net_ratio', retail_net_ratio,
        'sector_main_net_ratio', sector_main_net_ratio,
        'sector_retail_net_ratio', sector_retail_net_ratio,
        'diffusion_score', diffusion_score,
        'diffusion_phase', diffusion_phase,
        'current_state', current_state,
        'state_score', state_score,
        'risk_flags_json', risk_flags_json,
        'coverage_ratio', coverage_ratio
    ) AS evidence_json,
    now() AS updated_at
FROM ranked
ON CONFLICT (trade_date, ts_code, logic_version) DO UPDATE SET
    name=EXCLUDED.name,
    l1_code=EXCLUDED.l1_code,
    l1_name=EXCLUDED.l1_name,
    l2_code=EXCLUDED.l2_code,
    l2_name=EXCLUDED.l2_name,
    rank_in_sector=EXCLUDED.rank_in_sector,
    sector_rank_count=EXCLUDED.sector_rank_count,
    leader_score=EXCLUDED.leader_score,
    sector_score=EXCLUDED.sector_score,
    stock_score=EXCLUDED.stock_score,
    quality_flag=EXCLUDED.quality_flag,
    evidence_json=EXCLUDED.evidence_json,
    updated_at=now()
"""
