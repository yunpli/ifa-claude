"""Persistence for ta.candidates_daily."""
from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.ta.setups.ranker import RankedCandidate

log = logging.getLogger(__name__)


def upsert_candidates(
    engine: Engine,
    on_date: date,
    ranked: list[RankedCandidate],
    *,
    regime_at_gen: str | None = None,
) -> int:
    """Replace today's candidates_daily rows with the new ranking. Returns row count."""
    # Tracking rows reference candidate_id via FK; delete them first.
    sql_delete_tracking = text("""
        DELETE FROM ta.candidate_tracking
        WHERE candidate_id IN (
            SELECT candidate_id FROM ta.candidates_daily WHERE trade_date = :d
        )
    """)
    sql_delete = text("DELETE FROM ta.candidates_daily WHERE trade_date = :d")
    sql_insert = text("""
        INSERT INTO ta.candidates_daily
            (trade_date, ts_code, setup_name, rank, final_score, star_rating,
             regime_at_gen, evidence_json, in_top_watchlist)
        VALUES
            (:trade_date, :ts_code, :setup_name, :rank, :final_score, :star_rating,
             :regime_at_gen, :evidence, :in_top_watchlist)
    """)
    with engine.begin() as conn:
        conn.execute(sql_delete_tracking, {"d": on_date})
        conn.execute(sql_delete, {"d": on_date})
        for rc in ranked:
            c = rc.candidate
            evidence_payload = {
                **c.evidence,
                "raw_score": c.score,                       # per-setup pure score
                "triggers": list(c.triggers),
                "governance_status": rc.governance_status,
                "stock_score": rc.stock_score,
                "raw_stock_score": rc.raw_stock_score,
                "sector_factor": rc.sector_factor,
                "resonance_count": rc.resonance_count,
                "resonance_families": list(rc.resonance_families),
                "tier": rc.tier,
                # M9.7 — entry_close locked at scan time (推荐价 immutable)
                "entry_close": (c.evidence or {}).get("close")
                              if isinstance(c.evidence, dict) else None,
                # SmartMoney sector context for transparency
                "sector_role": rc.sector_role,
                "sector_phase": rc.sector_cycle_phase,
            }
            conn.execute(sql_insert, {
                "trade_date": on_date,
                "ts_code": c.ts_code,
                "setup_name": c.setup_name,
                "rank": rc.rank,
                # final_score now stores the per-stock aggregate (used for star);
                # raw per-candidate score lives in evidence_json
                "final_score": rc.stock_score,
                "star_rating": rc.star_rating,
                "regime_at_gen": regime_at_gen,
                "evidence": json.dumps(evidence_payload, ensure_ascii=False, default=str),
                "in_top_watchlist": rc.in_top_watchlist,
            })
    return len(ranked)


def count_candidates(engine: Engine, on_date: date) -> int:
    sql = text("SELECT COUNT(*) FROM ta.candidates_daily WHERE trade_date = :d")
    with engine.connect() as conn:
        return conn.execute(sql, {"d": on_date}).scalar() or 0
