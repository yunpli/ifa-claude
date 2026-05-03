"""Phase 3.B feature builder — reads from the FULL candidate pool.

Differences from features.py (v1):
  - Source table: ningbo.candidates_daily  (not recommendations_daily)
  - Source label: ningbo.candidate_outcomes (not recommendation_outcomes)
  - Multi-strategy detection: a stock with >1 row on the same rec_date
    is flagged via cs_n_strategies_for_stock and has_* columns.
  - Cross-sectional features now meaningful: rank within ~150 candidates
    of that day (not within 5 already-filtered picks).

Feature schema retains the same FEATURE_COLUMNS as features.py to allow
unified MLScorer inference (one scoring path, two training data sources).
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from ifa.families.ningbo.ml.features import (
    FEATURE_COLUMNS, _STRIKE_CODE_MAP, _calendar_features,
    _attach_sector_features, _load_sector_membership, _load_l2_momentum,
    _load_market_context, _load_stock_context,
)


def extract_features_from_meta_v2(meta: dict | str | None, strategy: str) -> dict[str, float]:
    """Like extract_features_from_meta but for raw strategy hits (not multi).

    The candidates_daily table stores ONE row per (ts_code, strategy) hit.
    Multi-strategy resonance is computed externally via cs_n_strategies_for_stock.
    Here we just extract the strategy-specific features from this one strategy's
    signal_meta.
    """
    if meta is None:
        meta = {}
    elif isinstance(meta, str):
        meta = json.loads(meta)

    out: dict[str, float] = {}

    # Strategy one-hot (single strategy here, multi computed externally)
    out["has_sniper"] = float(strategy == "sniper")
    out["has_basin"]  = float(strategy == "treasure_basin")
    out["has_hyd"]    = float(strategy == "half_year_double")
    # is_multi + n_hits set externally by cross-sectional pass

    # Strategy-specific
    out["sniper_strike_code"]      = float(_STRIKE_CODE_MAP.get(meta.get("strike_type"), 0)) if strategy == "sniper" else 0.0
    out["sniper_touch_precision"]  = float(meta.get("touch_precision", 0.0))  if strategy == "sniper" else 0.0
    out["sniper_rebound_strength"] = float(meta.get("rebound_strength", 0.0)) if strategy == "sniper" else 0.0
    out["sniper_vol_contraction"]  = float(meta.get("vol_contraction", 0.0))  if strategy == "sniper" else 0.0
    out["sniper_cross_freshness"]  = float(meta.get("cross_freshness", 0.0))  if strategy == "sniper" else 0.0
    out["basin_pattern_strength"]  = float(meta.get("pattern_strength", meta.get("strength_score", 0.0))) if strategy == "treasure_basin" else 0.0
    out["hyd_weekly_score"]        = float(meta.get("weekly_score", meta.get("weekly_macd_strength", 0.0))) if strategy == "half_year_double" else 0.0
    out["hyd_daily_score"]         = float(meta.get("daily_score", meta.get("daily_alignment", 0.0))) if strategy == "half_year_double" else 0.0

    # Heuristic baselines that are now per-row (not multi-aggregated)
    out["resonance_boost"]        = 0.0  # filled by cross-sectional
    out["best_individual_score"]  = float(meta.get("confidence_score",
                                          meta.get("strength_score", 0.0)))

    return out


# ── Cross-sectional features (now meaningful with ~150 candidates per day) ──

def _add_cross_sectional_v2(df: pd.DataFrame) -> pd.DataFrame:
    """Within-day cross-sectional features over the FULL candidate pool.

    Now operates on ~150 candidates/day rather than 5, so the ranks and
    counts are genuinely informative.
    """
    out = df.copy()
    g = out.groupby("rec_date")

    # cs_rank_confidence: 1 = highest conf in day's pool
    out["cs_rank_confidence"] = g["confidence_score"].rank(method="min", ascending=False)

    # Total candidates that day (market opportunity proxy)
    out["cs_n_picks_day"] = g["confidence_score"].transform("count")

    # n_hits = how many distinct strategies fire on this stock that day
    # Drop the placeholder column first to avoid pandas merge suffix collision.
    if "n_hits" in out.columns:
        out = out.drop(columns=["n_hits"])
    n_strategies_per_stock = (
        df.groupby(["rec_date", "ts_code"])["strategy"].nunique()
          .reset_index().rename(columns={"strategy": "n_hits"})
    )
    out = out.merge(n_strategies_per_stock, on=["rec_date", "ts_code"], how="left")
    # Override the per-row "is_multi" with the actual cross-strategy count
    out["is_multi"] = (out["n_hits"] >= 2).astype(float)
    # cs_n_multi_day: how many multi-hit stocks on this day
    multi_per_day = out.groupby("rec_date")["is_multi"].transform("sum")
    out["cs_n_multi_day"] = multi_per_day

    # If a stock has multiple rows (multi-strategy), boost its has_* flags
    # so a single row reflects ALL its strategy hits.
    has_per_stock = (
        df.assign(
            has_s=lambda d: (d["strategy"] == "sniper").astype(float),
            has_b=lambda d: (d["strategy"] == "treasure_basin").astype(float),
            has_h=lambda d: (d["strategy"] == "half_year_double").astype(float),
        )
        .groupby(["rec_date", "ts_code"])[["has_s", "has_b", "has_h"]]
        .max()
        .reset_index()
    )
    out = out.merge(has_per_stock, on=["rec_date", "ts_code"], how="left")
    out["has_sniper"] = out["has_s"]
    out["has_basin"]  = out["has_b"]
    out["has_hyd"]    = out["has_h"]
    out = out.drop(columns=["has_s", "has_b", "has_h"])

    # resonance_boost: 0.15 per extra strategy hit (mirroring heuristic logic)
    out["resonance_boost"] = (out["n_hits"] - 1).clip(lower=0) * 0.15

    return out


# ── Main builder ─────────────────────────────────────────────────────────────

def build_candidate_feature_matrix(
    engine: Engine,
    rec_date_start: dt.date,
    rec_date_end: dt.date,
    *,
    include_outcomes: bool = True,
    require_complete_outcome: bool = True,
) -> pd.DataFrame:
    """Build the full feature matrix sourced from the complete candidate pool.

    Args:
        engine:                   SQLAlchemy engine.
        rec_date_start/end:       Inclusive date range.
        include_outcomes:         If True, JOIN with candidate_outcomes and
                                  add label columns.
        require_complete_outcome: If True, filter to candidates that have
                                  a non-in_progress outcome (excludes recent
                                  candidates whose 15-day window isn't done).
    """
    # ── 1. Pull candidates + outcomes ───────────────────────────────────────
    sql = """
        SELECT
            c.rec_date, c.ts_code, c.strategy,
            c.confidence_score, c.rec_price, c.signal_meta
    """
    if include_outcomes:
        sql += """,
            o.outcome_status,
            o.final_cum_return,
            o.peak_cum_return,
            o.trough_cum_return,
            o.outcome_track_day
        """
    sql += """
        FROM ningbo.candidates_daily c
    """
    if include_outcomes:
        sql += """
        LEFT JOIN ningbo.candidate_outcomes o
               ON c.rec_date = o.rec_date
              AND c.ts_code  = o.ts_code
              AND c.strategy = o.strategy
        """
    sql += """
        WHERE c.rec_date BETWEEN :start AND :end
        ORDER BY c.rec_date, c.ts_code, c.strategy
    """
    candidates = pd.read_sql(text(sql), engine, params={
        "start": rec_date_start, "end": rec_date_end,
    })
    if candidates.empty:
        return candidates

    if include_outcomes and require_complete_outcome:
        candidates = candidates[
            candidates["outcome_status"].isin(["take_profit", "stop_loss", "expired"])
        ].copy()
        if candidates.empty:
            return candidates

    # ── 2. Per-row strategy-specific features ───────────────────────────────
    feat_rows: list[dict[str, Any]] = []
    for r in candidates.itertuples(index=False):
        feats = extract_features_from_meta_v2(r.signal_meta, r.strategy)
        feats["confidence_score"] = float(r.confidence_score)
        feats["n_hits"] = 1.0  # placeholder, fixed by cross-sectional
        feats["is_multi"] = 0.0  # placeholder
        feats.update(_calendar_features(r.rec_date))
        feat_rows.append(feats)
    feat_df = pd.DataFrame(feat_rows)

    # ── 3. Stock + market + L2 sector context ───────────────────────────────
    ts_codes = candidates["ts_code"].unique().tolist()
    stock_ctx     = _load_stock_context(engine, ts_codes, rec_date_start, rec_date_end)
    mkt_ctx       = _load_market_context(engine,           rec_date_start, rec_date_end)
    sec_member_df = _load_sector_membership(engine, ts_codes, rec_date_start, rec_date_end)
    sec_momentum  = _load_l2_momentum(engine,                rec_date_start, rec_date_end)

    base = candidates[["rec_date", "ts_code", "strategy", "rec_price"]].copy()
    stock_cols = ["vol_20d", "return_20d", "turnover_5d_avg",
                  "log_market_cap", "vol_surge", "dist_60d_high", "dist_60d_low"]
    if not stock_ctx.empty:
        base = base.merge(
            stock_ctx[["ts_code", "trade_date"] + stock_cols],
            left_on=["ts_code", "rec_date"], right_on=["ts_code", "trade_date"],
            how="left",
        ).drop(columns=["trade_date"])
    else:
        for c in stock_cols:
            base[c] = np.nan

    if not mkt_ctx.empty:
        base = base.merge(mkt_ctx, left_on="rec_date", right_on="trade_date", how="left")
        base = base.drop(columns=["trade_date"], errors="ignore")
    else:
        for c in ("index_pct_chg", "index_5d_return", "index_above_ma20", "index_5d_vol",
                  "index_10d_return", "index_above_ma60"):
            base[c] = np.nan

    base = _attach_sector_features(base, sec_member_df, sec_momentum)

    # ── 4. Combine ──────────────────────────────────────────────────────────
    feat_df["log_rec_price"] = np.log(np.maximum(base["rec_price"].astype(float), 0.01))
    for c in stock_cols + [
        "index_pct_chg", "index_5d_return", "index_above_ma20", "index_5d_vol",
        "index_10d_return", "index_above_ma60",
        "sector_l2_5d_return", "sector_l2_5d_breadth", "sector_l2_inflow_5d_norm",
    ]:
        feat_df[c] = base[c].astype(float)

    # ── 5. Cross-sectional features (now over ~150 candidates per day) ─────
    feat_df["rec_date"]  = candidates["rec_date"].values
    feat_df["ts_code"]   = candidates["ts_code"].values
    feat_df["strategy"]  = candidates["strategy"].values
    feat_df = _add_cross_sectional_v2(feat_df)

    # ── 6. Reorder & attach IDs/labels ──────────────────────────────────────
    out = pd.concat([
        candidates[["rec_date", "ts_code", "strategy"]].reset_index(drop=True),
        feat_df[FEATURE_COLUMNS].reset_index(drop=True),
    ], axis=1)

    if include_outcomes:
        out["outcome_status"]   = candidates["outcome_status"].values
        out["final_cum_return"] = candidates["final_cum_return"].astype(float).values
        out["peak_cum_return"]  = candidates["peak_cum_return"].astype(float).values
        out["y_take_profit"]    = (candidates["outcome_status"] == "take_profit").astype(int)
        out["y_final_return"]   = candidates["final_cum_return"].astype(float)

    return out
