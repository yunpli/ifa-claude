"""Persistence for ta.candidates_daily and ta.warnings_daily."""
from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.ta.setups.base import Candidate
from ifa.families.ta.setups.ranker import RankedCandidate
from ifa.families.ta.setups.recommended_price import (
    compute_recommended_price,
    merge_recommendations,
)

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
             regime_at_gen, evidence_json, in_top_watchlist,
             entry_price, stop_loss, target_price, rr_ratio, price_basis)
        VALUES
            (:trade_date, :ts_code, :setup_name, :rank, :final_score, :star_rating,
             :regime_at_gen, :evidence, :in_top_watchlist,
             :entry_price, :stop_loss, :target_price, :rr_ratio, :price_basis)
    """)
    # M10 P0.2 — compute ATR-based recommended prices per (setup hit), then
    # merge to per-stock conservative-side prices (max entry, max stop, min target).
    per_stock_setup_prices: dict[str, list] = {}
    per_setup_price: dict[tuple[str, str], object] = {}
    for rc in ranked:
        c = rc.candidate
        ev = c.evidence if isinstance(c.evidence, dict) else {}
        atr = ev.get("atr_pct_20d")
        entry_close = ev.get("close")
        rec_price = compute_recommended_price(
            c.setup_name, entry_close, atr, evidence=ev,
        )
        if rec_price is not None:
            per_setup_price[(c.ts_code, c.setup_name)] = rec_price
            per_stock_setup_prices.setdefault(c.ts_code, []).append(rec_price)
    per_stock_merged = {
        ts: merge_recommendations(plist)
        for ts, plist in per_stock_setup_prices.items()
    }

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
            # M10 P0.2 — embed recommended prices (per-setup + per-stock merged)
            ps = per_setup_price.get((c.ts_code, c.setup_name))
            if ps is not None:
                evidence_payload["rec_price_setup"] = {
                    "entry": ps.entry, "stop": ps.stop, "target": ps.target,
                    "rr": ps.rr, "entry_offset_atr": ps.entry_offset_atr,
                    "k_stop": ps.k_stop, "k_target": ps.k_target,
                }
            pm = per_stock_merged.get(c.ts_code)
            if pm is not None:
                evidence_payload["rec_price_stock"] = {
                    "entry": pm.entry, "stop": pm.stop, "target": pm.target,
                    "rr": pm.rr,
                }
            # M10 P1.1 — top-level price columns (per-stock merged price preferred,
            # else per-setup price). price_basis tells which one was used.
            pm = per_stock_merged.get(c.ts_code)
            ps = per_setup_price.get((c.ts_code, c.setup_name))
            chosen = pm or ps
            price_basis = "rec_price_stock" if pm else ("rec_price_setup" if ps else None)
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
                "entry_price": chosen.entry if chosen else None,
                "stop_loss": chosen.stop if chosen else None,
                "target_price": chosen.target if chosen else None,
                "rr_ratio": chosen.rr if chosen else None,
                "price_basis": price_basis,
            })
    return len(ranked)


def count_candidates(engine: Engine, on_date: date) -> int:
    sql = text("SELECT COUNT(*) FROM ta.candidates_daily WHERE trade_date = :d")
    with engine.connect() as conn:
        return conn.execute(sql, {"d": on_date}).scalar() or 0


def upsert_warnings(
    engine: Engine,
    on_date: date,
    warnings: list[Candidate],
    *,
    regime_at_gen: str | None = None,
) -> int:
    """Replace today's ta.warnings_daily rows with the latest D-family scan.

    Warnings live in their own table — not ranked, not filtered by Tier,
    not gated by Layer-1 sector rules. They surface in §13 风险扫描 of the
    evening report. PK = (trade_date, ts_code, setup_name).
    """
    sql_delete = text("DELETE FROM ta.warnings_daily WHERE trade_date = :d")
    sql_insert = text("""
        INSERT INTO ta.warnings_daily
            (trade_date, ts_code, setup_name, score, triggers, evidence,
             regime_at_gen, sector_role, sector_cycle_phase, in_long_universe)
        VALUES
            (:trade_date, :ts_code, :setup_name, :score, :triggers, :evidence,
             :regime_at_gen, :sector_role, :sector_cycle_phase, :in_long_universe)
        ON CONFLICT (trade_date, ts_code, setup_name) DO UPDATE SET
            score = EXCLUDED.score,
            triggers = EXCLUDED.triggers,
            evidence = EXCLUDED.evidence,
            regime_at_gen = EXCLUDED.regime_at_gen,
            sector_role = EXCLUDED.sector_role,
            sector_cycle_phase = EXCLUDED.sector_cycle_phase,
            in_long_universe = EXCLUDED.in_long_universe
    """)
    with engine.begin() as conn:
        conn.execute(sql_delete, {"d": on_date})
        for c in warnings:
            ev = c.evidence if isinstance(c.evidence, dict) else {}
            conn.execute(sql_insert, {
                "trade_date": on_date,
                "ts_code": c.ts_code,
                "setup_name": c.setup_name,
                "score": c.score,
                "triggers": list(c.triggers),
                "evidence": json.dumps(ev, ensure_ascii=False, default=str),
                "regime_at_gen": regime_at_gen,
                "sector_role": ev.get("sector_role"),
                "sector_cycle_phase": ev.get("sector_cycle_phase"),
                "in_long_universe": bool(ev.get("in_long_universe", True)),
            })
    return len(warnings)


def count_warnings(engine: Engine, on_date: date) -> int:
    sql = text("SELECT COUNT(*) FROM ta.warnings_daily WHERE trade_date = :d")
    with engine.connect() as conn:
        return conn.execute(sql, {"d": on_date}).scalar() or 0
