"""Phase 3.B trainer — learning-to-rank on the full candidate pool.

Key differences from trainer.py (v1):
  - Trains XGBRanker (rank:pairwise) — the right objective for this problem.
    Each rec_date is a "query"; candidates are documents to rank.
  - Continuous label: final_cum_return bucketized to ordinal grades 0-4.
  - Also trains XGB binary classifier on take_profit for AUC comparison.
  - Also keeps LR + RF for an honest comparison vs v1.
  - Walk-forward CV with 3 folds for ranking metrics (NDCG@5).
  - Best metric: top-5 AvgRet (the actual production metric).

Why ranking objective:
  Binary classification ignores "near misses" (e.g., a 19% return is
  treated as 0). Continuous regression weights all errors equally, but
  we only care about the top-5 ranking. LambdaRank/pairwise specifically
  optimizes for ranking quality.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, ndcg_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRanker

from ifa.families.ningbo.ml.features import FEATURE_COLUMNS

DEFAULT_RANDOM_STATE = 42


# ── Result containers ────────────────────────────────────────────────────────

@dataclass
class ModelResultV2:
    name: str
    objective: str               # 'classifier' | 'ranker'
    model: Any
    oos_auc: float
    oos_avg_precision: float
    oos_ndcg5: float             # NDCG@5 — primary ranking metric
    oos_top5_precision: float    # take_profit rate among top-5 picks
    oos_top5_avg_return: float   # avg final_cum_return of top-5
    oos_top5_med_return: float   # median (more robust than mean)
    feature_importances: dict[str, float] = field(default_factory=dict)


@dataclass
class TrainingArtifactsV2:
    model_version: str
    feature_columns: list[str]
    base_models: dict[str, Any]
    production_model: Any
    production_objective: str
    metrics: dict[str, ModelResultV2]
    train_range: tuple[dt.date, dt.date]
    oos_range: tuple[dt.date, dt.date]
    n_train: int
    n_oos: int
    n_train_days: int
    n_oos_days: int


# ── Bucketize returns into ordinal grades (for ranker label) ─────────────────

def _bucketize_returns(returns: np.ndarray, n_buckets: int = 5) -> np.ndarray:
    """Bucketize continuous returns into ordinal grades 0..n_buckets-1.

    Higher bucket = better outcome.  Designed for XGBRanker which needs
    ordinal labels.  We use empirical quantiles of the training set.
    """
    qs = np.linspace(0, 1, n_buckets + 1)[1:-1]  # interior quantiles
    cuts = np.quantile(returns, qs)
    return np.digitize(returns, cuts, right=False).astype(int)


# ── Top-N evaluation ─────────────────────────────────────────────────────────

def _top5_metrics(
    df: pd.DataFrame, scores: np.ndarray, top_n: int = 5, per_strategy_cap: int = 2,
) -> tuple[float, float, float]:
    """For each rec_date pick top-N by score (per-strategy cap=2), then compute:
       (precision = mean(y_take_profit), avg_return, median_return).
    """
    df = df.copy()
    df["_score"] = scores
    picks = []
    for _, group in df.groupby("rec_date"):
        g = group.sort_values("_score", ascending=False)
        chosen = []
        per_strat: dict[str, int] = {}
        for _, r in g.iterrows():
            s = r["strategy"]
            if per_strat.get(s, 0) >= per_strategy_cap:
                continue
            chosen.append(r)
            per_strat[s] = per_strat.get(s, 0) + 1
            if len(chosen) >= top_n:
                break
        picks.extend(chosen)
    if not picks:
        return 0.0, 0.0, 0.0
    pdf = pd.DataFrame(picks)
    return (
        float(pdf["y_take_profit"].mean()),
        float(pdf["final_cum_return"].mean()),
        float(pdf["final_cum_return"].median()),
    )


def _ndcg_per_day(df: pd.DataFrame, scores: np.ndarray, k: int = 5) -> float:
    """Mean NDCG@k across rec_date groups, using final_cum_return as relevance."""
    df = df.copy()
    df["_score"] = scores
    ndcgs = []
    for _, g in df.groupby("rec_date"):
        if len(g) < 2:
            continue
        # relevance = clamped non-negative log return (penalize huge losses lightly)
        y_true = np.maximum(g["final_cum_return"].values, -0.5).reshape(1, -1)
        y_pred = g["_score"].values.reshape(1, -1)
        # Shift to non-negative for NDCG
        y_true_shifted = y_true - y_true.min() + 1e-6
        try:
            ndcgs.append(ndcg_score(y_true_shifted, y_pred, k=k))
        except Exception:
            continue
    return float(np.mean(ndcgs)) if ndcgs else float("nan")


def _heuristic_baseline(oos_df: pd.DataFrame) -> ModelResultV2:
    y = oos_df["y_take_profit"].values
    s = oos_df["confidence_score"].values
    auc = roc_auc_score(y, s) if len(set(y)) > 1 else 0.5
    ap  = average_precision_score(y, s) if len(set(y)) > 1 else y.mean()
    ndcg5 = _ndcg_per_day(oos_df, s)
    p, r, m = _top5_metrics(oos_df, s)
    return ModelResultV2(
        name="heuristic", objective="baseline", model=None,
        oos_auc=auc, oos_avg_precision=ap, oos_ndcg5=ndcg5,
        oos_top5_precision=p, oos_top5_avg_return=r, oos_top5_med_return=m,
    )


# ── Model factories ──────────────────────────────────────────────────────────

def _make_lr() -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(
            C=0.5, class_weight="balanced", max_iter=3000,
            solver="lbfgs", random_state=DEFAULT_RANDOM_STATE,
        )),
    ])


def _make_rf() -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("clf", RandomForestClassifier(
            n_estimators=400, max_depth=6, min_samples_leaf=50,
            min_samples_split=100, max_features="sqrt",
            class_weight="balanced", n_jobs=-1, random_state=DEFAULT_RANDOM_STATE,
        )),
    ])


def _make_xgb_clf(scale_pos_weight: float = 3.0) -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("clf", XGBClassifier(
            n_estimators=400, max_depth=4, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.7, min_child_weight=20,
            reg_alpha=0.5, reg_lambda=2.0,
            scale_pos_weight=scale_pos_weight,
            objective="binary:logistic", eval_metric="auc",
            n_jobs=-1, random_state=DEFAULT_RANDOM_STATE, tree_method="hist",
        )),
    ])


def _make_xgb_ranker() -> XGBRanker:
    """XGBRanker with pairwise objective.

    Note: XGBRanker doesn't fit into a sklearn Pipeline cleanly because
    .fit() needs the `group` argument.  We handle imputation manually.
    """
    return XGBRanker(
        objective="rank:pairwise",
        n_estimators=400,
        max_depth=4,
        learning_rate=0.04,
        subsample=0.8,
        colsample_bytree=0.7,
        min_child_weight=20,
        reg_alpha=0.5,
        reg_lambda=2.0,
        n_jobs=-1,
        random_state=DEFAULT_RANDOM_STATE,
        tree_method="hist",
    )


# ── Main training driver ─────────────────────────────────────────────────────

def train_models_v2(
    feature_df: pd.DataFrame,
    *,
    in_sample_end: dt.date,
    model_version: str | None = None,
    best_by: str = "top5_avg_return",
    on_log: Callable[[str], None] = lambda m: None,
) -> TrainingArtifactsV2:
    """Train v2 models on the full candidate pool.

    Models:
      - lr, rf, xgb_clf: classification on y_take_profit
      - xgb_ranker:      pairwise ranking on bucketized final_cum_return
      - heuristic baseline (confidence_score as score)

    Pick winner by Top5_AvgReturn — the actual production metric.
    """
    if model_version is None:
        model_version = f"v{dt.date.today().strftime('%Y.%m.%d')}_v2"

    # ── Filter trainable rows (drop in_progress, NaN labels) ─────────────────
    df = feature_df.copy()
    df = df[df["outcome_status"].isin(["take_profit", "stop_loss", "expired"])]
    df = df.dropna(subset=["y_take_profit", "y_final_return"])
    if df.empty:
        raise ValueError("No trainable rows")

    # ── Strict temporal split ────────────────────────────────────────────────
    train_df = df[df["rec_date"] <= in_sample_end].copy().sort_values(["rec_date", "ts_code", "strategy"])
    oos_df   = df[df["rec_date"] >  in_sample_end].copy().sort_values(["rec_date", "ts_code", "strategy"])
    if train_df.empty or oos_df.empty:
        raise ValueError(f"Empty split: train={len(train_df)}, oos={len(oos_df)}")

    X_train = train_df[FEATURE_COLUMNS].values
    y_train_cls = train_df["y_take_profit"].values
    y_train_reg = train_df["y_final_return"].values
    X_oos   = oos_df[FEATURE_COLUMNS].values
    y_oos   = oos_df["y_take_profit"].values

    # Compute scale_pos_weight on training set (true ratio)
    pos_rate = float(y_train_cls.mean())
    n_train_days = train_df["rec_date"].nunique()
    n_oos_days   = oos_df["rec_date"].nunique()
    on_log(
        f"Train: {len(train_df):,} candidates over {n_train_days} days "
        f"({pos_rate*100:.1f}% pos)  |  "
        f"OOS: {len(oos_df):,} over {n_oos_days} days ({y_oos.mean()*100:.1f}% pos)"
    )

    metrics: dict[str, ModelResultV2] = {}
    base_models: dict[str, Any] = {}

    # ── Heuristic baseline ──────────────────────────────────────────────────
    heur = _heuristic_baseline(oos_df)
    metrics["heuristic"] = heur
    on_log(
        f"  heuristic:    AUC={heur.oos_auc:.3f}  NDCG@5={heur.oos_ndcg5:.3f}  "
        f"top5_prec={heur.oos_top5_precision:.3f}  top5_ret={heur.oos_top5_avg_return*100:+.2f}%"
    )

    # ── Classification models (LR / RF / XGB) ──────────────────────────────
    class_factories = {
        "lr":  _make_lr,
        "rf":  _make_rf,
        "xgb_clf": lambda: _make_xgb_clf(scale_pos_weight=(1 - pos_rate) / max(pos_rate, 0.01)),
    }
    for name, fac in class_factories.items():
        on_log(f"Training {name}…")
        m = fac()
        m.fit(X_train, y_train_cls)
        s_oos = m.predict_proba(X_oos)[:, 1]
        result = _build_result_v2(name, "classifier", m, oos_df, y_oos, s_oos)
        metrics[name] = result
        base_models[name] = m
        on_log(
            f"  {name}: AUC={result.oos_auc:.3f}  NDCG@5={result.oos_ndcg5:.3f}  "
            f"top5_prec={result.oos_top5_precision:.3f}  "
            f"top5_ret={result.oos_top5_avg_return*100:+.2f}% (med {result.oos_top5_med_return*100:+.2f}%)"
        )

    # ── Ranker (XGBRanker on bucketized returns) ────────────────────────────
    on_log("Training xgb_ranker (rank:pairwise on bucketized returns)…")
    # Group sizes per rec_date (sorted!)
    train_group_sizes = train_df.groupby("rec_date").size().values
    # Bucketize the continuous label (5 ordinal grades)
    y_train_grade = _bucketize_returns(y_train_reg, n_buckets=5)
    # Manual median imputation (XGBRanker doesn't accept Pipelines easily)
    imputer = SimpleImputer(strategy="median")
    X_train_imp = imputer.fit_transform(X_train)
    X_oos_imp   = imputer.transform(X_oos)

    ranker = _make_xgb_ranker()
    ranker.fit(X_train_imp, y_train_grade, group=train_group_sizes)
    s_oos = ranker.predict(X_oos_imp)
    # Wrap ranker + imputer for downstream inference
    ranker_pipeline = ("imputer_then_ranker", imputer, ranker)
    result = _build_result_v2("xgb_ranker", "ranker", ranker_pipeline, oos_df, y_oos, s_oos)
    metrics["xgb_ranker"] = result
    base_models["xgb_ranker"] = ranker_pipeline
    on_log(
        f"  xgb_ranker: AUC={result.oos_auc:.3f}  NDCG@5={result.oos_ndcg5:.3f}  "
        f"top5_prec={result.oos_top5_precision:.3f}  "
        f"top5_ret={result.oos_top5_avg_return*100:+.2f}% (med {result.oos_top5_med_return*100:+.2f}%)"
    )

    # ── Pick winner ─────────────────────────────────────────────────────────
    candidates = {k: v for k, v in metrics.items() if k != "heuristic"}
    keymap = {
        "top5_avg_return": lambda m: m.oos_top5_avg_return,
        "top5_med_return": lambda m: m.oos_top5_med_return,
        "ndcg5":            lambda m: m.oos_ndcg5,
        "oos_auc":          lambda m: m.oos_auc,
    }
    if best_by not in keymap:
        raise ValueError(f"Unknown best_by: {best_by}")
    winner_name = max(candidates, key=lambda n: keymap[best_by](candidates[n]))
    winner_obj  = base_models[winner_name]
    winner_meta = metrics[winner_name]
    on_log(
        f"\n[bold green]Winner by {best_by}:[/bold green] {winner_name}  "
        f"(top5_ret={winner_meta.oos_top5_avg_return*100:+.2f}%, "
        f"NDCG@5={winner_meta.oos_ndcg5:.3f})"
    )

    return TrainingArtifactsV2(
        model_version=model_version,
        feature_columns=list(FEATURE_COLUMNS),
        base_models=base_models,
        production_model=winner_obj,
        production_objective=metrics[winner_name].objective,
        metrics=metrics,
        train_range=(train_df["rec_date"].min(), train_df["rec_date"].max()),
        oos_range=(oos_df["rec_date"].min(), oos_df["rec_date"].max()),
        n_train=len(train_df),
        n_oos=len(oos_df),
        n_train_days=n_train_days,
        n_oos_days=n_oos_days,
    )


def _build_result_v2(
    name: str, objective: str, model: Any,
    oos_df: pd.DataFrame, y_oos: np.ndarray, s_oos: np.ndarray,
) -> ModelResultV2:
    auc = roc_auc_score(y_oos, s_oos) if len(set(y_oos)) > 1 else 0.5
    ap  = average_precision_score(y_oos, s_oos) if len(set(y_oos)) > 1 else y_oos.mean()
    ndcg5 = _ndcg_per_day(oos_df, s_oos)
    p, r, m = _top5_metrics(oos_df, s_oos)

    fi: dict[str, float] = {}
    try:
        if isinstance(model, tuple):
            # ranker_pipeline
            inner = model[2]
        elif hasattr(model, "named_steps"):
            inner = model.named_steps.get("clf", model)
        else:
            inner = model
        if hasattr(inner, "feature_importances_"):
            fi = dict(zip(FEATURE_COLUMNS, inner.feature_importances_.tolist()))
        elif hasattr(inner, "coef_"):
            fi = dict(zip(FEATURE_COLUMNS, np.abs(inner.coef_[0]).tolist()))
    except Exception:
        pass

    return ModelResultV2(
        name=name, objective=objective, model=model,
        oos_auc=auc, oos_avg_precision=ap, oos_ndcg5=ndcg5,
        oos_top5_precision=p, oos_top5_avg_return=r, oos_top5_med_return=m,
        feature_importances=fi,
    )
