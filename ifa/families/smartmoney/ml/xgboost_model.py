"""XGBoost wrapper for SmartMoney sector-up prediction.

M1 8GB safe defaults:
  n_estimators = 200   (not 1000)
  max_depth    = 6     (not 8+)
  tree_method  = 'hist' (memory-efficient histogram-based splits)
  nthread      = 2     (don't exhaust M1 cores)

Same interface as logistic.py and random_forest.py.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score
from xgboost import XGBClassifier

from .dataset import MLDataset

log = logging.getLogger(__name__)


class SmartMoneyXGBoost:
    model_name = "xgboost"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        mp = (params or {}).get("ml", {}).get("xgboost", {})
        self.n_estimators = int(mp.get("n_estimators", 200))
        self.max_depth = int(mp.get("max_depth", 6))
        self.learning_rate = float(mp.get("learning_rate", 0.05))
        self.subsample = float(mp.get("subsample", 0.8))
        self.colsample_bytree = float(mp.get("colsample_bytree", 0.8))
        self.min_child_weight = int(mp.get("min_child_weight", 10))
        self.reg_lambda = float(mp.get("reg_lambda", 1.0))
        self.nthread = int(mp.get("nthread", 2))

        self.clf = XGBClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            min_child_weight=self.min_child_weight,
            reg_lambda=self.reg_lambda,
            tree_method="hist",
            nthread=self.nthread,
            scale_pos_weight=1.0,  # override below after seeing class balance
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
            verbosity=0,
        )
        self._fitted = False
        self.feature_names: list[str] = []

    def fit(self, ds: MLDataset) -> "SmartMoneyXGBoost":
        self.feature_names = ds.feature_names
        # Set scale_pos_weight from training label balance
        n_neg = int(np.sum(ds.y_train == 0))
        n_pos = int(np.sum(ds.y_train == 1))
        if n_pos > 0:
            self.clf.set_params(scale_pos_weight=n_neg / n_pos)

        self.clf.fit(
            ds.X_train, ds.y_train,
            eval_set=[(ds.X_val, ds.y_val)] if len(ds.X_val) > 0 else None,
            verbose=False,
        )
        self._fitted = True
        log.info("[xgb] fitted: %d rows, %d features, n_pos/n_neg=%d/%d",
                 ds.n_train, ds.X_train.shape[1], n_pos, n_neg)
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
        log.info("[xgb] val AUC=%.3f acc=%.3f", metrics.get("val_auc", 0), metrics.get("val_accuracy", 0))
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
            "learning_rate": self.learning_rate,
            "fitted": self._fitted,
            "n_features": len(self.feature_names),
        }
