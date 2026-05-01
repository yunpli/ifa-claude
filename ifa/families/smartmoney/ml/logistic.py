"""LogisticRegression wrapper for SmartMoney sector-up prediction.

Fastest, most interpretable P1 model.  When in doubt, start here.

Interface is consistent with random_forest.py and xgboost_model.py so callers
can swap models without changing orchestration code.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.preprocessing import StandardScaler

from .dataset import MLDataset

log = logging.getLogger(__name__)


class SmartMoneyLogistic:
    """Wrapped LogisticRegression with standard fit/predict/evaluate interface.

    Attributes:
        model_name: Used for persistence keys and logging.
        params:     sklearn constructor kwargs (overridable from default.yaml
                    via params['ml']['logistic']).
    """

    model_name = "logistic"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        mp = (params or {}).get("ml", {}).get("logistic", {})
        self.C = float(mp.get("C", 1.0))
        self.max_iter = int(mp.get("max_iter", 1000))
        self.class_weight = mp.get("class_weight", "balanced")
        self.solver = mp.get("solver", "lbfgs")

        self.scaler = StandardScaler()
        self.clf = LogisticRegression(
            C=self.C,
            max_iter=self.max_iter,
            class_weight=self.class_weight,
            solver=self.solver,
            random_state=42,
        )
        self._fitted = False
        self.feature_names: list[str] = []

    # ── Training ──────────────────────────────────────────────────────────────

    def fit(self, ds: MLDataset) -> "SmartMoneyLogistic":
        """Fit scaler + classifier on the training split.

        Returns self for chaining.
        """
        self.feature_names = ds.feature_names
        X = self.scaler.fit_transform(ds.X_train)
        self.clf.fit(X, ds.y_train)
        self._fitted = True
        log.info("[logistic] fitted: %d rows, %d features", ds.n_train, X.shape[1])
        return self

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return probability of positive class (shape [n_samples])."""
        if not self._fitted:
            raise RuntimeError("Model not fitted yet")
        X_scaled = self.scaler.transform(X)
        return self.clf.predict_proba(X_scaled)[:, 1]

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Model not fitted yet")
        return self.clf.predict(self.scaler.transform(X))

    # ── Evaluation ────────────────────────────────────────────────────────────

    def evaluate(self, ds: MLDataset) -> dict[str, float]:
        """Evaluate on the validation split.

        Returns dict with val_auc, val_accuracy, val_precision, val_recall.
        """
        if not self._fitted or ds.X_val is None or len(ds.X_val) == 0:
            return {}

        proba = self.predict_proba(ds.X_val)
        preds = (proba >= 0.5).astype(int)

        metrics: dict[str, float] = {}
        try:
            metrics["val_auc"] = round(float(roc_auc_score(ds.y_val, proba)), 4)
        except Exception:
            metrics["val_auc"] = float("nan")

        n_correct = int(np.sum(preds == ds.y_val))
        metrics["val_accuracy"] = round(n_correct / len(ds.y_val), 4)

        n_pos_pred = int(np.sum(preds == 1))
        n_true_pos = int(np.sum((preds == 1) & (ds.y_val == 1)))
        metrics["val_precision"] = round(n_true_pos / n_pos_pred, 4) if n_pos_pred > 0 else 0.0
        n_actual_pos = int(np.sum(ds.y_val == 1))
        metrics["val_recall"] = round(n_true_pos / n_actual_pos, 4) if n_actual_pos > 0 else 0.0

        log.info("[logistic] val AUC=%.3f acc=%.3f prec=%.3f rec=%.3f",
                 metrics.get("val_auc", 0), metrics.get("val_accuracy", 0),
                 metrics.get("val_precision", 0), metrics.get("val_recall", 0))
        return metrics

    # ── Feature importance (coefficient magnitude) ────────────────────────────

    def feature_importances(self) -> list[tuple[str, float]]:
        """Return list of (feature_name, abs_coefficient) sorted descending."""
        if not self._fitted:
            return []
        coef = self.clf.coef_[0]
        pairs = sorted(
            zip(self.feature_names, np.abs(coef).tolist()),
            key=lambda x: x[1], reverse=True,
        )
        return [(name, round(val, 6)) for name, val in pairs]

    def to_dict(self) -> dict[str, Any]:
        """Serializable metadata (not the full model — use persistence.py for pickle)."""
        return {
            "model_name": self.model_name,
            "C": self.C,
            "max_iter": self.max_iter,
            "class_weight": self.class_weight,
            "fitted": self._fitted,
            "n_features": len(self.feature_names),
        }
