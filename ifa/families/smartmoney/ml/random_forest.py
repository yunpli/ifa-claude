"""RandomForestClassifier wrapper for SmartMoney sector-up prediction.

More robust than logistic to non-linear factor interactions.
M1-safe defaults: n_estimators=100, max_depth=10, n_jobs=2.

Same interface as logistic.py and xgboost_model.py.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score

from .dataset import MLDataset

log = logging.getLogger(__name__)


class SmartMoneyRandomForest:
    model_name = "random_forest"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        mp = (params or {}).get("ml", {}).get("random_forest", {})
        self.n_estimators = int(mp.get("n_estimators", 100))
        self.max_depth = int(mp.get("max_depth", 10))
        self.min_samples_leaf = int(mp.get("min_samples_leaf", 10))
        self.class_weight = mp.get("class_weight", "balanced")
        self.n_jobs = int(mp.get("n_jobs", 2))

        self.clf = RandomForestClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            min_samples_leaf=self.min_samples_leaf,
            class_weight=self.class_weight,
            n_jobs=self.n_jobs,
            random_state=42,
        )
        self._fitted = False
        self.feature_names: list[str] = []

    def fit(self, ds: MLDataset) -> "SmartMoneyRandomForest":
        self.feature_names = ds.feature_names
        self.clf.fit(ds.X_train, ds.y_train)
        self._fitted = True
        log.info("[rf] fitted: %d rows, %d features, %d trees",
                 ds.n_train, ds.X_train.shape[1], self.n_estimators)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Model not fitted yet")
        return self.clf.predict_proba(X)[:, 1]

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Model not fitted yet")
        return self.clf.predict(X)

    def evaluate(self, ds: MLDataset) -> dict[str, float]:
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
        log.info("[rf] val AUC=%.3f acc=%.3f", metrics.get("val_auc", 0), metrics.get("val_accuracy", 0))
        return metrics

    def feature_importances(self) -> list[tuple[str, float]]:
        if not self._fitted:
            return []
        imp = self.clf.feature_importances_
        pairs = sorted(zip(self.feature_names, imp.tolist()), key=lambda x: x[1], reverse=True)
        return [(name, round(val, 6)) for name, val in pairs]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "n_estimators": self.n_estimators,
            "max_depth": self.max_depth,
            "min_samples_leaf": self.min_samples_leaf,
            "fitted": self._fitted,
            "n_features": len(self.feature_names),
        }
