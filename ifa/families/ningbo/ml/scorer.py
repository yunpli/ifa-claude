"""ML inference scorer — Phase 3.4.

Implements the ConfidenceScorer protocol (see signals/confidence.py) so it
can drop-in replace HeuristicScorer in the selection pipeline.

Single-rec inference path:
    candidate dict (from select_top_n) → feature vector → model.predict_proba
    → P(take_profit) score in [0, 1]

Batch inference path (used by report rendering when ranking many candidates):
    candidates DataFrame → feature matrix → model.predict_proba → scores

Important: the model expects features in the EXACT order of FEATURE_COLUMNS.
We rely on extract_features_from_meta + market context joined externally.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine

from ifa.families.ningbo.ml.features import (
    FEATURE_COLUMNS, _calendar_features, extract_features_from_meta,
    _load_market_context, _load_stock_context,
)
from ifa.families.ningbo.ml.registry import load_active_model, load_model


class MLScorer:
    """ML-based scorer using a saved stacking ensemble.

    Loaded once at construction time; thereafter score() is fast (single-row
    predict_proba). For batch scoring of many candidates, prefer
    score_batch() which calls predict_proba once for the full matrix.
    """

    mode = "ml"

    def __init__(
        self,
        version: str | None = None,
        *,
        engine: Engine | None = None,
        market_ctx: dict | None = None,
        stock_ctx_lookup: dict[str, dict] | None = None,
    ):
        """Args:
            version: Specific model version to load; None → load active.
            engine:  DB engine (needed for fetching stock/market context for
                     fresh inference; not needed if context is pre-supplied).
            market_ctx: Pre-fetched market context for the rec_date being
                        scored (dict with keys index_pct_chg, etc.).  Set this
                        once per rec_date to avoid per-rec DB queries.
            stock_ctx_lookup: Pre-fetched per-(ts_code) context dict (e.g.
                        {"600519.SH": {"vol_20d": 0.02, ...}}).  Set this
                        once per rec_date for batch scoring.
        """
        if version is not None:
            self.model, self.metadata = load_model(version)
        else:
            loaded = load_active_model()
            if loaded is None:
                raise FileNotFoundError(
                    "No active ningbo ML model. Run `ifa ningbo train` first, "
                    "then `ifa ningbo params freeze ml <version>`."
                )
            self.model, self.metadata = loaded

        self.version = self.metadata["model_version"]
        self.feature_columns = self.metadata["feature_columns"]
        if list(self.feature_columns) != list(FEATURE_COLUMNS):
            # Schema drift — fail fast rather than score garbage
            raise ValueError(
                f"Model feature schema mismatch.  Saved: {self.feature_columns}.  "
                f"Current FEATURE_COLUMNS: {FEATURE_COLUMNS}.  "
                f"Retrain with `ifa ningbo train` and freeze the new version."
            )

        self._engine = engine
        self._market_ctx = market_ctx
        self._stock_ctx_lookup = stock_ctx_lookup or {}

    # ── Per-candidate scoring ────────────────────────────────────────────────

    def score(self, candidate: dict, context: dict | None = None) -> float:
        """Return P(take_profit | features) in [0, 1] for one candidate."""
        feats = self._candidate_to_feature_vector(candidate, context or {})
        X = np.array([[feats[c] for c in FEATURE_COLUMNS]], dtype=float)
        p = float(self.model.predict_proba(X)[0, 1])
        return p

    def explain(self, candidate: dict, context: dict | None = None) -> dict:
        """Return per-feature contribution dict.

        Without SHAP installed, we report:
          - feature values for this candidate
          - global feature importances (from training metadata)
          - approximate contribution = abs(value_normalized) * global_importance
        """
        feats = self._candidate_to_feature_vector(candidate, context or {})
        global_imp = self.metadata.get("metrics", {}).get("stacking", {}).get(
            "feature_importances", {}
        )
        # Stacking has its own structure — fall back to xgb if stacking has no FI
        if not global_imp:
            global_imp = self.metadata.get("metrics", {}).get("xgb", {}).get(
                "feature_importances", {}
            )

        contribs = {
            c: {
                "value": feats[c],
                "global_importance": global_imp.get(c, 0.0),
            }
            for c in FEATURE_COLUMNS
        }
        score = self.score(candidate, context)
        return {
            "model_version": self.version,
            "score": score,
            "contributions": contribs,
        }

    # ── Batch scoring (preferred for ranking many candidates) ────────────────

    def score_batch(self, candidates_df: pd.DataFrame, rec_date) -> np.ndarray:
        """Score many candidates at once. Returns array of P(take_profit) scores.

        candidates_df must have: ts_code, strategy, confidence_score,
        rec_signal_meta (dict).  rec_date is a single date applied to all rows.
        """
        if candidates_df.empty:
            return np.array([])

        # Build feature rows
        feat_rows: list[dict] = []
        for r in candidates_df.itertuples(index=False):
            cand = {
                "ts_code": r.ts_code,
                "strategy": r.strategy,
                "confidence_score": float(r.confidence_score),
                "rec_signal_meta": getattr(r, "rec_signal_meta", None),
            }
            ctx = {"rec_date": rec_date}
            if self._market_ctx is not None:
                ctx.update(self._market_ctx)
            if r.ts_code in self._stock_ctx_lookup:
                ctx.update(self._stock_ctx_lookup[r.ts_code])
            feat_rows.append(self._candidate_to_feature_vector(cand, ctx))

        X = np.array([[fr[c] for c in FEATURE_COLUMNS] for fr in feat_rows], dtype=float)
        return self.model.predict_proba(X)[:, 1]

    # ── Internal: build feature vector from a candidate dict ─────────────────

    def _candidate_to_feature_vector(self, candidate: dict, context: dict) -> dict[str, float]:
        meta = candidate.get("rec_signal_meta") or candidate.get("signal_meta") or {}
        feats = extract_features_from_meta(meta, candidate.get("strategy", ""))
        feats["confidence_score"] = float(candidate.get("confidence_score", 0.0))

        # Calendar features from rec_date
        rec_date = context.get("rec_date")
        if rec_date is None:
            # Fallback: today (won't normally happen — caller should pass)
            import datetime as _dt
            rec_date = _dt.date.today()
        feats.update(_calendar_features(rec_date))

        # Market context (must be supplied in `context` or via constructor)
        for col in ("index_pct_chg", "index_5d_return", "index_above_ma20", "index_5d_vol"):
            v = context.get(col)
            if v is None and self._market_ctx is not None:
                v = self._market_ctx.get(col)
            feats[col] = float(v) if v is not None else float("nan")

        # Stock context
        for col in ("vol_20d", "return_20d", "turnover_5d_avg"):
            v = context.get(col)
            if v is None and candidate.get("ts_code") in self._stock_ctx_lookup:
                v = self._stock_ctx_lookup[candidate["ts_code"]].get(col)
            feats[col] = float(v) if v is not None else float("nan")

        # Stock price context
        rec_price = context.get("rec_price") or candidate.get("rec_price")
        if rec_price is not None and rec_price > 0:
            feats["log_rec_price"] = float(np.log(max(rec_price, 0.01)))
        else:
            feats["log_rec_price"] = float("nan")

        return feats


def prefetch_context(
    engine: Engine, ts_codes: list[str], rec_date,
) -> tuple[dict | None, dict[str, dict]]:
    """Prefetch market + stock context for one rec_date.

    Useful for evening report inference: call once before scoring 50-200
    candidates so we don't hit the DB per-candidate.

    Returns (market_ctx_dict, stock_ctx_lookup).
    """
    import datetime as _dt
    if not isinstance(rec_date, _dt.date):
        rec_date = _dt.date.fromisoformat(str(rec_date))

    mkt = _load_market_context(engine, rec_date, rec_date)
    market_ctx: dict | None = None
    if not mkt.empty:
        row = mkt[mkt["trade_date"] == rec_date].iloc[0] if (mkt["trade_date"] == rec_date).any() else mkt.iloc[-1]
        market_ctx = {
            "index_pct_chg":    float(row["index_pct_chg"])    if pd.notna(row["index_pct_chg"])    else 0.0,
            "index_5d_return":  float(row["index_5d_return"])  if pd.notna(row["index_5d_return"])  else 0.0,
            "index_above_ma20": float(row["index_above_ma20"]) if pd.notna(row["index_above_ma20"]) else 0.0,
            "index_5d_vol":     float(row["index_5d_vol"])     if pd.notna(row["index_5d_vol"])     else 0.0,
        }

    stock = _load_stock_context(engine, ts_codes, rec_date, rec_date)
    stock_ctx: dict[str, dict] = {}
    if not stock.empty:
        for _, row in stock.iterrows():
            stock_ctx[row["ts_code"]] = {
                "vol_20d":         float(row["vol_20d"])         if pd.notna(row["vol_20d"])         else float("nan"),
                "return_20d":      float(row["return_20d"])      if pd.notna(row["return_20d"])      else float("nan"),
                "turnover_5d_avg": float(row["turnover_5d_avg"]) if pd.notna(row["turnover_5d_avg"]) else float("nan"),
            }

    return market_ctx, stock_ctx
