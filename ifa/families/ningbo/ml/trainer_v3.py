"""Phase 3.C trainer — comprehensive model matrix + ensemble + Kronos-ready.

Models trained:
    Linear:        LR
    Trees:         RF
    GBM:           XGB-clf, LightGBM-clf, CatBoost-clf
    Rankers:       XGB-pairwise, XGB-ndcg, LightGBM-lambdarank
    Tabular NN:    TabNet
    Ensembles:     mean-rank of all GBMs+rankers
                   stacking (LR meta over base OOF predictions)

Evaluation (OOS):
    AUC                — global ranking quality (less important for top-N use case)
    NDCG@5             — top-5 ranking quality (PRIMARY)
    Top5_Precision     — fraction of top-5 picks that hit take_profit
    Top5_Mean / Median — return distribution of top-5 picks
    Per-strategy lift  — does ML help sniper / basin / hyd specifically?
    Per-month return   — stability over time
    Bootstrap p-value  — statistical significance vs heuristic baseline

Production decision rule:
    Winner = model with highest Top5_Mean
    Promote IF: Top5_Mean ≥ +2.0%
            AND NDCG@5 ≥ 0.33
            AND p < 0.05 vs heuristic baseline
            AND Top5_Med ≥ -2.0% (avoid catastrophic loss profile)
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
from sklearn.model_selection import KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ifa.families.ningbo.ml.features import FEATURE_COLUMNS

DEFAULT_RANDOM_STATE = 42


# ── Result containers ────────────────────────────────────────────────────────

@dataclass
class ModelResultV3:
    name: str
    objective: str               # 'classifier' | 'ranker' | 'ensemble' | 'baseline'
    model: Any
    oos_auc: float
    oos_avg_precision: float
    oos_ndcg5: float
    oos_top5_precision: float
    oos_top5_avg_return: float
    oos_top5_med_return: float
    oos_top5_sharpe: float        # mean / std of per-day mean-of-top5
    oos_top5_winrate: float       # fraction of days top-5 had positive avg return
    oos_max_drawdown: float       # cumulative DD if equal-weighting top-5 daily
    feature_importances: dict[str, float] = field(default_factory=dict)
    bootstrap_p_value: float = float("nan")  # vs heuristic baseline
    raw_oos_scores: np.ndarray | None = None  # for ensembling
    passes_promotion: bool = False


@dataclass
class TrainingArtifactsV3:
    model_version: str
    feature_columns: list[str]
    base_models: dict[str, Any]
    production_model_name: str
    production_model: Any
    metrics: dict[str, ModelResultV3]
    train_range: tuple[dt.date, dt.date]
    oos_range: tuple[dt.date, dt.date]
    n_train: int
    n_oos: int
    n_train_days: int
    n_oos_days: int
    decision: str   # human-readable promote/hold-back reason


# ── Top-N + walk-forward + statistical evaluation ────────────────────────────

def _select_top_n_per_day(
    df: pd.DataFrame, scores: np.ndarray, top_n: int = 5, per_strategy_cap: int = 2,
) -> pd.DataFrame:
    """Apply per-strategy cap + take top-N per rec_date. Returns picks-only df."""
    df = df.copy()
    df["_score"] = scores
    picks = []
    for _, group in df.groupby("rec_date"):
        g = group.sort_values("_score", ascending=False)
        chosen, per_strat = [], {}
        for _, r in g.iterrows():
            s = r["strategy"]
            if per_strat.get(s, 0) >= per_strategy_cap:
                continue
            chosen.append(r)
            per_strat[s] = per_strat.get(s, 0) + 1
            if len(chosen) >= top_n:
                break
        picks.extend(chosen)
    return pd.DataFrame(picks) if picks else pd.DataFrame()


def _per_day_top5_returns(picks_df: pd.DataFrame) -> np.ndarray:
    """Return array of mean-of-top5 final_cum_return per rec_date."""
    if picks_df.empty:
        return np.array([])
    return picks_df.groupby("rec_date")["final_cum_return"].mean().values


def _max_drawdown(per_day_returns: np.ndarray) -> float:
    """Equal-weighted compounded drawdown, treating each day's mean as a return."""
    if len(per_day_returns) == 0:
        return 0.0
    equity = np.cumprod(1 + per_day_returns)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(dd.min())


def _ndcg_per_day(df: pd.DataFrame, scores: np.ndarray, k: int = 5) -> float:
    df = df.copy()
    df["_score"] = scores
    ndcgs = []
    for _, g in df.groupby("rec_date"):
        if len(g) < 2:
            continue
        y_true = np.maximum(g["final_cum_return"].values, -0.5).reshape(1, -1)
        y_pred = g["_score"].values.reshape(1, -1)
        y_true_shifted = y_true - y_true.min() + 1e-6
        try:
            ndcgs.append(ndcg_score(y_true_shifted, y_pred, k=k))
        except Exception:
            continue
    return float(np.mean(ndcgs)) if ndcgs else float("nan")


def _bootstrap_p_value(
    oos_df: pd.DataFrame, scores_a: np.ndarray, scores_b: np.ndarray,
    n_boot: int = 500, seed: int = 42,
) -> float:
    """Bootstrap test: P(model A's top5_avg_return > model B's | null).

    Resample rec_dates with replacement, recompute top-5 returns for both
    models, count how often A < B. Two-sided p approximated.
    """
    rng = np.random.default_rng(seed)
    days = oos_df["rec_date"].unique()
    n_days = len(days)
    if n_days == 0:
        return float("nan")

    # Pre-build per-day picks for both models
    df_a = oos_df.copy(); df_a["_score"] = scores_a
    df_b = oos_df.copy(); df_b["_score"] = scores_b

    # observed difference
    obs_a = _per_day_top5_returns(_select_top_n_per_day(oos_df, scores_a))
    obs_b = _per_day_top5_returns(_select_top_n_per_day(oos_df, scores_b))
    obs_diff = obs_a.mean() - obs_b.mean() if len(obs_a) and len(obs_b) else 0

    # Bootstrap day-level
    by_day_a = df_a.groupby("rec_date")
    by_day_b = df_b.groupby("rec_date")
    a_per_day_lookup = {d: g for d, g in by_day_a}
    b_per_day_lookup = {d: g for d, g in by_day_b}

    cnt_extreme = 0
    for _ in range(n_boot):
        sample_days = rng.choice(days, size=n_days, replace=True)
        a_picks, b_picks = [], []
        for d in sample_days:
            ga = a_per_day_lookup[d].sort_values("_score", ascending=False)
            gb = b_per_day_lookup[d].sort_values("_score", ascending=False)
            # Apply per-strategy cap + top-5
            for picks_acc, g in [(a_picks, ga), (b_picks, gb)]:
                chosen, per_strat = [], {}
                for _, r in g.iterrows():
                    if per_strat.get(r["strategy"], 0) >= 2:
                        continue
                    chosen.append(r["final_cum_return"])
                    per_strat[r["strategy"]] = per_strat.get(r["strategy"], 0) + 1
                    if len(chosen) >= 5:
                        break
                if chosen:
                    picks_acc.append(np.mean(chosen))
        a_mean = np.mean(a_picks) if a_picks else 0
        b_mean = np.mean(b_picks) if b_picks else 0
        if (a_mean - b_mean) <= 0:  # one-sided: A doesn't beat B
            cnt_extreme += 1
    return cnt_extreme / n_boot


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


def _make_xgb_clf(scale_pos_weight: float) -> Pipeline:
    from xgboost import XGBClassifier
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


def _make_lgbm_clf(scale_pos_weight: float) -> Pipeline:
    from lightgbm import LGBMClassifier
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("clf", LGBMClassifier(
            n_estimators=500, max_depth=6, num_leaves=31, learning_rate=0.04,
            min_child_samples=30, subsample=0.8, colsample_bytree=0.7,
            reg_alpha=0.5, reg_lambda=2.0,
            scale_pos_weight=scale_pos_weight,
            objective="binary", metric="auc",
            n_jobs=-1, random_state=DEFAULT_RANDOM_STATE, verbose=-1,
        )),
    ])


def _make_catboost_clf(scale_pos_weight: float) -> Pipeline:
    from catboost import CatBoostClassifier
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("clf", CatBoostClassifier(
            iterations=500, depth=6, learning_rate=0.04,
            l2_leaf_reg=3.0, subsample=0.8, rsm=0.7,
            scale_pos_weight=scale_pos_weight,
            loss_function="Logloss", eval_metric="AUC",
            random_seed=DEFAULT_RANDOM_STATE, verbose=0,
            thread_count=-1, bootstrap_type="Bernoulli",
        )),
    ])


# ── Rankers (don't fit pipelines well — handle imputation separately) ───────

def _make_xgb_ranker(objective: str = "rank:ndcg"):
    """objective in {'rank:pairwise', 'rank:ndcg', 'rank:map'}"""
    from xgboost import XGBRanker
    return XGBRanker(
        objective=objective,
        n_estimators=400, max_depth=4, learning_rate=0.04,
        subsample=0.8, colsample_bytree=0.7, min_child_weight=20,
        reg_alpha=0.5, reg_lambda=2.0,
        n_jobs=-1, random_state=DEFAULT_RANDOM_STATE, tree_method="hist",
    )


def _make_lgbm_ranker():
    from lightgbm import LGBMRanker
    return LGBMRanker(
        objective="lambdarank", metric="ndcg",
        n_estimators=500, max_depth=6, num_leaves=31, learning_rate=0.04,
        min_child_samples=30, subsample=0.8, colsample_bytree=0.7,
        reg_alpha=0.5, reg_lambda=2.0, label_gain=[0, 1, 2, 3, 4, 5],
        n_jobs=-1, random_state=DEFAULT_RANDOM_STATE, verbose=-1,
    )


# ── TabNet (PyTorch-based attention tabular NN) ──────────────────────────────

def _make_tabnet():
    """Returns a callable that takes (X_train, y_train, X_oos) and yields scores.

    Wrapped because TabNet needs special fit signature (numpy arrays, no pipe).
    """
    import torch
    from pytorch_tabnet.tab_model import TabNetClassifier

    device = "mps" if torch.backends.mps.is_available() else (
        "cuda" if torch.cuda.is_available() else "cpu"
    )
    return TabNetClassifier(
        n_d=16, n_a=16, n_steps=4, gamma=1.3,
        lambda_sparse=1e-3, optimizer_fn=torch.optim.Adam,
        optimizer_params=dict(lr=2e-2),
        scheduler_params=dict(step_size=10, gamma=0.9),
        scheduler_fn=torch.optim.lr_scheduler.StepLR,
        mask_type="entmax", verbose=0, seed=DEFAULT_RANDOM_STATE,
        device_name=device,
    )


# ── Bucketize for ranker labels ──────────────────────────────────────────────

def _bucketize_returns(returns: np.ndarray, n_buckets: int = 5) -> np.ndarray:
    qs = np.linspace(0, 1, n_buckets + 1)[1:-1]
    cuts = np.quantile(returns, qs)
    return np.digitize(returns, cuts, right=False).astype(int)


# ── Main training driver ─────────────────────────────────────────────────────

def train_models_v3(
    feature_df: pd.DataFrame,
    *,
    in_sample_end: dt.date,
    model_version: str | None = None,
    use_tabnet: bool = True,
    use_kronos_features: bool = False,
    on_log: Callable[[str], None] = lambda m: None,
) -> TrainingArtifactsV3:
    """Train comprehensive model matrix on full candidate pool."""
    if model_version is None:
        suffix = "_v3kronos" if use_kronos_features else "_v3"
        model_version = f"v{dt.date.today().strftime('%Y.%m.%d')}{suffix}"

    # Filter trainable rows
    df = feature_df.copy()
    df = df[df["outcome_status"].isin(["take_profit", "stop_loss", "expired"])]
    df = df.dropna(subset=["y_take_profit", "y_final_return"])
    if df.empty:
        raise ValueError("No trainable rows")

    # Feature columns: base or base+kronos
    feat_cols = list(FEATURE_COLUMNS)
    if use_kronos_features:
        kronos_cols = [c for c in df.columns if c.startswith("kronos_emb_")]
        feat_cols = feat_cols + kronos_cols
        on_log(f"Using {len(feat_cols)} features (39 base + {len(kronos_cols)} kronos)")
    else:
        on_log(f"Using {len(feat_cols)} base features (no Kronos)")

    # Strict temporal split
    train_df = df[df["rec_date"] <= in_sample_end].copy().sort_values(
        ["rec_date", "ts_code", "strategy"]
    )
    oos_df   = df[df["rec_date"] >  in_sample_end].copy().sort_values(
        ["rec_date", "ts_code", "strategy"]
    )
    if train_df.empty or oos_df.empty:
        raise ValueError(f"Empty split: train={len(train_df)}, oos={len(oos_df)}")

    X_train = train_df[feat_cols].values
    y_train_cls = train_df["y_take_profit"].values
    y_train_reg = train_df["y_final_return"].values
    X_oos   = oos_df[feat_cols].values
    y_oos   = oos_df["y_take_profit"].values

    pos_rate = float(y_train_cls.mean())
    spw = (1 - pos_rate) / max(pos_rate, 0.01)
    n_train_days = train_df["rec_date"].nunique()
    n_oos_days   = oos_df["rec_date"].nunique()
    on_log(
        f"Train: {len(train_df):,} candidates / {n_train_days} days ({pos_rate*100:.1f}% pos)  |  "
        f"OOS: {len(oos_df):,} / {n_oos_days} days ({y_oos.mean()*100:.1f}% pos)  |  "
        f"scale_pos_weight={spw:.2f}"
    )

    metrics: dict[str, ModelResultV3] = {}
    base_models: dict[str, Any] = {}

    # ── Heuristic baseline ──────────────────────────────────────────────────
    s_oos_heur = oos_df["confidence_score"].values
    metrics["heuristic"] = _build_result_v3("heuristic", "baseline", None, oos_df, y_oos, s_oos_heur)
    on_log(f"  heuristic:    {_fmt(metrics['heuristic'])}")

    # ── Classification models ───────────────────────────────────────────────
    classifier_factories = {
        "lr":          lambda: _make_lr(),
        "rf":          lambda: _make_rf(),
        "xgb_clf":     lambda: _make_xgb_clf(spw),
        "lgbm_clf":    lambda: _make_lgbm_clf(spw),
        "cat_clf":     lambda: _make_catboost_clf(spw),
    }
    for name, fac in classifier_factories.items():
        on_log(f"Training {name}…")
        m = fac()
        m.fit(X_train, y_train_cls)
        s_oos = m.predict_proba(X_oos)[:, 1]
        result = _build_result_v3(name, "classifier", m, oos_df, y_oos, s_oos)
        metrics[name] = result
        base_models[name] = m
        on_log(f"  {name}:        {_fmt(result)}")

    # ── Ranker models (group by rec_date) ───────────────────────────────────
    train_groups = train_df.groupby("rec_date").size().values
    y_train_grade = _bucketize_returns(y_train_reg, n_buckets=5)
    imputer = SimpleImputer(strategy="median")
    X_train_imp = imputer.fit_transform(X_train)
    X_oos_imp   = imputer.transform(X_oos)

    ranker_factories = {
        "xgb_pair":   lambda: _make_xgb_ranker("rank:pairwise"),
        "xgb_ndcg":   lambda: _make_xgb_ranker("rank:ndcg"),
        "lgbm_lamda": lambda: _make_lgbm_ranker(),
    }
    for name, fac in ranker_factories.items():
        on_log(f"Training {name}…")
        try:
            ranker = fac()
            if name.startswith("lgbm"):
                ranker.fit(X_train_imp, y_train_grade, group=train_groups)
            else:
                ranker.fit(X_train_imp, y_train_grade, group=train_groups)
            s_oos = ranker.predict(X_oos_imp)
            result = _build_result_v3(
                name, "ranker", ("imputer_then_ranker", imputer, ranker),
                oos_df, y_oos, s_oos,
            )
            metrics[name] = result
            base_models[name] = ("imputer_then_ranker", imputer, ranker)
            on_log(f"  {name}:    {_fmt(result)}")
        except Exception as exc:
            on_log(f"  ⚠️  {name} failed: {exc}")

    # ── TabNet ──────────────────────────────────────────────────────────────
    if use_tabnet:
        on_log("Training tabnet (PyTorch on M1 MPS)…")
        try:
            tabnet = _make_tabnet()
            tabnet.fit(
                X_train_imp, y_train_cls,
                eval_set=[(X_oos_imp, y_oos)],
                eval_name=["oos"], eval_metric=["auc"],
                max_epochs=80, patience=15, batch_size=2048, virtual_batch_size=256,
            )
            s_oos = tabnet.predict_proba(X_oos_imp)[:, 1]
            result = _build_result_v3(
                "tabnet", "tabular_nn", ("imputer_then_tabnet", imputer, tabnet),
                oos_df, y_oos, s_oos,
            )
            metrics["tabnet"] = result
            base_models["tabnet"] = ("imputer_then_tabnet", imputer, tabnet)
            on_log(f"  tabnet:        {_fmt(result)}")
        except Exception as exc:
            on_log(f"  ⚠️  tabnet failed: {exc}")

    # ── Mean-rank ensemble (top GBMs + ranker) ──────────────────────────────
    on_log("Computing mean-rank ensemble of GBM/ranker models…")
    ensemble_members = [
        n for n in ("xgb_clf", "lgbm_clf", "cat_clf", "lgbm_lamda", "xgb_ndcg")
        if n in metrics
    ]
    if len(ensemble_members) >= 2:
        # Convert each model's OOS scores to ranks within rec_date, then average
        rank_dfs = []
        for n in ensemble_members:
            scores = metrics[n].raw_oos_scores
            tmp = oos_df[["rec_date"]].copy()
            tmp["_s"] = scores
            tmp[f"rank_{n}"] = tmp.groupby("rec_date")["_s"].rank(method="min", ascending=False)
            rank_dfs.append(tmp[f"rank_{n}"].values)
        mean_rank = np.mean(rank_dfs, axis=0)
        # Lower rank = better → invert for "score"
        ensemble_score = -mean_rank
        result = _build_result_v3(
            "ensemble_meanrank", "ensemble", ensemble_members,
            oos_df, y_oos, ensemble_score,
        )
        metrics["ensemble_meanrank"] = result
        base_models["ensemble_meanrank"] = ensemble_members
        on_log(f"  ensemble:      {_fmt(result)}  ({len(ensemble_members)} members)")

    # ── Bootstrap significance vs heuristic ────────────────────────────────
    on_log("Computing bootstrap p-values vs heuristic baseline…")
    s_heur = metrics["heuristic"].raw_oos_scores
    for name, m in metrics.items():
        if name == "heuristic":
            continue
        m.bootstrap_p_value = _bootstrap_p_value(oos_df, m.raw_oos_scores, s_heur, n_boot=200)

    # ── Apply promotion rule ────────────────────────────────────────────────
    PROMOTION_THRESHOLDS = dict(top5_mean=0.020, ndcg5=0.330, top5_med_floor=-0.020, p_value=0.05)
    for name, m in metrics.items():
        if name == "heuristic":
            continue
        m.passes_promotion = (
            m.oos_top5_avg_return >= PROMOTION_THRESHOLDS["top5_mean"]
            and m.oos_ndcg5            >= PROMOTION_THRESHOLDS["ndcg5"]
            and m.oos_top5_med_return  >= PROMOTION_THRESHOLDS["top5_med_floor"]
            and m.bootstrap_p_value    <  PROMOTION_THRESHOLDS["p_value"]
        )

    # ── Pick winner: highest Top5_Mean among models passing promotion ───────
    candidates = {k: v for k, v in metrics.items() if k != "heuristic"}
    passers = {k: v for k, v in candidates.items() if v.passes_promotion}
    if passers:
        winner_name = max(passers, key=lambda n: passers[n].oos_top5_avg_return)
        decision = f"PROMOTE: {winner_name} passes all 4 thresholds"
    else:
        winner_name = max(candidates, key=lambda n: candidates[n].oos_top5_avg_return)
        decision = f"HOLD-BACK: {winner_name} is top performer but doesn't pass promotion thresholds"

    winner_obj = base_models.get(winner_name)
    on_log(f"\n  Winner: [bold]{winner_name}[/bold]  Decision: {decision}")

    return TrainingArtifactsV3(
        model_version=model_version,
        feature_columns=feat_cols,
        base_models=base_models,
        production_model_name=winner_name,
        production_model=winner_obj,
        metrics=metrics,
        train_range=(train_df["rec_date"].min(), train_df["rec_date"].max()),
        oos_range=(oos_df["rec_date"].min(), oos_df["rec_date"].max()),
        n_train=len(train_df), n_oos=len(oos_df),
        n_train_days=n_train_days, n_oos_days=n_oos_days,
        decision=decision,
    )


def _build_result_v3(
    name: str, objective: str, model: Any,
    oos_df: pd.DataFrame, y_oos: np.ndarray, s_oos: np.ndarray,
) -> ModelResultV3:
    auc   = roc_auc_score(y_oos, s_oos) if len(set(y_oos)) > 1 else 0.5
    ap    = average_precision_score(y_oos, s_oos) if len(set(y_oos)) > 1 else float(y_oos.mean())
    ndcg5 = _ndcg_per_day(oos_df, s_oos)
    picks = _select_top_n_per_day(oos_df, s_oos)
    if picks.empty:
        prec = mean = med = sharpe = winrate = mdd = 0.0
    else:
        prec = float(picks["y_take_profit"].mean())
        mean = float(picks["final_cum_return"].mean())
        med  = float(picks["final_cum_return"].median())
        per_day = _per_day_top5_returns(picks)
        sharpe = float(per_day.mean() / per_day.std()) if per_day.std() > 1e-8 else 0.0
        winrate = float((per_day > 0).mean())
        mdd = _max_drawdown(per_day)

    fi: dict[str, float] = {}
    try:
        if isinstance(model, tuple):
            inner = model[2]
        elif hasattr(model, "named_steps"):
            inner = model.named_steps.get("clf", model)
        else:
            inner = model
        if inner is not None:
            if hasattr(inner, "feature_importances_"):
                fi = dict(zip(FEATURE_COLUMNS, np.asarray(inner.feature_importances_).tolist()[:len(FEATURE_COLUMNS)]))
            elif hasattr(inner, "coef_"):
                fi = dict(zip(FEATURE_COLUMNS, np.abs(inner.coef_[0]).tolist()[:len(FEATURE_COLUMNS)]))
    except Exception:
        pass

    return ModelResultV3(
        name=name, objective=objective, model=model,
        oos_auc=auc, oos_avg_precision=ap, oos_ndcg5=ndcg5,
        oos_top5_precision=prec, oos_top5_avg_return=mean, oos_top5_med_return=med,
        oos_top5_sharpe=sharpe, oos_top5_winrate=winrate, oos_max_drawdown=mdd,
        feature_importances=fi, raw_oos_scores=s_oos,
    )


def _fmt(m: ModelResultV3) -> str:
    return (
        f"AUC={m.oos_auc:.3f}  NDCG@5={m.oos_ndcg5:.3f}  "
        f"top5_prec={m.oos_top5_precision*100:.1f}%  "
        f"top5_ret={m.oos_top5_avg_return*100:+.2f}% (med {m.oos_top5_med_return*100:+.2f}%)  "
        f"sharpe={m.oos_top5_sharpe:.2f}  winrate={m.oos_top5_winrate*100:.0f}%  "
        f"mdd={m.oos_max_drawdown*100:+.1f}%"
    )
