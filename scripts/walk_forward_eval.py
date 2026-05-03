#!/usr/bin/env python3
"""Walk-forward CV evaluation — test if model performance is stable over time.

Splits OOS period into 3 consecutive monthly buckets and runs the trained
model on each, reporting per-bucket Top5_Mean / NDCG@5. If performance is
concentrated in one bucket only, the model is likely overfitting.

Usage:
    uv run python scripts/walk_forward_eval.py
"""
from __future__ import annotations

import datetime as dt
import time

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table


def main():
    console = Console()
    console.print("[bold cyan]Walk-Forward Evaluation[/bold cyan]")
    console.print("Tests stability across consecutive OOS periods\n")

    is_start = dt.date(2024, 1, 2)
    is_end   = dt.date(2025, 9, 30)
    oos_end  = dt.date(2026, 4, 30)

    # Define rolling OOS buckets (~2 months each)
    oos_buckets = [
        ("Oct-Nov 2025", dt.date(2025, 10, 1), dt.date(2025, 11, 30)),
        ("Dec 2025-Jan 2026", dt.date(2025, 12, 1), dt.date(2026, 1, 31)),
        ("Feb-Mar 2026", dt.date(2026, 2, 1),  dt.date(2026, 3, 31)),
        ("Apr 2026", dt.date(2026, 4, 1),  dt.date(2026, 4, 30)),
    ]

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.features_v2 import build_candidate_feature_matrix
    from ifa.families.ningbo.ml.kronos_features import attach_kronos_embeddings
    from ifa.families.ningbo.ml.trainer_v3 import (
        train_models_v3, _select_top_n_per_day, _per_day_top5_returns,
        _ndcg_per_day, _max_drawdown,
    )

    engine = get_engine(get_settings())

    console.print("[bold]Building feature matrix (with Kronos)…[/bold]")
    t0 = time.time()
    feat_df = build_candidate_feature_matrix(engine, is_start, oos_end, include_outcomes=True)
    feat_df = attach_kronos_embeddings(engine, feat_df)
    console.print(f"  {feat_df.shape}  [{time.time()-t0:.1f}s]\n")

    console.print("[bold]Training V3+Kronos…[/bold]")
    t0 = time.time()
    art = train_models_v3(
        feat_df, in_sample_end=is_end,
        use_tabnet=False, use_kronos_features=True,
        on_log=lambda m: console.print(f"  [dim]{m}[/dim]"),
    )
    console.print(f"  done in {time.time()-t0:.1f}s\n")

    # ── Walk-forward evaluation per model per bucket ────────────────────────
    feat_cols = art.feature_columns
    df = feat_df.copy()
    df = df[df["outcome_status"].isin(["take_profit", "stop_loss", "expired"])]
    df = df.dropna(subset=["y_take_profit", "y_final_return"])

    models_to_check = ["ensemble_meanrank", "xgb_ndcg", "xgb_clf", "lgbm_clf", "cat_clf", "heuristic"]

    console.print(f"[bold cyan]Walk-Forward by OOS bucket[/bold cyan]\n")
    t = Table(show_header=True, header_style="bold")
    t.add_column("Bucket")
    t.add_column("N days", justify="right")
    t.add_column("N cands", justify="right")
    for name in models_to_check:
        t.add_column(name)

    for bucket_name, b_start, b_end in oos_buckets:
        bucket_df = df[(df["rec_date"] >= b_start) & (df["rec_date"] <= b_end)].copy()
        if bucket_df.empty:
            continue
        n_days = bucket_df["rec_date"].nunique()
        n_cands = len(bucket_df)
        row = [bucket_name, str(n_days), str(n_cands)]

        X_bucket = bucket_df[feat_cols].values
        y_bucket = bucket_df["y_take_profit"].values

        for name in models_to_check:
            if name == "heuristic":
                scores = bucket_df["confidence_score"].values
            else:
                model = art.base_models.get(name)
                if model is None:
                    row.append("—")
                    continue
                # Compute scores
                if isinstance(model, list):
                    # ensemble: rank-average
                    rank_arrs = []
                    for m_name in model:
                        m_obj = art.base_models[m_name]
                        scores_m = _model_predict(m_obj, X_bucket)
                        tmp = bucket_df[["rec_date"]].copy()
                        tmp["_s"] = scores_m
                        rank_arrs.append(
                            tmp.groupby("rec_date")["_s"].rank(method="min", ascending=False).values
                        )
                    scores = -np.mean(rank_arrs, axis=0)
                else:
                    scores = _model_predict(model, X_bucket)

            picks = _select_top_n_per_day(bucket_df, scores)
            if picks.empty:
                row.append("—")
                continue
            mean_ret = picks["final_cum_return"].mean()
            row.append(f"{mean_ret*100:+.2f}%")

        t.add_row(*row)
    console.print(t)

    console.print(f"\n[bold]Interpretation:[/bold]")
    console.print("  - If ensemble +Kronos is consistently >+1% across all 4 buckets → robust.")
    console.print("  - If it's huge in one bucket but flat elsewhere → recent-period luck.")
    console.print("  - If Apr 2026 is bad → market regime shift, model needs retrain.")


def _model_predict(model, X):
    """Handle the model wrapper variants (Pipeline / tuple-wrapper)."""
    if isinstance(model, tuple):
        # ('imputer_then_X', imputer, X)
        imputer = model[1]
        inner = model[2]
        X_imp = imputer.transform(X)
        if hasattr(inner, "predict_proba"):
            return inner.predict_proba(X_imp)[:, 1]
        return inner.predict(X_imp)
    elif hasattr(model, "predict_proba"):
        return model.predict_proba(X)[:, 1]
    else:
        return model.predict(X)


if __name__ == "__main__":
    main()
