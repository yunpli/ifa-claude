"""Backfill ml_aggressive + ml_conservative recommendations for past trading days.

Why this exists:
  Before dual mode was introduced, evening reports only inserted heuristic
  recommendations. Now that we have active ML models, we can retroactively
  score the historical candidate pool and insert what the current models
  WOULD HAVE picked. This populates the consensus matrix with all 3 tracks
  for past dates so the tracking section shows ★★★+ stocks.

Important caveat:
  Dates BEFORE active.train_range_end are IN-SAMPLE for the current models.
  These backfilled picks are useful for *display* but should NOT be used
  for OOS evaluation. The weekly refresh's evaluation uses a separate
  rolling OOS window from candidates_daily/candidate_outcomes.

Usage:
    from ifa.families.ningbo.ml.backfill_dual import backfill_dual_recs
    backfill_dual_recs(engine, days_back=30)
"""
from __future__ import annotations

import datetime as dt
import json
import time
from typing import Callable

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from ifa.families.ningbo.ml.champion_challenger import (
    SLOT_AGGRESSIVE, SLOT_CONSERVATIVE, get_active_for_slot, load_model_artifact,
)
from ifa.families.ningbo.ml.dual_scorer import build_inference_features, _model_predict


TOP_N_PERSIST = 10
PER_STRATEGY_CAP = 3


def _trading_days_back(engine: Engine, on_date: dt.date, n: int) -> list[dt.date]:
    """Return last `n` trading days strictly before on_date (most recent last)."""
    sql = text("""
        SELECT cal_date FROM smartmoney.trade_cal
        WHERE exchange='SSE' AND is_open
          AND cal_date < :on_date AND cal_date >= :earliest
        ORDER BY cal_date
    """)
    earliest = on_date - dt.timedelta(days=n * 2)  # 2x buffer for weekends/holidays
    with engine.connect() as c:
        rows = c.execute(sql, {"on_date": on_date, "earliest": earliest}).fetchall()
    return [r[0] for r in rows[-n:]]


def _candidates_for_date(engine: Engine, rec_date: dt.date) -> pd.DataFrame:
    """Pull the full candidate pool for a date from candidates_daily."""
    sql = text("""
        SELECT ts_code, strategy, confidence_score, signal_meta, rec_price
        FROM ningbo.candidates_daily
        WHERE rec_date = :rd
        ORDER BY ts_code, strategy
    """)
    df = pd.read_sql(sql, engine, params={"rd": rec_date})
    if not df.empty:
        df = df.rename(columns={"signal_meta": "rec_signal_meta"})
        # Parse JSONB string to dict if needed
        df["rec_signal_meta"] = df["rec_signal_meta"].apply(
            lambda v: json.loads(v) if isinstance(v, str) else (v or {})
        )
    return df


def _picks_top_n(
    candidates_df: pd.DataFrame, scores: np.ndarray, scoring_mode: str,
    top_n: int = TOP_N_PERSIST, per_strategy_cap: int = PER_STRATEGY_CAP,
) -> list[dict]:
    """Apply per-strategy cap top-N selection on ML scores.

    Mirrors evening.py._picks_from_ml_scores but standalone (no TOP_N constants).
    """
    if scores is None or candidates_df.empty:
        return []
    # Normalize scores to [0, 1] per-day so they fit in NUMERIC and stay
    # comparable across modes/models (ensemble outputs raw negative ranks).
    s_min, s_max = float(np.min(scores)), float(np.max(scores))
    if s_max > s_min:
        norm_scores = (scores - s_min) / (s_max - s_min)
    else:
        norm_scores = np.zeros_like(scores)
    df = candidates_df.copy()
    df["_score"] = norm_scores
    df = df.sort_values("_score", ascending=False)

    picks: list[dict] = []
    seen_ts: set[str] = set()
    per_strat: dict[str, int] = {}
    for _, r in df.iterrows():
        ts = r["ts_code"]
        if ts in seen_ts:
            continue
        s = r["strategy"]
        if per_strat.get(s, 0) >= per_strategy_cap:
            continue
        all_strats = candidates_df[candidates_df["ts_code"] == ts]["strategy"].unique().tolist()
        is_multi = len(all_strats) >= 2
        picks.append({
            "ts_code": ts,
            "strategy": "multi" if is_multi else s,
            "rec_price": float(r["rec_price"]),
            "confidence_score": float(r["_score"]),
            "rec_signal_meta": json.dumps({
                "by_strategy": {
                    str(strat): {
                        "raw_score": float(candidates_df[
                            (candidates_df["ts_code"] == ts) &
                            (candidates_df["strategy"] == strat)
                        ]["confidence_score"].iloc[0]),
                    } for strat in all_strats
                },
                "strategies_hit": sorted(all_strats),
                "n_hits": len(all_strats),
                "ml_score": float(r["_score"]),
            }, default=str),
            "scoring_mode": scoring_mode,
            "param_version": f"{scoring_mode}_v1",
        })
        seen_ts.add(ts)
        per_strat[s] = per_strat.get(s, 0) + 1
        if len(picks) >= top_n:
            break
    return picks


def _insert_picks(engine: Engine, rec_date: dt.date, picks: list[dict]) -> int:
    if not picks:
        return 0
    sql = text("""
        INSERT INTO ningbo.recommendations_daily
            (rec_date, ts_code, strategy, scoring_mode, param_version,
             rec_price, confidence_score, rec_signal_meta, llm_narrative)
        VALUES
            (:rec_date, :ts_code, :strategy, :scoring_mode, :param_version,
             :rec_price, :confidence_score, :rec_signal_meta, NULL)
        ON CONFLICT (rec_date, ts_code, strategy, scoring_mode) DO UPDATE SET
            param_version    = EXCLUDED.param_version,
            rec_price        = EXCLUDED.rec_price,
            confidence_score = EXCLUDED.confidence_score,
            rec_signal_meta  = EXCLUDED.rec_signal_meta
    """)
    init_outcome_sql = text("""
        INSERT INTO ningbo.recommendation_outcomes
            (rec_date, ts_code, strategy, scoring_mode,
             outcome_status, outcome_track_day, outcome_date,
             final_cum_return, peak_cum_return, trough_cum_return, updated_at)
        VALUES
            (:rec_date, :ts_code, :strategy, :scoring_mode,
             'in_progress', NULL, NULL, 0.0, 0.0, 0.0, NOW())
        ON CONFLICT (rec_date, ts_code, strategy, scoring_mode) DO NOTHING
    """)
    rows = []
    for p in picks:
        rows.append({
            "rec_date": rec_date, **p,
        })
    with engine.begin() as c:
        c.execute(sql, rows)
        c.execute(init_outcome_sql, [{"rec_date": rec_date,
                                       "ts_code": p["ts_code"],
                                       "strategy": p["strategy"],
                                       "scoring_mode": p["scoring_mode"]}
                                      for p in picks])
    return len(picks)


def backfill_dual_recs(
    engine: Engine,
    *,
    days_back: int = 30,
    on_date: dt.date | None = None,
    on_log: Callable[[str], None] = print,
) -> dict:
    """Score historical candidate pools with current active ML models, persist top-10."""
    if on_date is None:
        on_date = dt.date.today()

    days = _trading_days_back(engine, on_date, days_back)
    on_log(f"\nBackfilling dual ML recs for last {len(days)} trading days "
           f"({days[0]} → {days[-1]})")

    # Load active models once
    agg_active  = get_active_for_slot(engine, SLOT_AGGRESSIVE)
    cons_active = get_active_for_slot(engine, SLOT_CONSERVATIVE)
    if not agg_active or not cons_active:
        on_log("⚠️  Missing active model in one or both slots — run weekly refresh first.")
        return {"days_processed": 0, "inserted": 0}

    on_log(f"  aggressive  active: {agg_active['model_version']}/{agg_active['model_name']}")
    on_log(f"  conservative active: {cons_active['model_version']}/{cons_active['model_name']}")

    agg_model  = load_model_artifact(agg_active["artifact_path"])
    cons_model = load_model_artifact(cons_active["artifact_path"])

    total_inserted = 0
    days_processed = 0
    t_start = time.time()

    for i, day in enumerate(days, 1):
        candidates_df = _candidates_for_date(engine, day)
        if candidates_df.empty:
            continue
        # Build features for this day
        try:
            X = build_inference_features(engine, candidates_df, day)
            scores_agg  = _model_predict(agg_model, X)
            scores_cons = _model_predict(cons_model, X)
        except Exception as exc:
            on_log(f"  ⚠️  {day}: scoring failed — {exc}")
            continue

        picks_agg  = _picks_top_n(candidates_df, scores_agg,  "ml_aggressive")
        picks_cons = _picks_top_n(candidates_df, scores_cons, "ml_conservative")
        n_a = _insert_picks(engine, day, picks_agg)
        n_c = _insert_picks(engine, day, picks_cons)
        total_inserted += (n_a + n_c)
        days_processed += 1

        if i % 5 == 0 or i == len(days):
            elapsed = time.time() - t_start
            on_log(f"  [{i}/{len(days)}] {day}  agg={n_a} cons={n_c}  "
                   f"({elapsed:.1f}s elapsed)")

    on_log(f"\n  Done. {days_processed} days, {total_inserted} recs inserted.")

    # Compute outcomes for newly inserted recs (bulk SQL)
    on_log("  Computing outcomes (bulk SQL)…")
    from ifa.families.ningbo.backfill import run_bulk_tracking_sql
    for sm in ("ml_aggressive", "ml_conservative"):
        out = run_bulk_tracking_sql(
            engine, days[0], on_date, scoring_mode=sm,
            on_log=lambda m: on_log(f"    {m}"),
        )
        on_log(f"  {sm}: {out['outcomes_upserted']} outcomes")

    return {"days_processed": days_processed, "inserted": total_inserted}
