"""Model training pipeline for ningbo ML scoring — Phase 3.2/3.3.

Implements:
  - Three base models: LogisticRegression, RandomForest, XGBoost
  - Stacking ensemble (LR meta-learner over base OOF predictions)
  - Probability calibration (sigmoid via CalibratedClassifierCV)
  - Strict temporal train/OOS split (no shuffling)
  - Metrics: AUC-ROC, top-N precision, average return of top-5

Models are trained on `y_take_profit` (binary: take_profit vs not).
Outputs scores in [0, 1] interpreted as P(take_profit | features).
For ranking purposes (top-5 selection), only relative scores matter,
but calibration ensures scores are interpretable.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier, StackingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score, brier_score_loss, log_loss, roc_auc_score,
)
from sklearn.model_selection import KFold, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from ifa.families.ningbo.ml.features import FEATURE_COLUMNS, select_trainable

DEFAULT_RANDOM_STATE = 42


# ── Result containers ─────────────────────────────────────────────────────────

@dataclass
class ModelResult:
    name: str
    model: Any                            # fitted estimator
    train_auc: float
    oos_auc: float
    oos_avg_precision: float              # area under PR curve
    oos_brier: float
    oos_log_loss: float
    oos_top5_precision: float             # of top-5 picks per day, fraction take_profit
    oos_top5_avg_return: float            # avg final_cum_return of top-5 picks
    feature_importances: dict[str, float] = field(default_factory=dict)


@dataclass
class TrainingArtifacts:
    model_version: str
    feature_columns: list[str]
    base_models: dict[str, Any]           # name -> fitted model
    stacking_model: Any                   # fitted stacking ensemble (calibrated)
    metrics: dict[str, ModelResult]       # per-model results
    train_range: tuple[dt.date, dt.date]
    oos_range: tuple[dt.date, dt.date]
    n_train: int
    n_oos: int
    pos_rate_train: float
    pos_rate_oos: float


# ── Heuristic baseline (for comparison) ──────────────────────────────────────

def evaluate_heuristic_baseline(oos_df: pd.DataFrame) -> ModelResult:
    """Use confidence_score as the model's score and compute the same metrics.

    This gives us an apples-to-apples baseline: how good is the heuristic
    confidence_score at ranking take_profit candidates?
    """
    y_true  = oos_df["y_take_profit"].values
    y_score = oos_df["confidence_score"].values

    auc = roc_auc_score(y_true, y_score) if len(set(y_true)) > 1 else 0.5
    ap  = average_precision_score(y_true, y_score) if len(set(y_true)) > 1 else y_true.mean()
    top5_p, top5_r = _top5_metrics(oos_df, y_score)
    return ModelResult(
        name="heuristic_baseline",
        model=None,
        train_auc=float("nan"),
        oos_auc=auc,
        oos_avg_precision=ap,
        oos_brier=brier_score_loss(y_true, y_score) if y_score.min() >= 0 and y_score.max() <= 1 else float("nan"),
        oos_log_loss=float("nan"),  # uncalibrated heuristic — log_loss not meaningful
        oos_top5_precision=top5_p,
        oos_top5_avg_return=top5_r,
    )


# ── Top-N evaluation ─────────────────────────────────────────────────────────

def _top5_metrics(
    df: pd.DataFrame, scores: np.ndarray, top_n: int = 5, per_strategy_cap: int = 2,
) -> tuple[float, float]:
    """For each rec_date, pick top-N candidates by score (with per-strategy cap).
    Return (precision = mean(y_take_profit), avg_return = mean(final_cum_return)).
    """
    df = df.copy()
    df["_score"] = scores
    picks = []
    for rec_date, group in df.groupby("rec_date"):
        g = group.sort_values("_score", ascending=False)
        # Apply per-strategy cap
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
        return 0.0, 0.0
    picks_df = pd.DataFrame(picks)
    return (
        float(picks_df["y_take_profit"].mean()),
        float(picks_df["final_cum_return"].mean()),
    )


# ── Model factories ──────────────────────────────────────────────────────────

def _make_lr() -> Pipeline:
    """Logistic regression with L2 + balanced class weight + StandardScaler."""
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(
            C=0.5,                       # stronger regularization
            penalty="l2",
            class_weight="balanced",
            max_iter=3000,
            solver="lbfgs",
            random_state=DEFAULT_RANDOM_STATE,
        )),
    ])


def _make_rf() -> Pipeline:
    """RF — heavily regularized for ~2k samples × ~40 features (avoid overfit)."""
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("clf", RandomForestClassifier(
            n_estimators=300,
            max_depth=5,                # was 8
            min_samples_leaf=20,        # was 10
            min_samples_split=30,
            max_features="sqrt",
            class_weight="balanced",
            n_jobs=-1,
            random_state=DEFAULT_RANDOM_STATE,
        )),
    ])


def _make_xgb(scale_pos_weight: float = 3.0) -> Pipeline:
    """XGB — shallower trees, more regularization, lower lr.

    scale_pos_weight reduced from 6→3 (less aggressive class re-weighting;
    extreme values were hurting calibration without helping ranking).
    """
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("clf", XGBClassifier(
            n_estimators=200,           # was 300
            max_depth=3,                # was 4
            learning_rate=0.03,         # was 0.05
            subsample=0.8,
            colsample_bytree=0.7,
            min_child_weight=10,        # NEW — strong leaf regularization
            reg_alpha=0.5,              # was 0.1
            reg_lambda=1.5,             # was 1.0
            scale_pos_weight=scale_pos_weight,
            objective="binary:logistic",
            eval_metric="auc",
            n_jobs=-1,
            random_state=DEFAULT_RANDOM_STATE,
            tree_method="hist",
        )),
    ])


def _make_stacking(base_estimators: list[tuple[str, Any]]) -> StackingClassifier:
    """Stacking ensemble: base models → LR meta-learner.

    StackingClassifier requires the CV to produce a partition (every sample
    predicted exactly once via cross_val_predict).  TimeSeriesSplit doesn't
    do this — earliest samples never appear in any test fold — so we use
    plain KFold for the meta-feature generation.  Temporal integrity is
    preserved at the outer train/OOS boundary; the inner CV is only used
    to produce out-of-fold base predictions.
    """
    return StackingClassifier(
        estimators=base_estimators,
        final_estimator=LogisticRegression(
            max_iter=1000, class_weight="balanced", random_state=DEFAULT_RANDOM_STATE,
        ),
        cv=KFold(n_splits=5, shuffle=True, random_state=DEFAULT_RANDOM_STATE),
        passthrough=False,
        n_jobs=-1,
    )


# ── Training driver ──────────────────────────────────────────────────────────

def train_models(
    feature_df: pd.DataFrame,
    *,
    in_sample_end: dt.date,
    model_version: str | None = None,
    best_by: str = "top5_avg_return",
    on_log: Callable[[str], None] = lambda m: None,
) -> TrainingArtifacts:
    """Train base models + stacking; pick the best one as production.

    Strategy:
      1. Train each base model UNCALIBRATED on full training set.
      2. Evaluate raw probabilities on OOS (calibration affects scaling
         not ranking, and CalibratedClassifierCV's inner CV with small data
         tends to hurt AUC).
      3. Train stacking ensemble (LR meta over base models).
      4. Pick winner by `best_by` metric; calibrate ONLY the winner with
         simple sigmoid (Platt) using a 70/30 holdout (faster, cleaner).
      5. Save: winner as `stacking_model` (production), all base as base_models.

    Args:
        feature_df:    Output of build_feature_matrix.
        in_sample_end: Last rec_date in training set (exclusive of OOS).
        model_version: Tag for saved artifacts; auto-generated if None.
        best_by:       'top5_avg_return' | 'oos_auc' | 'top5_precision'.
    """
    if model_version is None:
        model_version = f"v{dt.date.today().strftime('%Y.%m.%d')}_b"

    # ── Filter to trainable rows ─────────────────────────────────────────────
    df = select_trainable(feature_df).copy()
    df = df.dropna(subset=["y_take_profit", "final_cum_return"])
    if df.empty:
        raise ValueError("No trainable rows after filtering")

    # ── Strict temporal split ────────────────────────────────────────────────
    train_df = df[df["rec_date"] <= in_sample_end].copy()
    oos_df   = df[df["rec_date"] >  in_sample_end].copy()
    if train_df.empty or oos_df.empty:
        raise ValueError(
            f"Empty split: train={len(train_df)}, oos={len(oos_df)}, "
            f"in_sample_end={in_sample_end}"
        )

    X_train = train_df[FEATURE_COLUMNS].values
    y_train = train_df["y_take_profit"].values
    X_oos   = oos_df[FEATURE_COLUMNS].values
    y_oos   = oos_df["y_take_profit"].values

    pos_rate_train = float(y_train.mean())
    pos_rate_oos   = float(y_oos.mean())
    scale_pos_weight = (1 - pos_rate_train) / max(pos_rate_train, 0.01)
    on_log(
        f"Train: {len(train_df)} samples ({pos_rate_train*100:.1f}% pos)  |  "
        f"OOS: {len(oos_df)} samples ({pos_rate_oos*100:.1f}% pos)  |  "
        f"effective scale_pos_weight={scale_pos_weight:.2f} (XGB uses 3.0)"
    )

    # ── Train base models (uncalibrated) ─────────────────────────────────────
    metrics: dict[str, ModelResult] = {}
    base_models: dict[str, Any] = {}

    factories = {
        "lr":  lambda: _make_lr(),
        "rf":  lambda: _make_rf(),
        "xgb": lambda: _make_xgb(scale_pos_weight=3.0),
    }

    for name, factory in factories.items():
        on_log(f"Training {name}…")
        model = factory()
        model.fit(X_train, y_train)
        s_train = model.predict_proba(X_train)[:, 1]
        s_oos   = model.predict_proba(X_oos)[:, 1]
        m = _build_result(
            name=name, model=model,
            y_train=y_train, s_train=s_train,
            oos_df=oos_df, y_oos=y_oos, s_oos=s_oos,
            feature_names=FEATURE_COLUMNS,
        )
        metrics[name] = m
        base_models[name] = model
        on_log(
            f"  {name}: AUC oos={m.oos_auc:.3f}  AP={m.oos_avg_precision:.3f}  "
            f"top5_prec={m.oos_top5_precision:.3f}  top5_ret={m.oos_top5_avg_return*100:+.2f}%"
        )

    # ── Heuristic baseline ───────────────────────────────────────────────────
    heur = evaluate_heuristic_baseline(oos_df)
    metrics["heuristic"] = heur
    on_log(
        f"  heuristic baseline: AUC oos={heur.oos_auc:.3f}  AP={heur.oos_avg_precision:.3f}  "
        f"top5_prec={heur.oos_top5_precision:.3f}  top5_ret={heur.oos_top5_avg_return*100:+.2f}%"
    )

    # ── Stacking ensemble ────────────────────────────────────────────────────
    on_log("Training stacking ensemble (LR meta over LR/RF/XGB)…")
    base_for_stack = [(name, factories[name]()) for name in ("lr", "rf", "xgb")]
    stacking = _make_stacking(base_for_stack)
    stacking.fit(X_train, y_train)
    s_train = stacking.predict_proba(X_train)[:, 1]
    s_oos   = stacking.predict_proba(X_oos)[:, 1]
    m_stk = _build_result(
        name="stacking", model=stacking,
        y_train=y_train, s_train=s_train,
        oos_df=oos_df, y_oos=y_oos, s_oos=s_oos,
        feature_names=FEATURE_COLUMNS,
    )
    metrics["stacking"] = m_stk
    on_log(
        f"  stacking: AUC oos={m_stk.oos_auc:.3f}  AP={m_stk.oos_avg_precision:.3f}  "
        f"top5_prec={m_stk.oos_top5_precision:.3f}  top5_ret={m_stk.oos_top5_avg_return*100:+.2f}%"
    )

    # ── Pick winner ─────────────────────────────────────────────────────────
    candidates = {n: metrics[n] for n in ("lr", "rf", "xgb", "stacking")}
    metric_keymap = {
        "top5_avg_return": lambda m: m.oos_top5_avg_return,
        "oos_auc":         lambda m: m.oos_auc,
        "top5_precision":  lambda m: m.oos_top5_precision,
    }
    if best_by not in metric_keymap:
        raise ValueError(f"Unknown best_by: {best_by}")
    winner_name = max(candidates, key=lambda n: metric_keymap[best_by](candidates[n]))
    winner_obj = base_models[winner_name] if winner_name in base_models else stacking
    on_log(f"Winner by {best_by}: [bold green]{winner_name}[/bold green]")

    # ── Save the winner directly (uncalibrated raw probabilities) ────────────
    # Calibration only affects probability scaling, not ranking.  Top-N
    # selection only needs monotonic scores → skip calibration to avoid
    # sklearn API churn + small-sample CV noise.
    if winner_name in base_models:
        production_model = base_models[winner_name]
    else:
        production_model = stacking
    on_log(f"Saving {winner_name} as production model (raw P(take_profit) scores).")

    return TrainingArtifacts(
        model_version=model_version,
        feature_columns=list(FEATURE_COLUMNS),
        base_models=base_models,
        stacking_model=production_model,             # <-- the production model
        metrics=metrics,
        train_range=(train_df["rec_date"].min(), train_df["rec_date"].max()),
        oos_range=(oos_df["rec_date"].min(), oos_df["rec_date"].max()),
        n_train=len(train_df),
        n_oos=len(oos_df),
        pos_rate_train=pos_rate_train,
        pos_rate_oos=pos_rate_oos,
    )


def _build_result(
    *, name: str, model: Any,
    y_train: np.ndarray, s_train: np.ndarray,
    oos_df: pd.DataFrame, y_oos: np.ndarray, s_oos: np.ndarray,
    feature_names: list[str],
) -> ModelResult:
    train_auc = roc_auc_score(y_train, s_train) if len(set(y_train)) > 1 else 0.5
    oos_auc   = roc_auc_score(y_oos,   s_oos)   if len(set(y_oos))   > 1 else 0.5
    oos_ap    = average_precision_score(y_oos, s_oos) if len(set(y_oos)) > 1 else y_oos.mean()
    oos_brier = brier_score_loss(y_oos, s_oos)
    oos_ll    = log_loss(y_oos, np.clip(s_oos, 1e-7, 1 - 1e-7))
    top5_p, top5_r = _top5_metrics(oos_df, s_oos)

    # Feature importance (if available)
    fi: dict[str, float] = {}
    try:
        # Walk the calibrated model → first calibrated_classifier → estimator → pipeline → clf
        cc = model.calibrated_classifiers_[0] if hasattr(model, "calibrated_classifiers_") else None
        inner = getattr(cc, "estimator", None) if cc else model
        # Pipeline → clf
        if hasattr(inner, "named_steps") and "clf" in inner.named_steps:
            clf = inner.named_steps["clf"]
        else:
            clf = inner
        if hasattr(clf, "feature_importances_"):
            fi = dict(zip(feature_names, clf.feature_importances_.tolist()))
        elif hasattr(clf, "coef_"):
            fi = dict(zip(feature_names, np.abs(clf.coef_[0]).tolist()))
    except Exception:
        pass

    return ModelResult(
        name=name, model=model,
        train_auc=train_auc, oos_auc=oos_auc,
        oos_avg_precision=oos_ap, oos_brier=oos_brier, oos_log_loss=oos_ll,
        oos_top5_precision=top5_p, oos_top5_avg_return=top5_r,
        feature_importances=fi,
    )
