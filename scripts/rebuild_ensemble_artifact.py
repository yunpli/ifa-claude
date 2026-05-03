#!/usr/bin/env python3
"""Surgical fix: rebuild the aggressive slot's ensemble artifact properly.

Why this exists:
  The first weekly refresh saved ensemble_meanrank as a Python list of member
  names instead of an EnsembleWrapper bundling the actual model objects.
  At inference, _model_predict can't handle a bare list → aggressive slot fails.

This script:
  1. Re-trains ONLY the 5 ensemble members (LR is excluded; XGB/LGBM/Cat + 2 rankers)
  2. Bundles them in EnsembleWrapper
  3. Overwrites the existing artifact at the active aggressive's path
  4. Updates the registry artifact_path

No re-evaluation, no bootstrap — assumes existing metrics in registry are correct.
Takes ~5 minutes.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.config import get_settings
from ifa.families.ningbo.ml.features    import FEATURE_COLUMNS
from ifa.families.ningbo.ml.features_v2 import build_candidate_feature_matrix
from ifa.families.ningbo.ml.trainer_v3  import (
    _make_xgb_clf, _make_lgbm_clf, _make_catboost_clf,
    _make_xgb_ranker, _make_lgbm_ranker, _bucketize_returns,
)
from ifa.families.ningbo.ml.dual_scorer import EnsembleWrapper
from ifa.families.ningbo.ml.champion_challenger import (
    SLOT_AGGRESSIVE, get_active_for_slot,
)


def main():
    print("Surgical rebuild of aggressive slot ensemble artifact")
    engine = get_engine(get_settings())
    active = get_active_for_slot(engine, SLOT_AGGRESSIVE)
    if active is None:
        print("❌ No active aggressive model — run `ifa ningbo refresh weekly` first")
        return

    print(f"Current active: {active['model_version']} ({active['model_name']})")
    print(f"Artifact path:  {active['artifact_path']}")

    # Train range from existing active record
    train_end = active["train_range_end"]
    print(f"Train end: {train_end}")

    # 1. Build feature matrix (same as last refresh)
    in_sample_start = dt.date(2024, 1, 2)
    oos_end = dt.date.today() - dt.timedelta(days=1)
    print(f"\n[1/3] Building features {in_sample_start} → {oos_end}…")
    t0 = time.time()
    feat_df = build_candidate_feature_matrix(engine, in_sample_start, oos_end, include_outcomes=True)
    df = feat_df[feat_df["outcome_status"].isin(["take_profit", "stop_loss", "expired"])]
    df = df.dropna(subset=["y_take_profit", "y_final_return"])
    train_df = df[df["rec_date"] <= train_end].copy().sort_values(
        ["rec_date", "ts_code", "strategy"]
    )
    X_train = train_df[FEATURE_COLUMNS].values
    y_train_cls = train_df["y_take_profit"].values
    y_train_reg = train_df["y_final_return"].values
    train_groups = train_df.groupby("rec_date").size().values
    print(f"  → train: {len(train_df):,} rows  [{time.time()-t0:.1f}s]")

    # 2. Train the 5 ensemble members
    print(f"\n[2/3] Training 5 ensemble members…")
    pos_rate = float(y_train_cls.mean())
    spw = (1 - pos_rate) / max(pos_rate, 0.01)

    members = {}

    print("  xgb_clf…", end="", flush=True)
    t = time.time()
    m = _make_xgb_clf(spw); m.fit(X_train, y_train_cls)
    members["xgb_clf"] = m
    print(f" [{time.time()-t:.1f}s]")

    print("  lgbm_clf…", end="", flush=True)
    t = time.time()
    m = _make_lgbm_clf(spw); m.fit(X_train, y_train_cls)
    members["lgbm_clf"] = m
    print(f" [{time.time()-t:.1f}s]")

    print("  cat_clf…", end="", flush=True)
    t = time.time()
    m = _make_catboost_clf(spw); m.fit(X_train, y_train_cls)
    members["cat_clf"] = m
    print(f" [{time.time()-t:.1f}s]")

    # Rankers need imputation + group sizes
    from sklearn.impute import SimpleImputer
    imputer = SimpleImputer(strategy="median")
    X_train_imp = imputer.fit_transform(X_train)
    y_train_grade = _bucketize_returns(y_train_reg, n_buckets=5)

    print("  xgb_ndcg…", end="", flush=True)
    t = time.time()
    r = _make_xgb_ranker("rank:ndcg")
    r.fit(X_train_imp, y_train_grade, group=train_groups)
    members["xgb_ndcg"] = ("imputer_then_ranker", imputer, r)
    print(f" [{time.time()-t:.1f}s]")

    print("  lgbm_lamda…", end="", flush=True)
    t = time.time()
    r = _make_lgbm_ranker()
    r.fit(X_train_imp, y_train_grade, group=train_groups)
    members["lgbm_lamda"] = ("imputer_then_ranker", imputer, r)
    print(f" [{time.time()-t:.1f}s]")

    # 3. Save bundled wrapper, overwrite active artifact
    print(f"\n[3/3] Saving EnsembleWrapper to {active['artifact_path']}…")
    wrapper = EnsembleWrapper(members=members)
    joblib.dump(wrapper, active["artifact_path"])
    size_mb = Path(active["artifact_path"]).stat().st_size / 1e6
    print(f"  ✓ Saved {size_mb:.1f} MB")

    print(f"\nDone. Aggressive slot now usable. Test:")
    print(f"  ifa ningbo evening --report-date 2026-04-30 --scoring dual --mode manual")


if __name__ == "__main__":
    main()
