"""B8 training entrypoint: fit RF (1d) + XGB (20d) on SW L2 in-sample,
evaluate on OOS, persist models with version tag.

Pipeline per model:
  1. build_dataset(source='sw_l2', horizon_days=H, label='binary_top_quintile')
     over [in_sample_start, in_sample_end]
  2. fit on train portion (val_frac=0 means use all in-sample for training)
  3. evaluate on OOS dataset built from [oos_start, oos_end]
  4. save_model with version_tag, metrics, notes

Outputs:
  ~/claude/ifaenv/models/smartmoney/random_forest_v2026_05.pkl
  ~/claude/ifaenv/models/smartmoney/xgboost_v2026_05.pkl
  manifest.json updated

Usage:
  uv run python -m ifa.cli smartmoney train \\
    --in-sample-start 2021-01-04 --in-sample-end 2025-10-31 \\
    --oos-start 2025-11-01 --oos-end 2026-04-30 \\
    --version v2026_05
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from sqlalchemy.engine import Engine

from .dataset import build_dataset
from .features import ALL_FEATURE_COLS
from .persistence import save_model
from .random_forest import SmartMoneyRandomForest
from .xgboost_model import SmartMoneyXGBoost

log = logging.getLogger(__name__)


@dataclass
class TrainResult:
    model_name: str
    version_tag: str
    horizon_days: int
    in_sample_n: int
    oos_n: int
    in_sample_metrics: dict[str, float]
    oos_metrics: dict[str, float]
    top_features: list[tuple[str, float]]
    train_seconds: float


def _build_full_in_sample(
    engine: Engine,
    *,
    start: dt.date,
    end: dt.date,
    horizon_days: int,
    source: str = "sw_l2",
):
    """Build in-sample dataset using ALL dates as training (val_frac=0).

    We separately build OOS for honest OOS evaluation, so val_frac is not
    needed here.
    """
    return build_dataset(
        engine,
        train_start=start,
        train_end=end,
        val_frac=0.0001,  # tiny val (1 batch) just to satisfy the API
        label_scheme="binary_top_quintile",
        label_quantile=0.20,
        horizon_days=horizon_days,
        source=source,
    )


def _build_oos(
    engine: Engine,
    *,
    start: dt.date,
    end: dt.date,
    horizon_days: int,
    source: str = "sw_l2",
):
    """Build OOS dataset; we use val portion only (X_val, y_val)."""
    return build_dataset(
        engine,
        train_start=start,
        train_end=end,
        val_frac=1.0 - 0.0001,  # almost everything in val
        label_scheme="binary_top_quintile",
        label_quantile=0.20,
        horizon_days=horizon_days,
        source=source,
    )


def train_and_persist(
    engine: Engine,
    *,
    in_sample_start: dt.date,
    in_sample_end: dt.date,
    oos_start: dt.date,
    oos_end: dt.date,
    version_tag: str,
    source: str = "sw_l2",
    short_horizon: int = 1,
    long_horizon: int = 20,
    on_log: Callable[[str], None] = lambda m: None,
) -> tuple[TrainResult, TrainResult]:
    """Train RF (short horizon) + XGB (long horizon), evaluate on OOS, persist.

    Returns (rf_result, xgb_result). Both models saved to disk via save_model.
    """
    # ── RF (short horizon) ─────────────────────────────────────────────────
    on_log(f"[train] building RF in-sample ds (horizon={short_horizon}d)…")
    t0 = time.time()
    is_ds = _build_full_in_sample(
        engine, start=in_sample_start, end=in_sample_end,
        horizon_days=short_horizon, source=source,
    )
    on_log(f"[train]   in-sample: {is_ds.n_train:,} rows, {is_ds.X_train.shape[1]} feats")

    on_log(f"[train] building RF OOS ds…")
    oos_ds = _build_oos(
        engine, start=oos_start, end=oos_end,
        horizon_days=short_horizon, source=source,
    )
    on_log(f"[train]   OOS: {oos_ds.n_val:,} rows")

    on_log(f"[train] fitting RandomForest …")
    rf = SmartMoneyRandomForest()
    rf.fit(is_ds)
    is_metrics = rf.evaluate(is_ds)
    on_log(f"[train]   in-sample (degenerate val): {is_metrics}")

    # OOS eval: use rf's own evaluator on an MLDataset where ds.X_val/y_val = our OOS
    # The RF.evaluate uses ds.X_val/y_val so we just pass the OOS ds directly.
    oos_metrics = rf.evaluate(oos_ds)
    on_log(f"[train]   OOS:    {oos_metrics}")
    rf_seconds = time.time() - t0

    save_path = save_model(
        rf, model_name="random_forest", version_tag=version_tag,
        metrics={
            "in_sample_auc": is_metrics.get("val_auc"),
            "oos_auc": oos_metrics.get("val_auc"),
            "oos_accuracy": oos_metrics.get("val_accuracy"),
            "oos_precision": oos_metrics.get("val_precision"),
            "oos_recall": oos_metrics.get("val_recall"),
        },
        notes=f"B8 SW L2 RF short={short_horizon}d, "
              f"in-sample {in_sample_start}→{in_sample_end}, "
              f"OOS {oos_start}→{oos_end}, n_features={len(ALL_FEATURE_COLS)}",
    )
    on_log(f"[train]   saved RF → {save_path}")

    rf_result = TrainResult(
        model_name="random_forest",
        version_tag=version_tag,
        horizon_days=short_horizon,
        in_sample_n=is_ds.n_train,
        oos_n=oos_ds.n_val,
        in_sample_metrics=is_metrics,
        oos_metrics=oos_metrics,
        top_features=rf.feature_importances()[:20],
        train_seconds=rf_seconds,
    )

    # ── XGB (long horizon) ─────────────────────────────────────────────────
    on_log(f"[train] building XGB in-sample ds (horizon={long_horizon}d)…")
    t0 = time.time()
    is_ds = _build_full_in_sample(
        engine, start=in_sample_start, end=in_sample_end,
        horizon_days=long_horizon, source=source,
    )
    on_log(f"[train]   in-sample: {is_ds.n_train:,} rows")

    on_log(f"[train] building XGB OOS ds…")
    oos_ds = _build_oos(
        engine, start=oos_start, end=oos_end,
        horizon_days=long_horizon, source=source,
    )
    on_log(f"[train]   OOS: {oos_ds.n_val:,} rows")

    on_log(f"[train] fitting XGBoost …")
    xgb = SmartMoneyXGBoost()
    xgb.fit(is_ds)
    is_metrics_x = xgb.evaluate(is_ds)
    on_log(f"[train]   in-sample (degenerate val): {is_metrics_x}")
    oos_metrics_x = xgb.evaluate(oos_ds)
    on_log(f"[train]   OOS:    {oos_metrics_x}")
    xgb_seconds = time.time() - t0

    save_path = save_model(
        xgb, model_name="xgboost", version_tag=version_tag,
        metrics={
            "in_sample_auc": is_metrics_x.get("val_auc"),
            "oos_auc": oos_metrics_x.get("val_auc"),
            "oos_accuracy": oos_metrics_x.get("val_accuracy"),
            "oos_precision": oos_metrics_x.get("val_precision"),
            "oos_recall": oos_metrics_x.get("val_recall"),
        },
        notes=f"B8 SW L2 XGB long={long_horizon}d, "
              f"in-sample {in_sample_start}→{in_sample_end}, "
              f"OOS {oos_start}→{oos_end}, n_features={len(ALL_FEATURE_COLS)}",
    )
    on_log(f"[train]   saved XGB → {save_path}")

    xgb_result = TrainResult(
        model_name="xgboost",
        version_tag=version_tag,
        horizon_days=long_horizon,
        in_sample_n=is_ds.n_train,
        oos_n=oos_ds.n_val,
        in_sample_metrics=is_metrics_x,
        oos_metrics=oos_metrics_x,
        top_features=xgb.feature_importances()[:20],
        train_seconds=xgb_seconds,
    )

    return rf_result, xgb_result
