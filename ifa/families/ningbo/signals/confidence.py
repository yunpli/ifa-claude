"""Confidence scorer interface and implementations.

Strategy pattern: HeuristicScorer (Phase 1+) and MLScorer (Phase 3+) both
implement the ConfidenceScorer protocol so they're interchangeable
behind a `--scoring` CLI flag.

Both modes coexist post-Phase 3: each daily report renders heuristic
top-5 AND ml top-5 in separate sections.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ConfidenceScorer(Protocol):
    """Common interface for heuristic and ML scorers."""

    mode: str  # 'heuristic' | 'ml'
    version: str  # e.g. 'v1.0' for heuristic, 'v2026.05' for ml

    def score(self, candidate: dict, context: dict) -> float:
        """Return confidence in [0, 1] for a strategy candidate."""
        ...

    def explain(self, candidate: dict, context: dict) -> dict:
        """Return per-feature contribution dict for debugging / SHAP / LLM."""
        ...


class HeuristicScorer:
    """Phase 1.7 — rule-based scoring per strategy.

    Each strategy supplies its own scoring function; this class dispatches.
    Output is heuristic but versioned (`heuristic_v1.0`, `heuristic_v1.1`...)
    so changes are tracked in `ningbo.strategy_params`.
    """

    mode = "heuristic"

    def __init__(self, version: str = "v1.0", params: dict | None = None):
        self.version = version
        self.params = params or {}

    def score(self, candidate: dict, context: dict) -> float:
        raise NotImplementedError("Phase 1.7")

    def explain(self, candidate: dict, context: dict) -> dict:
        raise NotImplementedError("Phase 1.7")


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

    def score(self, candidate: dict, context: dict) -> float:
        raise NotImplementedError("Phase 3.5")

    def explain(self, candidate: dict, context: dict) -> dict:
        raise NotImplementedError("Phase 3.8 — SHAP integration")
