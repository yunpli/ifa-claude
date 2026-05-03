"""Dual-track scorer + consensus matrix builder for evening report.

For each evening report:
  1. Load active aggressive model + active conservative model from registry
  2. Score all candidates with both → produce two score arrays
  3. Build a consensus matrix combining heuristic + aggressive + conservative
  4. Each pick gets a weighted-rank score → ★1 to ★5 stars

Star score formula (per stock):
  weight_track = 6 - rank  if rank ≤ 5 else 0   (top-1 = 5 pts, top-5 = 1 pt)
  total_score  = sum over the 3 tracks
  stars: 13-15→★★★★★, 10-12→★★★★, 7-9→★★★, 4-6→★★, 1-3→★
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine

from ifa.families.ningbo.ml.champion_challenger import (
    SLOT_AGGRESSIVE, SLOT_CONSERVATIVE, get_active_for_slot, load_model_artifact,
)
from ifa.families.ningbo.ml.features    import FEATURE_COLUMNS
from ifa.families.ningbo.ml.features_v2 import (
    extract_features_from_meta_v2, _add_cross_sectional_v2,
)
from ifa.families.ningbo.ml.features    import (
    _calendar_features, _load_market_context, _load_stock_context,
    _load_sector_membership, _load_l2_momentum, _attach_sector_features,
)


class EnsembleWrapper:
    """Bundles multiple base models + does mean-rank ensembling at inference.

    Saves to disk as a single artifact (joblib-able). At score time, scores
    each member, converts to ranks, averages — same logic as training-time
    mean-rank ensemble.

    Note: rank-averaging requires per-day grouping (group by rec_date), so
    callers should use score_with_groups(X, group_ids) instead of plain
    predict_proba(X). For single-day inference (typical evening report),
    the entire X belongs to one group → simple ranking suffices.
    """

    def __init__(self, members: dict[str, Any]):
        self.members = members

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Single-group predict_proba (treats all rows as one rec_date).

        Returns (n, 2) where col 1 is ensemble score (NOT a probability —
        higher = better, but not calibrated to [0, 1]).
        """
        rank_arrs = []
        for _, m in self.members.items():
            scores = _model_predict(m, X)
            # Per the entire input (single day), rank descending; lower rank=better
            ranks = pd.Series(scores).rank(method="min", ascending=False).values
            rank_arrs.append(ranks)
        mean_rank = np.mean(rank_arrs, axis=0)
        ensemble_score = -mean_rank   # higher = better
        # Return as (n, 2) to match sklearn predict_proba convention
        return np.column_stack([np.zeros_like(ensemble_score), ensemble_score])


def _model_predict(model_obj: Any, X: np.ndarray) -> np.ndarray:
    """Score X using a model from the registry. Returns scores higher = better."""
    if isinstance(model_obj, EnsembleWrapper):
        return model_obj.predict_proba(X)[:, 1]
    elif isinstance(model_obj, tuple):
        # ('imputer_then_X', imputer, X)
        imputer, inner = model_obj[1], model_obj[2]
        X_imp = imputer.transform(X)
        if hasattr(inner, "predict_proba"):
            return inner.predict_proba(X_imp)[:, 1]
        return inner.predict(X_imp)
    elif isinstance(model_obj, list):
        # ensemble: list of base model names — caller should have wrapped these
        raise ValueError("Bare list of model names — should have been wrapped via EnsembleWrapper")
    elif hasattr(model_obj, "predict_proba"):
        return model_obj.predict_proba(X)[:, 1]
    else:
        return model_obj.predict(X)


def _ensemble_predict(
    base_objects: dict[str, Any],
    X: np.ndarray,
    candidates_df: pd.DataFrame,
) -> np.ndarray:
    """Mean-rank ensemble: each base model scores, convert to per-day rank, average."""
    rank_arrs = []
    for name, m in base_objects.items():
        scores = _model_predict(m, X)
        tmp = candidates_df[["rec_date"]].copy()
        tmp["_s"] = scores
        ranks = tmp.groupby("rec_date")["_s"].rank(method="min", ascending=False).values
        rank_arrs.append(ranks)
    mean_rank = np.mean(rank_arrs, axis=0)
    return -mean_rank   # invert so higher = better


def build_inference_features(
    engine: Engine, candidates_df: pd.DataFrame, rec_date: dt.date,
) -> np.ndarray:
    """Build the feature matrix for a single rec_date inference.

    candidates_df columns: ts_code, strategy, confidence_score, rec_signal_meta, rec_price
    Returns an (n, len(FEATURE_COLUMNS)) numpy array.
    """
    if candidates_df.empty:
        return np.zeros((0, len(FEATURE_COLUMNS)))

    feat_rows: list[dict] = []
    for r in candidates_df.itertuples(index=False):
        feats = extract_features_from_meta_v2(r.rec_signal_meta, r.strategy)
        feats["confidence_score"] = float(r.confidence_score)
        feats["n_hits"]   = 1.0
        feats["is_multi"] = 0.0
        feats.update(_calendar_features(rec_date))
        feat_rows.append(feats)
    feat_df = pd.DataFrame(feat_rows)

    # Stock + market + sector context
    ts_codes = candidates_df["ts_code"].unique().tolist()
    stock_ctx     = _load_stock_context(engine,  ts_codes, rec_date, rec_date)
    mkt_ctx       = _load_market_context(engine,           rec_date, rec_date)
    sec_member_df = _load_sector_membership(engine, ts_codes, rec_date, rec_date)
    sec_momentum  = _load_l2_momentum(engine,                rec_date, rec_date)

    base = candidates_df[["ts_code", "strategy", "rec_price"]].copy()
    base["rec_date"] = rec_date

    stock_cols = ["vol_20d", "return_20d", "turnover_5d_avg",
                  "log_market_cap", "vol_surge", "dist_60d_high", "dist_60d_low"]
    if not stock_ctx.empty:
        base = base.merge(
            stock_ctx[["ts_code", "trade_date"] + stock_cols],
            left_on=["ts_code", "rec_date"], right_on=["ts_code", "trade_date"], how="left",
        ).drop(columns=["trade_date"])
    else:
        for c in stock_cols: base[c] = np.nan
    if not mkt_ctx.empty:
        base = base.merge(mkt_ctx, left_on="rec_date", right_on="trade_date", how="left")
        base = base.drop(columns=["trade_date"], errors="ignore")
    else:
        for c in ("index_pct_chg","index_5d_return","index_above_ma20",
                  "index_5d_vol","index_10d_return","index_above_ma60"):
            base[c] = np.nan
    base = _attach_sector_features(base, sec_member_df, sec_momentum)

    feat_df["log_rec_price"] = np.log(np.maximum(base["rec_price"].astype(float), 0.01))
    for c in stock_cols + [
        "index_pct_chg","index_5d_return","index_above_ma20","index_5d_vol",
        "index_10d_return","index_above_ma60",
        "sector_l2_5d_return","sector_l2_5d_breadth","sector_l2_inflow_5d_norm",
    ]:
        feat_df[c] = base[c].astype(float)

    feat_df["rec_date"] = rec_date
    feat_df["ts_code"]  = candidates_df["ts_code"].values
    feat_df["strategy"] = candidates_df["strategy"].values
    feat_df = _add_cross_sectional_v2(feat_df)

    return feat_df[FEATURE_COLUMNS].values


def score_with_active_models(
    engine: Engine, candidates_df: pd.DataFrame, rec_date: dt.date,
) -> dict[str, np.ndarray]:
    """Score candidates with both active models (aggressive + conservative).

    Returns {"ml_aggressive": scores, "ml_conservative": scores, "heuristic": scores}.
    Missing slots get None.
    """
    if candidates_df.empty:
        return {}

    X = build_inference_features(engine, candidates_df, rec_date)

    out = {"heuristic": candidates_df["confidence_score"].values.astype(float)}

    for slot, key in [(SLOT_AGGRESSIVE, "ml_aggressive"),
                      (SLOT_CONSERVATIVE, "ml_conservative")]:
        active = get_active_for_slot(engine, slot)
        if active is None:
            out[key] = None
            continue
        try:
            model_obj = load_model_artifact(active["artifact_path"])
            out[key] = _model_predict(model_obj, X)
        except Exception as exc:
            print(f"  ⚠️  failed to score {slot}: {exc}")
            out[key] = None

    return out


# ── Consensus matrix ────────────────────────────────────────────────────────

def _per_track_top5_ranks(
    candidates_df: pd.DataFrame, scores: np.ndarray | None,
    top_n: int = 5, per_strategy_cap: int = 3,
) -> dict[str, int]:
    """Return {ts_code: rank_within_top5} for the picked top-5 of one track.

    Picks are made with per-strategy cap. Ranks are 1-based.
    """
    if scores is None:
        return {}
    df = candidates_df.copy()
    df["_score"] = scores
    df = df.sort_values("_score", ascending=False)
    picks: list[tuple[str, int]] = []
    per_strat: dict[str, int] = {}
    for _, r in df.iterrows():
        s = r["strategy"]
        if per_strat.get(s, 0) >= per_strategy_cap:
            continue
        picks.append((r["ts_code"], len(picks) + 1))
        per_strat[s] = per_strat.get(s, 0) + 1
        if len(picks) >= top_n:
            break
    # If a stock appears under multiple strategies, take its best (lowest) rank
    rank_map: dict[str, int] = {}
    for ts, rk in picks:
        if ts not in rank_map or rk < rank_map[ts]:
            rank_map[ts] = rk
    return rank_map


def build_consensus_matrix(
    candidates_df: pd.DataFrame,
    scores_by_track: dict[str, np.ndarray | None],
    top_n: int = 5, per_strategy_cap: int = 3,
) -> pd.DataFrame:
    """Combine 3 tracks' top-5 rankings into a consensus DataFrame.

    Returns rows for every stock that appears in any track's top-5, with:
      - ts_code, strategies (comma-list)
      - rank_heuristic / rank_aggressive / rank_conservative (None if not picked)
      - score_total (0-15)
      - stars (1-5)
    Sorted by score_total desc.
    """
    track_ranks: dict[str, dict[str, int]] = {}
    for key in ("heuristic", "ml_aggressive", "ml_conservative"):
        scores = scores_by_track.get(key)
        track_ranks[key] = _per_track_top5_ranks(
            candidates_df, scores, top_n=top_n, per_strategy_cap=per_strategy_cap,
        )

    all_ts = set()
    for d in track_ranks.values():
        all_ts.update(d.keys())

    rows = []
    for ts in all_ts:
        rh = track_ranks["heuristic"].get(ts)
        ra = track_ranks["ml_aggressive"].get(ts)
        rc = track_ranks["ml_conservative"].get(ts)
        # Per-track score = max(0, 6 - rank) if picked
        score = sum(max(0, 6 - r) for r in (rh, ra, rc) if r is not None)
        if score == 0:
            continue
        # Map to stars
        if   score >= 13: stars = 5
        elif score >= 10: stars = 4
        elif score >= 7:  stars = 3
        elif score >= 4:  stars = 2
        else:             stars = 1
        # Find this stock's strategies + rec_price
        sub = candidates_df[candidates_df["ts_code"] == ts]
        strategies = ",".join(sorted(sub["strategy"].unique()))
        rec_price = float(sub["rec_price"].iloc[0]) if "rec_price" in sub.columns else 0.0
        rows.append({
            "ts_code": ts,
            "strategies": strategies,
            "rank_heuristic": rh,
            "rank_aggressive": ra,
            "rank_conservative": rc,
            "score_total": score,
            "stars": stars,
            "rec_price": rec_price,
        })
    return pd.DataFrame(rows).sort_values(["score_total", "ts_code"], ascending=[False, True])
