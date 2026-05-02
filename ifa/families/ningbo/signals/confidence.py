"""Confidence scorer interface and implementations.

Strategy pattern: HeuristicScorer (Phase 1+) and MLScorer (Phase 3+) both
implement the ConfidenceScorer protocol so they're interchangeable
behind a `--scoring` CLI flag.

Both modes coexist post-Phase 3: each daily report renders heuristic
top-5 AND ml top-5 in separate sections.

Heuristic scoring delegates to per-strategy `confidence_score` already
computed by the strategy itself (sniper.py, treasure_basin.py, etc.).
The HeuristicScorer's role is to apply optional version-controlled
weights/multipliers and return a final 0-1 score.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


# Strategy-level multipliers (heuristic v1.0).  All 1.0 by default —
# diversity is enforced at the selection layer (per_strategy_cap), not by
# rebalancing weights.  Tweaks per future version go in the params dict.
DEFAULT_STRATEGY_WEIGHTS_V1 = {
    "sniper": 1.0,
    "treasure_basin": 1.0,
    "half_year_double": 1.0,
    "six_step": 0.85,          # baseline filter — slight downweight if surfacing alone
}


@runtime_checkable
class ConfidenceScorer(Protocol):
    """Common interface for heuristic and ML scorers."""

    mode: str  # 'heuristic' | 'ml'
    version: str  # e.g. 'v1.0' for heuristic, 'v2026.05' for ml

    def score(self, candidate: dict, context: dict) -> float:
        """Return confidence in [0, 1] for a strategy candidate.

        Args:
            candidate: dict with at minimum 'ts_code', 'strategy',
                       'confidence_score' (per-strategy raw score),
                       and 'signal_meta'.
            context:   market-level context (e.g. 'index_pct_chg',
                       'sector_flow_summary')
        """
        ...

    def explain(self, candidate: dict, context: dict) -> dict:
        """Return per-feature contribution dict (for SHAP / LLM)."""
        ...


class HeuristicScorer:
    """Phase 1.7 — rule-based scoring.

    For Phase 1, simply applies per-strategy multiplier and clamps to [0, 1].
    Future versions (v1.1+) can add market-regime adjustments.
    """

    mode = "heuristic"

    def __init__(self, version: str = "v1.0", params: dict | None = None):
        self.version = version
        self.params = params or {}
        self._weights = self.params.get("strategy_weights", DEFAULT_STRATEGY_WEIGHTS_V1)

    def score(self, candidate: dict, context: dict | None = None) -> float:
        raw = float(candidate.get("confidence_score", 0.0))
        strategy = candidate.get("strategy", "")
        weight = self._weights.get(strategy, 1.0)
        return float(max(0.0, min(1.0, raw * weight)))

    def explain(self, candidate: dict, context: dict | None = None) -> dict:
        raw = float(candidate.get("confidence_score", 0.0))
        strategy = candidate.get("strategy", "")
        weight = self._weights.get(strategy, 1.0)
        components = candidate.get("components", {})
        return {
            "raw_score": raw,
            "strategy": strategy,
            "strategy_weight": weight,
            "final_score": max(0.0, min(1.0, raw * weight)),
            "components": components,  # per-strategy components (e.g. trigger_w, vol_contraction)
        }


class MLScorer:
    """Phase 3.5 — ML-based scoring (RF/XGB/LGBM/CatBoost stacking)."""

    mode = "ml"

    def __init__(self, version: str = "v2026.05", model_path: str | None = None):
        self.version = version
        self._model_path = model_path
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            raise NotImplementedError("Phase 3.5 — load model from disk")

    def score(self, candidate: dict, context: dict | None = None) -> float:
        raise NotImplementedError("Phase 3.5")

    def explain(self, candidate: dict, context: dict | None = None) -> dict:
        raise NotImplementedError("Phase 3.8 — SHAP integration")
