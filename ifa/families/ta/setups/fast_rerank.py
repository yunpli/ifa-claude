"""Fast re-rank — skip context building + scan, re-rank existing candidates.

Use case: ranker-only param changes (a_size, b_size, concentration cap,
Q3 factor range, winrate floor, regime_boost). These don't change WHICH
candidates fire — only HOW Tier A/B is assigned and the stock_score.

Speedup: ~5-10x over full re-scan (skips build_contexts which is the
heavy SQL barrage).

Limitations:
  · Does NOT update entry_price / stop_loss / target_price (those depend
    on ATR k_stop/k_target — separate fast_reprice path needed).
  · Does NOT re-evaluate position_events_daily (still uses original prices).
  · Does NOT change which setups fire (use full re-scan if setup gates change).

Usage:
    from ifa.families.ta.setups.fast_rerank import fast_rerank_window
    fast_rerank_window(engine, start_date, end_date)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.ta.setups.base import Candidate
from ifa.families.ta.setups.ranker import rank

log = logging.getLogger(__name__)


def _load_candidates_for_day(engine: Engine, on_date: date) -> tuple[list[Candidate], dict]:
    """Reconstruct Candidate list from candidates_daily + warnings_daily for a date.

    Returns (candidates_list, candidate_id_map) where map is {(ts_code, setup_name): candidate_id}
    so we can UPDATE-by-id later.
    """
    sql = text("""
        SELECT candidate_id, ts_code, setup_name, evidence_json
        FROM ta.candidates_daily
        WHERE trade_date = :d
    """)
    candidates: list[Candidate] = []
    id_map: dict[tuple[str, str], str] = {}
    with engine.connect() as conn:
        for row in conn.execute(sql, {"d": on_date}):
            ev = row[3] if isinstance(row[3], dict) else {}
            triggers = tuple(ev.get("triggers", []))
            score = ev.get("raw_score") or ev.get("score") or 0.0
            # Strip ranker-injected fields from evidence so re-rank is clean
            clean_ev = {
                k: v for k, v in ev.items()
                if k not in (
                    "tier", "stock_score", "raw_stock_score", "rank",
                    "star_rating", "in_top_watchlist", "sector_factor",
                    "resonance_count", "resonance_families",
                    "governance_status", "rec_price_setup", "rec_price_stock",
                    # Keep raw_score, triggers, sector_role/phase/quality, atr_pct_20d,
                    # in_long_universe, sw_l2_code, entry_close — these are scanner-time data
                )
            }
            cand = Candidate(
                ts_code=row[1],
                trade_date=on_date,
                setup_name=row[2],
                score=float(score),
                triggers=triggers,
                evidence=clean_ev,
            )
            candidates.append(cand)
            id_map[(row[1], row[2])] = str(row[0])
    return candidates, id_map


def fast_rerank_one_day(engine: Engine, on_date: date) -> int:
    """Re-rank existing candidates for one date. Returns rows updated.

    No re-scan, no context building. Just reads existing candidates_daily,
    re-runs rank() with current yaml params, and UPDATEs tier / final_score /
    star_rating / in_top_watchlist / evidence_json.

    Position prices (entry/stop/target) and position_events are NOT touched.
    """
    # Load regime + setup_metrics (same as full pipeline)
    with engine.connect() as conn:
        regime = conn.execute(
            text("SELECT regime FROM ta.regime_daily WHERE trade_date = :d"),
            {"d": on_date},
        ).scalar()
        latest = conn.execute(
            text("SELECT MAX(trade_date) FROM ta.setup_metrics_daily WHERE trade_date < :d"),
            {"d": on_date},
        ).scalar()
        setup_metrics: dict = {}
        if latest:
            for r in conn.execute(text("""
                SELECT setup_name, decay_score, suitable_regimes,
                       winrate_60d, regime_winrates, combined_score_60d
                FROM ta.setup_metrics_daily WHERE trade_date = :d
            """), {"d": latest}):
                setup_metrics[r[0]] = {
                    "decay_score": float(r[1]) if r[1] else None,
                    "suitable_regimes": list(r[2]) if r[2] else [],
                    "winrate_60d": float(r[3]) if r[3] else None,
                    "regime_winrates": r[4] if isinstance(r[4], dict) else {},
                    "combined_score_60d": float(r[5]) if r[5] is not None else None,
                }

    candidates, id_map = _load_candidates_for_day(engine, on_date)
    if not candidates:
        return 0

    ranked = rank(candidates, top_n=200,
                  current_regime=regime, setup_metrics=setup_metrics)

    # Build update plan: candidate_id → new (tier, stock_score, star, in_top, evidence_overlay)
    sql_update = text("""
        UPDATE ta.candidates_daily
        SET final_score = :final_score,
            star_rating = :star,
            in_top_watchlist = :in_top,
            rank = :rank,
            evidence_json = (evidence_json::jsonb || CAST(:overlay AS jsonb))::json
        WHERE candidate_id = CAST(:cid AS UUID)
    """)
    sql_clear_dropped = text("""
        UPDATE ta.candidates_daily
        SET in_top_watchlist = false,
            evidence_json = (evidence_json::jsonb - 'tier' - 'stock_score'
                             - 'rank' - 'star_rating')::json
        WHERE trade_date = :d
          AND candidate_id NOT IN (SELECT CAST(unnest(:keep_ids) AS UUID))
    """)

    n = 0
    keep_ids: list[str] = []
    with engine.begin() as conn:
        for rc in ranked:
            cid = id_map.get((rc.candidate.ts_code, rc.candidate.setup_name))
            if not cid:
                continue
            keep_ids.append(cid)
            overlay = {
                "tier": rc.tier,
                "stock_score": rc.stock_score,
                "raw_stock_score": rc.raw_stock_score,
                "sector_factor": rc.sector_factor,
                "resonance_count": rc.resonance_count,
                "resonance_families": list(rc.resonance_families),
                "governance_status": rc.governance_status,
                "rank": rc.rank,
                "star_rating": rc.star_rating,
            }
            conn.execute(sql_update, {
                "final_score": rc.stock_score,
                "star": rc.star_rating,
                "in_top": rc.in_top_watchlist,
                "rank": rc.rank,
                "cid": cid,
                "overlay": json.dumps(overlay, ensure_ascii=False, default=str),
            })
            n += 1
        # Clear tier/stock_score for candidates that didn't make it into any tier this run
        if keep_ids:
            conn.execute(sql_clear_dropped, {"d": on_date, "keep_ids": keep_ids})
    return n


def fast_rerank_window(engine: Engine, start: date, end: date,
                       progress_every: int = 30) -> int:
    """Re-rank all dates in window. Returns total rows updated."""
    with engine.connect() as conn:
        dates = [
            r[0] for r in conn.execute(text("""
                SELECT DISTINCT trade_date FROM ta.candidates_daily
                WHERE trade_date BETWEEN :s AND :e
                ORDER BY trade_date
            """), {"s": start, "e": end})
        ]
    log.info("fast_rerank %d dates [%s..%s]", len(dates), start, end)
    t0 = time.time()
    total = 0
    for i, d in enumerate(dates):
        total += fast_rerank_one_day(engine, d)
        if (i + 1) % progress_every == 0:
            log.info("  %d/%d (%ds, %d rows)", i + 1, len(dates), int(time.time() - t0), total)
    log.info("done %d rows in %ds", total, int(time.time() - t0))
    return total


if __name__ == "__main__":
    import argparse
    from ifa.core.db import get_engine

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    args = ap.parse_args()
    fast_rerank_window(
        get_engine(),
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
    )
