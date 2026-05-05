"""On-demand single-stock meta models for Stock Edge.

These models are intentionally local and per-trigger. They train only from the
target stock's historical bars before the report date, then score the latest
state. Heavy global preset training can later replace them with persisted
artifacts without changing the strategy matrix contract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MetaModelProfile:
    available: bool
    reason: str
    model_name: str
    sample_count: int
    positive_rate: float | None
    probability: float | None
    oos_hit_rate: float | None
    oos_auc_proxy: float | None
    feature_snapshot: dict[str, float]
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_right_tail_meta_gbm(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    if not params.get("enabled", True):
        return _missing("right-tail meta GBM disabled", "hist_gradient_boosting")
    features, labels, latest = _feature_label_frame(daily_bars, params=params, risk_params=risk_params)
    min_samples = int(params.get("min_samples", 140))
    if len(features) < min_samples or labels.nunique() < 2:
        return _missing(f"有效训练样本 {len(features)} 个，或缺少正负标签。", "hist_gradient_boosting")
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier

        split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
        split = min(max(split, 40), len(features) - 20)
        X_train, y_train = features.iloc[:split], labels.iloc[:split]
        X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
        model = HistGradientBoostingClassifier(
            max_iter=int(params.get("max_iter", 80)),
            learning_rate=float(params.get("learning_rate", 0.06)),
            max_leaf_nodes=int(params.get("max_leaf_nodes", 15)),
            l2_regularization=float(params.get("l2_regularization", 0.04)),
            random_state=17,
        )
        model.fit(X_train, y_train)
        probability = _positive_probability(model, latest)
        oos_prob = _positive_probabilities(model, X_oos)
        oos_hit_rate = float(y_oos[oos_prob >= np.quantile(oos_prob, 0.65)].mean()) if len(oos_prob) >= 10 else None
        auc_proxy = _auc_proxy(y_oos.to_numpy(), oos_prob) if len(oos_prob) >= 10 else None
        return MetaModelProfile(
            available=True,
            reason="已完成单股右尾收益 GBM 即时训练。",
            model_name="sklearn.HistGradientBoostingClassifier",
            sample_count=int(len(features)),
            positive_rate=float(labels.mean()),
            probability=probability,
            oos_hit_rate=oos_hit_rate,
            oos_auc_proxy=auc_proxy,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"right-tail meta GBM 训练失败：{type(exc).__name__}: {exc}", "hist_gradient_boosting")


def build_temporal_sequence_ranker(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    if not params.get("enabled", True):
        return _missing("temporal sequence ranker disabled", "mlp_sequence_ranker")
    features, labels, latest = _feature_label_frame(daily_bars, params=params, risk_params=risk_params, sequence=True)
    min_samples = int(params.get("min_samples", 160))
    if len(features) < min_samples or labels.nunique() < 2:
        return _missing(f"有效序列训练样本 {len(features)} 个，或缺少正负标签。", "mlp_sequence_ranker")
    try:
        from sklearn.neural_network import MLPClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
        split = min(max(split, 50), len(features) - 20)
        X_train, y_train = features.iloc[:split], labels.iloc[:split]
        X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
        model = make_pipeline(
            StandardScaler(),
            MLPClassifier(
                hidden_layer_sizes=tuple(params.get("hidden_layer_sizes", [12, 6])),
                activation="relu",
                alpha=float(params.get("alpha", 0.004)),
                max_iter=int(params.get("max_iter", 180)),
                random_state=23,
                early_stopping=True,
                n_iter_no_change=12,
            ),
        )
        model.fit(X_train, y_train)
        probability = _positive_probability(model, latest)
        oos_prob = _positive_probabilities(model, X_oos)
        oos_hit_rate = float(y_oos[oos_prob >= np.quantile(oos_prob, 0.65)].mean()) if len(oos_prob) >= 10 else None
        auc_proxy = _auc_proxy(y_oos.to_numpy(), oos_prob) if len(oos_prob) >= 10 else None
        return MetaModelProfile(
            available=True,
            reason="已完成单股多周期序列 MLP 即时训练。",
            model_name="sklearn.MLPClassifier(sequence)",
            sample_count=int(len(features)),
            positive_rate=float(labels.mean()),
            probability=probability,
            oos_hit_rate=oos_hit_rate,
            oos_auc_proxy=auc_proxy,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"temporal sequence ranker 训练失败：{type(exc).__name__}: {exc}", "mlp_sequence_ranker")


def build_target_stop_survival_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Train a target-before-stop hazard classifier from target-stock history."""
    if not params.get("enabled", True):
        return _missing("target/stop survival model disabled", "target_stop_survival_rf")
    frame = _target_stop_frame(daily_bars, params=params, risk_params=risk_params)
    features, labels, latest, meta = frame
    min_samples = int(params.get("min_samples", 150))
    if len(features) < min_samples or labels.nunique() < 2:
        return _missing(f"有效目标/止损路径样本 {len(features)} 个，或缺少正负标签。", "target_stop_survival_rf")
    try:
        from sklearn.ensemble import RandomForestClassifier

        split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
        split = min(max(split, 50), len(features) - 20)
        X_train, y_train = features.iloc[:split], labels.iloc[:split]
        X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
        model = RandomForestClassifier(
            n_estimators=int(params.get("n_estimators", 96)),
            max_depth=int(params.get("max_depth", 5)),
            min_samples_leaf=int(params.get("min_samples_leaf", 8)),
            class_weight="balanced_subsample",
            random_state=31,
            n_jobs=1,
        )
        model.fit(X_train, y_train)
        probability = _positive_probability(model, latest)
        oos_prob = _positive_probabilities(model, X_oos)
        oos_hit_rate = float(y_oos[oos_prob >= np.quantile(oos_prob, 0.65)].mean()) if len(oos_prob) >= 10 else None
        auc_proxy = _auc_proxy(y_oos.to_numpy(), oos_prob) if len(oos_prob) >= 10 else None
        return MetaModelProfile(
            available=True,
            reason="已完成单股目标/止损先后顺序随机森林训练。",
            model_name="sklearn.RandomForestClassifier(target_stop_hazard)",
            sample_count=int(len(features)),
            positive_rate=float(labels.mean()),
            probability=probability,
            oos_hit_rate=oos_hit_rate,
            oos_auc_proxy=auc_proxy,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
            extra={
                "target_first_probability": probability,
                "stop_first_probability": _clip01(1.0 - probability),
                "historical_target_first_rate": meta["target_first_rate"],
                "historical_stop_first_rate": meta["stop_first_rate"],
                "historical_neither_rate": meta["neither_rate"],
                "avg_days_to_target": meta["avg_days_to_target"],
                "avg_days_to_stop": meta["avg_days_to_stop"],
                "target_pct": meta["target_pct"],
                "stop_pct": meta["stop_pct"],
                "horizon_bars": meta["horizon_bars"],
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"target/stop survival model 训练失败：{type(exc).__name__}: {exc}", "target_stop_survival_rf")


def build_stop_loss_hazard_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Train a standalone stop-loss hazard classifier."""
    if not params.get("enabled", True):
        return _missing("stop-loss hazard model disabled", "stop_loss_hazard_rf")
    features, labels, latest, meta = _stop_loss_hazard_frame(daily_bars, params=params, risk_params=risk_params)
    min_samples = int(params.get("min_samples", 140))
    if len(features) < min_samples or labels.nunique() < 2:
        return _missing(f"有效止损危险率样本 {len(features)} 个，或缺少正负标签。", "stop_loss_hazard_rf")
    try:
        from sklearn.ensemble import RandomForestClassifier

        split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
        split = min(max(split, 50), len(features) - 20)
        X_train, y_train = features.iloc[:split], labels.iloc[:split]
        X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
        model = RandomForestClassifier(
            n_estimators=int(params.get("n_estimators", 128)),
            max_depth=int(params.get("max_depth", 6)),
            min_samples_leaf=int(params.get("min_samples_leaf", 8)),
            class_weight="balanced_subsample",
            random_state=89,
            n_jobs=1,
        )
        model.fit(X_train, y_train)
        probability = _positive_probability(model, latest)
        oos_prob = _positive_probabilities(model, X_oos)
        oos_hit_rate = float(y_oos[oos_prob >= np.quantile(oos_prob, 0.65)].mean()) if len(oos_prob) >= 10 else None
        auc_proxy = _auc_proxy(y_oos.to_numpy(), oos_prob) if len(oos_prob) >= 10 else None
        return MetaModelProfile(
            available=True,
            reason="已完成单股止损危险率随机森林即时训练。",
            model_name="sklearn.RandomForestClassifier(stop_loss_hazard)",
            sample_count=int(len(features)),
            positive_rate=float(labels.mean()),
            probability=probability,
            oos_hit_rate=oos_hit_rate,
            oos_auc_proxy=auc_proxy,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
            extra={
                **meta,
                "stop_hazard_probability": probability,
                "survival_probability": _clip01(1.0 - probability),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"stop-loss hazard model 训练失败：{type(exc).__name__}: {exc}", "stop_loss_hazard_rf")


def build_gap_risk_open_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Train a next-open adverse gap-risk classifier from daily bars."""
    if not params.get("enabled", True):
        return _missing("gap-risk open model disabled", "gap_risk_open_rf")
    features, labels, latest, meta = _gap_risk_open_frame(daily_bars, params=params, risk_params=risk_params)
    min_samples = int(params.get("min_samples", 140))
    if len(features) < min_samples or labels.nunique() < 2:
        return _missing(f"有效跳空风险样本 {len(features)} 个，或缺少正负标签。", "gap_risk_open_rf")
    try:
        from sklearn.ensemble import RandomForestClassifier

        split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
        split = min(max(split, 50), len(features) - 20)
        X_train, y_train = features.iloc[:split], labels.iloc[:split]
        X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
        model = RandomForestClassifier(
            n_estimators=int(params.get("n_estimators", 128)),
            max_depth=int(params.get("max_depth", 6)),
            min_samples_leaf=int(params.get("min_samples_leaf", 8)),
            class_weight="balanced_subsample",
            random_state=109,
            n_jobs=1,
        )
        model.fit(X_train, y_train)
        probability = _positive_probability(model, latest)
        oos_prob = _positive_probabilities(model, X_oos)
        oos_hit_rate = float(y_oos[oos_prob >= np.quantile(oos_prob, 0.65)].mean()) if len(oos_prob) >= 10 else None
        auc_proxy = _auc_proxy(y_oos.to_numpy(), oos_prob) if len(oos_prob) >= 10 else None
        return MetaModelProfile(
            available=True,
            reason="已完成次日开盘跳空风险随机森林即时训练。",
            model_name="sklearn.RandomForestClassifier(gap_risk_open)",
            sample_count=int(len(features)),
            positive_rate=float(labels.mean()),
            probability=probability,
            oos_hit_rate=oos_hit_rate,
            oos_auc_proxy=auc_proxy,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
            extra={
                **meta,
                "adverse_gap_probability": probability,
                "gap_survival_probability": _clip01(1.0 - probability),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"gap-risk open model 训练失败：{type(exc).__name__}: {exc}", "gap_risk_open_rf")


def build_multi_horizon_target_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Train per-trigger classifiers for several target/horizon contracts."""
    if not params.get("enabled", True):
        return _missing("multi-horizon target model disabled", "multi_horizon_target_gbm")
    scenarios = _target_scenarios(params, risk_params)
    if not scenarios:
        return _missing("multi-horizon target model has no scenarios", "multi_horizon_target_gbm")
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier

        rows: list[dict[str, Any]] = []
        for scenario in scenarios:
            scenario_params = {
                **params,
                "horizon_bars": int(scenario["horizon_bars"]),
                "target_pct": float(scenario["target_pct"]),
            }
            features, labels, latest = _feature_label_frame(daily_bars, params=scenario_params, risk_params=risk_params, sequence=True)
            min_samples = int(params.get("min_samples", 130))
            if len(features) < min_samples or labels.nunique() < 2:
                rows.append({
                    **scenario,
                    "available": False,
                    "reason": f"有效样本 {len(features)} 个，或缺少正负标签。",
                })
                continue
            split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
            split = min(max(split, 50), len(features) - 20)
            X_train, y_train = features.iloc[:split], labels.iloc[:split]
            X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
            model = HistGradientBoostingClassifier(
                max_iter=int(params.get("max_iter", 70)),
                learning_rate=float(params.get("learning_rate", 0.055)),
                max_leaf_nodes=int(params.get("max_leaf_nodes", 13)),
                l2_regularization=float(params.get("l2_regularization", 0.05)),
                random_state=41 + len(rows),
            )
            model.fit(X_train, y_train)
            probability = _positive_probability(model, latest)
            oos_prob = _positive_probabilities(model, X_oos)
            oos_hit_rate = float(y_oos[oos_prob >= np.quantile(oos_prob, 0.65)].mean()) if len(oos_prob) >= 10 else None
            auc_proxy = _auc_proxy(y_oos.to_numpy(), oos_prob) if len(oos_prob) >= 10 else None
            base_rate = float(labels.mean())
            target_pct = float(scenario["target_pct"])
            horizon_bars = int(scenario["horizon_bars"])
            annualized_pressure = (target_pct / 100.0) / max(horizon_bars / 252.0, 1e-6)
            rows.append({
                **scenario,
                "available": True,
                "sample_count": int(len(features)),
                "base_rate": base_rate,
                "probability": probability,
                "oos_hit_rate": oos_hit_rate,
                "oos_auc_proxy": auc_proxy,
                "expected_return_proxy": probability * target_pct,
                "annualized_pressure": annualized_pressure,
            })
        available_rows = [row for row in rows if row.get("available")]
        if not available_rows:
            reason = "；".join(str(row.get("reason")) for row in rows[:3])
            return _missing(f"multi-horizon target model 无可用场景：{reason}", "multi_horizon_target_gbm")
        best = max(
            available_rows,
            key=lambda row: (
                float(row.get("probability") or 0.0) - float(row.get("base_rate") or 0.0)
                + 0.25 * float(row.get("expected_return_proxy") or 0.0) / 50.0
            ),
        )
        return MetaModelProfile(
            available=True,
            reason="已完成单股多目标周期即时训练。",
            model_name="sklearn.HistGradientBoostingClassifier(multi_horizon)",
            sample_count=int(sum(int(row.get("sample_count") or 0) for row in available_rows)),
            positive_rate=float(best.get("base_rate") or 0.0),
            probability=float(best.get("probability") or 0.0),
            oos_hit_rate=best.get("oos_hit_rate"),
            oos_auc_proxy=best.get("oos_auc_proxy"),
            feature_snapshot={},
            extra={
                "best_key": best.get("key"),
                "best_label": best.get("label"),
                "rows": rows,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"multi-horizon target model 训练失败：{type(exc).__name__}: {exc}", "multi_horizon_target_gbm")


def build_target_ladder_probability_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Train target-ladder classifiers with stop-risk and time-to-hit context."""
    if not params.get("enabled", True):
        return _missing("target ladder probability model disabled", "target_ladder_probability_gbm")
    scenarios = _target_scenarios(params, risk_params)
    if not scenarios:
        return _missing("target ladder probability model has no scenarios", "target_ladder_probability_gbm")
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier

        rows: list[dict[str, Any]] = []
        for scenario in scenarios:
            scenario_params = {
                **params,
                "horizon_bars": int(scenario["horizon_bars"]),
                "target_pct": float(scenario["target_pct"]),
            }
            features, labels, latest, meta = _target_ladder_frame(daily_bars, params=scenario_params, risk_params=risk_params)
            min_samples = int(params.get("min_samples", 130))
            if len(features) < min_samples or labels.nunique() < 2:
                rows.append({**scenario, "available": False, "reason": f"有效阶梯样本 {len(features)} 个，或缺少正负标签。"})
                continue
            split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
            split = min(max(split, 50), len(features) - 20)
            X_train, y_train = features.iloc[:split], labels.iloc[:split]
            X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
            model = HistGradientBoostingClassifier(
                max_iter=int(params.get("max_iter", 72)),
                learning_rate=float(params.get("learning_rate", 0.055)),
                max_leaf_nodes=int(params.get("max_leaf_nodes", 13)),
                l2_regularization=float(params.get("l2_regularization", 0.05)),
                random_state=83 + len(rows),
            )
            model.fit(X_train, y_train)
            probability = _positive_probability(model, latest)
            oos_prob = _positive_probabilities(model, X_oos)
            oos_hit_rate = float(y_oos[oos_prob >= np.quantile(oos_prob, 0.65)].mean()) if len(oos_prob) >= 10 else None
            auc_proxy = _auc_proxy(y_oos.to_numpy(), oos_prob) if len(oos_prob) >= 10 else None
            base_rate = float(labels.mean())
            target_pct = float(scenario["target_pct"])
            stop_penalty = float(meta.get("stop_before_target_rate") or 0.0)
            rows.append({
                **scenario,
                "available": True,
                "sample_count": int(len(features)),
                "base_rate": base_rate,
                "probability": probability,
                "oos_hit_rate": oos_hit_rate,
                "oos_auc_proxy": auc_proxy,
                "stop_before_target_rate": stop_penalty,
                "avg_days_to_target": meta.get("avg_days_to_target"),
                "median_days_to_target": meta.get("median_days_to_target"),
                "expected_return_proxy": probability * target_pct - stop_penalty * float(meta.get("stop_pct") or 0.0),
                "target_stop_ratio": probability / max(stop_penalty, 0.02),
            })
        available_rows = [row for row in rows if row.get("available")]
        if not available_rows:
            reason = "；".join(str(row.get("reason")) for row in rows[:3])
            return _missing(f"target ladder probability model 无可用场景：{reason}", "target_ladder_probability_gbm")
        best = max(
            available_rows,
            key=lambda row: (
                float(row.get("probability") or 0.0) - float(row.get("base_rate") or 0.0)
                + 0.18 * float(row.get("expected_return_proxy") or 0.0) / 30.0
                - 0.16 * float(row.get("stop_before_target_rate") or 0.0)
            ),
        )
        return MetaModelProfile(
            available=True,
            reason="已完成单股目标阶梯概率即时训练。",
            model_name="sklearn.HistGradientBoostingClassifier(target_ladder)",
            sample_count=int(sum(int(row.get("sample_count") or 0) for row in available_rows)),
            positive_rate=float(best.get("base_rate") or 0.0),
            probability=float(best.get("probability") or 0.0),
            oos_hit_rate=best.get("oos_hit_rate"),
            oos_auc_proxy=best.get("oos_auc_proxy"),
            feature_snapshot={},
            extra={
                "best_key": best.get("key"),
                "best_label": best.get("label"),
                "rows": rows,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"target ladder probability model 训练失败：{type(exc).__name__}: {exc}", "target_ladder_probability_gbm")


def build_path_shape_mixture_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Fit a single-stock path-shape Gaussian mixture and score latest state."""
    if not params.get("enabled", True):
        return _missing("path-shape mixture model disabled", "path_shape_gmm")
    features, labels, latest, meta = _path_shape_mixture_frame(daily_bars, params=params, risk_params=risk_params)
    min_samples = int(params.get("min_samples", 150))
    if len(features) < min_samples or labels.nunique() < 2:
        return _missing(f"有效路径形态样本 {len(features)} 个，或缺少正负标签。", "path_shape_gmm")
    try:
        from sklearn.mixture import GaussianMixture
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        components = max(2, min(int(params.get("n_components", 5)), max(2, len(features) // 35)))
        model = make_pipeline(
            StandardScaler(),
            GaussianMixture(
                n_components=components,
                covariance_type=str(params.get("covariance_type", "diag")),
                reg_covar=float(params.get("reg_covar", 0.0002)),
                random_state=97,
            ),
        )
        model.fit(features)
        gmm = model.named_steps["gaussianmixture"]
        posterior = gmm.predict_proba(model.named_steps["standardscaler"].transform(latest))[0]
        assignments = gmm.predict(model.named_steps["standardscaler"].transform(features))
        cluster_rows: list[dict[str, Any]] = []
        weighted_prob = 0.0
        weighted_return = 0.0
        weighted_stop = 0.0
        for cluster_id in range(components):
            mask = assignments == cluster_id
            cluster_labels = labels[mask]
            returns = meta["forward_returns"][mask]
            stops = meta["stop_first"][mask]
            if len(cluster_labels) == 0:
                hit_rate = float(labels.mean())
                avg_return = float(meta["forward_returns"].mean())
                stop_rate = float(meta["stop_first"].mean())
            else:
                hit_rate = float(cluster_labels.mean())
                avg_return = float(np.mean(returns))
                stop_rate = float(np.mean(stops))
            weight = float(posterior[cluster_id])
            weighted_prob += weight * hit_rate
            weighted_return += weight * avg_return
            weighted_stop += weight * stop_rate
            cluster_rows.append({
                "cluster": cluster_id,
                "posterior": weight,
                "sample_count": int(mask.sum()),
                "hit_rate": hit_rate,
                "avg_forward_return_pct": avg_return * 100.0,
                "stop_first_rate": stop_rate,
            })
        dominant = max(cluster_rows, key=lambda row: float(row["posterior"]))
        return MetaModelProfile(
            available=True,
            reason="已完成单股路径形态 GMM 即时拟合。",
            model_name="sklearn.GaussianMixture(path_shape)",
            sample_count=int(len(features)),
            positive_rate=float(labels.mean()),
            probability=float(_clip01(weighted_prob)),
            oos_hit_rate=None,
            oos_auc_proxy=None,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
            extra={
                "target_pct": meta["target_pct"],
                "stop_pct": meta["stop_pct"],
                "horizon_bars": meta["horizon_bars"],
                "expected_forward_return_pct": round(weighted_return * 100.0, 4),
                "weighted_stop_first_rate": round(float(weighted_stop), 6),
                "dominant_cluster": dominant,
                "clusters": cluster_rows,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"path-shape mixture model 训练失败：{type(exc).__name__}: {exc}", "path_shape_gmm")


def build_mfe_mae_surface_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Predict future max favorable/adverse excursions from single-stock history."""
    if not params.get("enabled", True):
        return _missing("MFE/MAE surface model disabled", "mfe_mae_surface_gbr")
    features, labels, latest, meta = _mfe_mae_surface_frame(daily_bars, params=params, risk_params=risk_params)
    min_samples = int(params.get("min_samples", 150))
    if len(features) < min_samples:
        return _missing(f"有效 MFE/MAE 样本 {len(features)} 个。", "mfe_mae_surface_gbr")
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor

        split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
        split = min(max(split, 50), len(features) - 20)
        X_train, X_oos = features.iloc[:split], features.iloc[split:]
        y_up_train = labels["mfe"].iloc[:split]
        y_down_train = labels["mae"].iloc[:split]
        y_up_oos = labels["mfe"].iloc[split:]
        y_down_oos = labels["mae"].iloc[split:]
        common = {
            "max_iter": int(params.get("max_iter", 80)),
            "learning_rate": float(params.get("learning_rate", 0.055)),
            "max_leaf_nodes": int(params.get("max_leaf_nodes", 13)),
            "l2_regularization": float(params.get("l2_regularization", 0.05)),
        }
        up_model = HistGradientBoostingRegressor(**common, random_state=101)
        down_model = HistGradientBoostingRegressor(**common, random_state=103)
        up_model.fit(X_train, y_up_train)
        down_model.fit(X_train, y_down_train)
        expected_up = max(float(up_model.predict(latest)[0]), 0.0)
        expected_down = max(float(down_model.predict(latest)[0]), 0.0)
        target_pct = float(meta["target_pct"]) / 100.0
        stop_pct = float(meta["stop_pct"]) / 100.0
        reward_risk = expected_up / max(expected_down, 0.01)
        probability = _clip01(0.5 + (expected_up - target_pct) * 1.8 - (expected_down - stop_pct) * 1.2 + (reward_risk - 1.5) * 0.08)
        oos_hit_rate = None
        if len(X_oos) >= 10:
            oos_up = up_model.predict(X_oos)
            oos_down = down_model.predict(X_oos)
            oos_select = (oos_up >= target_pct) & (oos_up / np.maximum(oos_down, 0.01) >= 1.5)
            if oos_select.any():
                oos_hit_rate = float((y_up_oos[oos_select] >= target_pct).mean())
        return MetaModelProfile(
            available=True,
            reason="已完成单股 MFE/MAE 收益风险面即时回归。",
            model_name="sklearn.HistGradientBoostingRegressor(mfe_mae_surface)",
            sample_count=int(len(features)),
            positive_rate=float((labels["mfe"] >= target_pct).mean()),
            probability=float(probability),
            oos_hit_rate=oos_hit_rate,
            oos_auc_proxy=None,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
            extra={
                **meta,
                "expected_mfe_pct": round(expected_up * 100.0, 4),
                "expected_mae_pct": round(expected_down * 100.0, 4),
                "expected_reward_risk": round(float(reward_risk), 4),
                "historical_mfe_median_pct": round(float(labels["mfe"].median()) * 100.0, 4),
                "historical_mae_median_pct": round(float(labels["mae"].median()) * 100.0, 4),
                "oos_mae_mean_pct": round(float(y_down_oos.mean()) * 100.0, 4) if len(y_down_oos) else None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"MFE/MAE surface model 训练失败：{type(exc).__name__}: {exc}", "mfe_mae_surface_gbr")


def build_forward_entry_timing_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Estimate whether the next five sessions favor buy-now or wait-for-pullback."""
    if not params.get("enabled", True):
        return _missing("forward entry timing model disabled", "forward_entry_timing_rf")
    frame = _entry_timing_frame(daily_bars, params=params, risk_params=risk_params)
    features, labels, latest, meta = frame
    min_samples = int(params.get("min_samples", 140))
    if len(features) < min_samples or labels.nunique() < 2:
        return _missing(f"有效择时样本 {len(features)} 个，或缺少正负标签。", "forward_entry_timing_rf")
    try:
        from sklearn.ensemble import RandomForestClassifier

        split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
        split = min(max(split, 50), len(features) - 20)
        X_train, y_train = features.iloc[:split], labels.iloc[:split]
        X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
        model = RandomForestClassifier(
            n_estimators=int(params.get("n_estimators", 120)),
            max_depth=int(params.get("max_depth", 5)),
            min_samples_leaf=int(params.get("min_samples_leaf", 8)),
            class_weight="balanced_subsample",
            random_state=53,
            n_jobs=1,
        )
        model.fit(X_train, y_train)
        probability = _positive_probability(model, latest)
        oos_prob = _positive_probabilities(model, X_oos)
        oos_hit_rate = float(y_oos[oos_prob >= np.quantile(oos_prob, 0.65)].mean()) if len(oos_prob) >= 10 else None
        auc_proxy = _auc_proxy(y_oos.to_numpy(), oos_prob) if len(oos_prob) >= 10 else None
        return MetaModelProfile(
            available=True,
            reason="已完成未来5日入场择时随机森林训练。",
            model_name="sklearn.RandomForestClassifier(forward_entry_timing)",
            sample_count=int(len(features)),
            positive_rate=float(labels.mean()),
            probability=probability,
            oos_hit_rate=oos_hit_rate,
            oos_auc_proxy=auc_proxy,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
            extra={
                **meta,
                "buy_now_probability": probability,
                "wait_probability": _clip01(1.0 - probability),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"forward entry timing model 训练失败：{type(exc).__name__}: {exc}", "forward_entry_timing_rf")


def build_entry_price_surface_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Train a route classifier for today's entry, pullback, breakout, or avoid.

    This is deliberately an execution model rather than a narrative model: each
    historical date is labelled by the route with the best realized continuous
    utility over the configured holding horizon.
    """
    if not params.get("enabled", True):
        return _missing("entry price surface model disabled", "entry_price_surface_rf")
    features, labels, latest, meta = _entry_price_surface_frame(daily_bars, params=params, risk_params=risk_params)
    min_samples = int(params.get("min_samples", 150))
    if len(features) < min_samples or labels.nunique() < 2:
        return _missing(f"有效买入价格面样本 {len(features)} 个，或缺少多路线标签。", "entry_price_surface_rf")
    try:
        from sklearn.ensemble import RandomForestClassifier

        split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
        split = min(max(split, 50), len(features) - 20)
        X_train, y_train = features.iloc[:split], labels.iloc[:split]
        X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
        model = RandomForestClassifier(
            n_estimators=int(params.get("n_estimators", 140)),
            max_depth=int(params.get("max_depth", 6)),
            min_samples_leaf=int(params.get("min_samples_leaf", 8)),
            class_weight="balanced_subsample",
            random_state=71,
            n_jobs=1,
        )
        model.fit(X_train, y_train)
        latest_prob = model.predict_proba(latest)[0]
        route_probs = {meta["route_names"][int(cls)]: float(prob) for cls, prob in zip(model.classes_, latest_prob, strict=False)}
        for route in meta["route_names"].values():
            route_probs.setdefault(route, 0.0)
        best_route = max(route_probs, key=route_probs.get)
        probability = float(route_probs[best_route])
        oos_prob = model.predict_proba(X_oos) if len(X_oos) else np.array([])
        oos_hit_rate = None
        auc_proxy = None
        if len(oos_prob) >= 10:
            pred = np.array([model.classes_[int(np.argmax(row))] for row in oos_prob])
            oos_hit_rate = float((pred == y_oos.to_numpy()).mean())
            if 0 in model.classes_:
                avoid_idx = list(model.classes_).index(0)
                non_avoid = (y_oos.to_numpy() != 0).astype(int)
                auc_proxy = _auc_proxy(non_avoid, 1.0 - oos_prob[:, avoid_idx])
        return MetaModelProfile(
            available=True,
            reason="已完成买入价格面路线随机森林即时训练。",
            model_name="sklearn.RandomForestClassifier(entry_price_surface)",
            sample_count=int(len(features)),
            positive_rate=float((labels != 0).mean()),
            probability=probability,
            oos_hit_rate=oos_hit_rate,
            oos_auc_proxy=auc_proxy,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
            extra={
                **meta,
                "best_route": best_route,
                "route_probabilities": {key: round(float(value), 6) for key, value in route_probs.items()},
                "tradable_probability": round(float(1.0 - route_probs.get("avoid", 0.0)), 6),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"entry price surface model 训练失败：{type(exc).__name__}: {exc}", "entry_price_surface_rf")


def build_pullback_rebound_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Estimate whether a pullback setup historically rebounds before breaking."""
    if not params.get("enabled", True):
        return _missing("pullback rebound model disabled", "pullback_rebound_rf")
    frame = _pullback_rebound_frame(daily_bars, params=params, risk_params=risk_params)
    features, labels, latest, meta = frame
    min_samples = int(params.get("min_samples", 130))
    if len(features) < min_samples or labels.nunique() < 2:
        return _missing(f"有效回踩反弹样本 {len(features)} 个，或缺少正负标签。", "pullback_rebound_rf")
    try:
        from sklearn.ensemble import RandomForestClassifier

        split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
        split = min(max(split, 50), len(features) - 20)
        X_train, y_train = features.iloc[:split], labels.iloc[:split]
        X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
        model = RandomForestClassifier(
            n_estimators=int(params.get("n_estimators", 120)),
            max_depth=int(params.get("max_depth", 5)),
            min_samples_leaf=int(params.get("min_samples_leaf", 8)),
            class_weight="balanced_subsample",
            random_state=61,
            n_jobs=1,
        )
        model.fit(X_train, y_train)
        probability = _positive_probability(model, latest)
        oos_prob = _positive_probabilities(model, X_oos)
        oos_hit_rate = float(y_oos[oos_prob >= np.quantile(oos_prob, 0.65)].mean()) if len(oos_prob) >= 10 else None
        auc_proxy = _auc_proxy(y_oos.to_numpy(), oos_prob) if len(oos_prob) >= 10 else None
        return MetaModelProfile(
            available=True,
            reason="已完成单股回踩反弹随机森林训练。",
            model_name="sklearn.RandomForestClassifier(pullback_rebound)",
            sample_count=int(len(features)),
            positive_rate=float(labels.mean()),
            probability=probability,
            oos_hit_rate=oos_hit_rate,
            oos_auc_proxy=auc_proxy,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
            extra=meta,
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"pullback rebound model 训练失败：{type(exc).__name__}: {exc}", "pullback_rebound_rf")


def build_squeeze_breakout_model(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> MetaModelProfile:
    """Estimate whether volatility compression can expand upward."""
    if not params.get("enabled", True):
        return _missing("squeeze breakout model disabled", "squeeze_breakout_gbm")
    frame = _squeeze_breakout_frame(daily_bars, params=params, risk_params=risk_params)
    features, labels, latest, meta = frame
    min_samples = int(params.get("min_samples", 130))
    if len(features) < min_samples or labels.nunique() < 2:
        return _missing(f"有效收敛突破样本 {len(features)} 个，或缺少正负标签。", "squeeze_breakout_gbm")
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier

        split = max(int(len(features) * float(params.get("train_fraction", 0.72))), min_samples // 2)
        split = min(max(split, 50), len(features) - 20)
        X_train, y_train = features.iloc[:split], labels.iloc[:split]
        X_oos, y_oos = features.iloc[split:], labels.iloc[split:]
        model = HistGradientBoostingClassifier(
            max_iter=int(params.get("max_iter", 80)),
            learning_rate=float(params.get("learning_rate", 0.055)),
            max_leaf_nodes=int(params.get("max_leaf_nodes", 13)),
            l2_regularization=float(params.get("l2_regularization", 0.05)),
            random_state=67,
        )
        model.fit(X_train, y_train)
        probability = _positive_probability(model, latest)
        oos_prob = _positive_probabilities(model, X_oos)
        oos_hit_rate = float(y_oos[oos_prob >= np.quantile(oos_prob, 0.65)].mean()) if len(oos_prob) >= 10 else None
        auc_proxy = _auc_proxy(y_oos.to_numpy(), oos_prob) if len(oos_prob) >= 10 else None
        return MetaModelProfile(
            available=True,
            reason="已完成单股波动收敛突破 GBM 即时训练。",
            model_name="sklearn.HistGradientBoostingClassifier(squeeze_breakout)",
            sample_count=int(len(features)),
            positive_rate=float(labels.mean()),
            probability=probability,
            oos_hit_rate=oos_hit_rate,
            oos_auc_proxy=auc_proxy,
            feature_snapshot={k: round(float(v), 6) for k, v in latest.iloc[0].to_dict().items()},
            extra=meta,
        )
    except Exception as exc:  # noqa: BLE001
        return _missing(f"squeeze breakout model 训练失败：{type(exc).__name__}: {exc}", "squeeze_breakout_gbm")


def _feature_label_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
    sequence: bool = False,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    df = _prepare(daily_bars)
    horizon = int(params.get("horizon_bars", 40))
    target_pct = float(params.get("target_pct", risk_params.get("right_tail_target_pct", 30.0))) / 100.0
    if len(df) < horizon + 80:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame()
    feat = pd.DataFrame(index=df.index)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    amount = df.get("amount", pd.Series([0.0] * len(df), index=df.index)).astype(float)
    for w in [3, 5, 10, 20, 40, 60]:
        feat[f"ret_{w}"] = close.pct_change(w).fillna(0.0)
        feat[f"vol_{w}"] = close.pct_change().rolling(w, min_periods=max(3, w // 3)).std().fillna(0.0)
    range_high = high.rolling(60, min_periods=20).max()
    range_low = low.rolling(60, min_periods=20).min()
    feat["range_pos_60"] = ((close - range_low) / (range_high - range_low).replace(0, np.nan)).fillna(0.5)
    feat["drawdown_20"] = (close / close.rolling(20, min_periods=10).max() - 1.0).fillna(0.0)
    feat["amount_ratio_20"] = (amount / amount.rolling(20, min_periods=5).mean().replace(0, np.nan)).fillna(1.0)
    tr = pd.concat([(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    feat["atr14_pct"] = (tr.rolling(14, min_periods=5).mean() / close.replace(0, np.nan)).fillna(0.04)
    if sequence:
        for w in [5, 10, 20]:
            feat[f"ret_accel_{w}"] = feat[f"ret_{w}"] - feat[f"ret_{w}"].shift(w).fillna(0.0)
        feat["vol_slope_5_20"] = feat["vol_5"] - feat["vol_20"]
        feat["range_pos_delta_10"] = feat["range_pos_60"] - feat["range_pos_60"].shift(10).fillna(0.5)
    labels = []
    rows = []
    for idx in range(60, len(df) - horizon):
        entry = float(close.iloc[idx])
        future = high.iloc[idx + 1 : idx + 1 + horizon]
        if entry <= 0 or future.empty:
            continue
        rows.append(idx)
        labels.append(int(float(future.max()) >= entry * (1.0 + target_pct)))
    X = feat.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = pd.Series(labels, index=X.index, dtype=int)
    latest = feat.tail(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return X, y, latest


def _pullback_rebound_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, dict[str, Any]]:
    df = _prepare(daily_bars)
    horizon = int(params.get("horizon_bars", 20))
    target_pct = float(params.get("target_pct", 18.0)) / 100.0
    stop_pct = float(params.get("stop_pct", risk_params.get("max_stop_distance_pct", 10.0))) / 100.0
    if len(df) < horizon + 90:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_path_meta(target_pct, stop_pct, horizon)
    base, _, latest = _feature_label_frame(
        daily_bars,
        params={**params, "horizon_bars": horizon, "target_pct": target_pct * 100.0},
        risk_params=risk_params,
        sequence=True,
    )
    if base.empty:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_path_meta(target_pct, stop_pct, horizon)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    ma20 = close.rolling(20, min_periods=10).mean()
    ma60 = close.rolling(60, min_periods=20).mean()
    rolling_high20 = high.rolling(20, min_periods=10).max()
    rolling_low20 = low.rolling(20, min_periods=10).min()
    amount = df.get("amount", pd.Series([0.0] * len(df), index=df.index)).astype(float)
    enriched = base.copy()
    enriched["dist_ma20"] = (close / ma20 - 1.0).reindex(enriched.index).fillna(0.0)
    enriched["ma20_ma60"] = (ma20 / ma60 - 1.0).reindex(enriched.index).fillna(0.0)
    enriched["drawdown_20"] = (close / rolling_high20 - 1.0).reindex(enriched.index).fillna(0.0)
    enriched["rebound_from_low20"] = (close / rolling_low20 - 1.0).reindex(enriched.index).fillna(0.0)
    enriched["amount_ratio_5_20"] = (
        amount.rolling(5, min_periods=2).mean() / amount.rolling(20, min_periods=5).mean().replace(0, np.nan)
    ).reindex(enriched.index).fillna(1.0)
    latest = enriched.tail(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    rows = []
    labels = []
    target_first = 0
    stop_first = 0
    for idx in enriched.index:
        entry = float(close.iloc[idx])
        if entry <= 0:
            continue
        hit_target_day, hit_stop_day = _first_event_days(high, low, idx + 1, min(idx + 1 + horizon, len(df)), entry, target_pct, stop_pct)
        if hit_target_day is None and hit_stop_day is None:
            continue
        success = hit_target_day is not None and (hit_stop_day is None or hit_target_day <= hit_stop_day)
        rows.append(idx)
        labels.append(int(success))
        target_first += int(success)
        stop_first += int(not success)
    X = enriched.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0) if rows else pd.DataFrame()
    y = pd.Series(labels, index=X.index, dtype=int)
    meta = {
        **_empty_path_meta(target_pct, stop_pct, horizon),
        "historical_target_first_rate": float(target_first / max(len(labels), 1)) if labels else 0.0,
        "historical_stop_first_rate": float(stop_first / max(len(labels), 1)) if labels else 0.0,
        "current_drawdown_20_pct": round(float(latest.iloc[0].get("drawdown_20", 0.0)) * 100.0, 4) if not latest.empty else 0.0,
        "current_dist_ma20_pct": round(float(latest.iloc[0].get("dist_ma20", 0.0)) * 100.0, 4) if not latest.empty else 0.0,
    }
    return X, y, latest, meta


def _squeeze_breakout_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, dict[str, Any]]:
    df = _prepare(daily_bars)
    horizon = int(params.get("horizon_bars", 25))
    target_pct = float(params.get("target_pct", 22.0)) / 100.0
    if len(df) < horizon + 90:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_path_meta(target_pct, 0.0, horizon)
    base, _, latest = _feature_label_frame(
        daily_bars,
        params={**params, "horizon_bars": horizon, "target_pct": target_pct * 100.0},
        risk_params=risk_params,
        sequence=True,
    )
    if base.empty:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_path_meta(target_pct, 0.0, horizon)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    amount = df.get("amount", pd.Series([0.0] * len(df), index=df.index)).astype(float)
    returns = close.pct_change().fillna(0.0)
    tr = pd.concat([(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr5 = tr.rolling(5, min_periods=3).mean()
    atr20 = tr.rolling(20, min_periods=8).mean()
    high60 = high.rolling(60, min_periods=20).max()
    low60 = low.rolling(60, min_periods=20).min()
    enriched = base.copy()
    enriched["atr5_atr20"] = (atr5 / atr20.replace(0, np.nan)).reindex(enriched.index).fillna(1.0)
    enriched["realized_vol_10_40"] = (
        returns.rolling(10, min_periods=5).std() / returns.rolling(40, min_periods=15).std().replace(0, np.nan)
    ).reindex(enriched.index).fillna(1.0)
    enriched["range_pos_60"] = ((close - low60) / (high60 - low60).replace(0, np.nan)).reindex(enriched.index).fillna(0.5)
    enriched["amount_ratio_5_20"] = (
        amount.rolling(5, min_periods=2).mean() / amount.rolling(20, min_periods=5).mean().replace(0, np.nan)
    ).reindex(enriched.index).fillna(1.0)
    latest = enriched.tail(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    rows = []
    labels = []
    for idx in enriched.index:
        entry = float(close.iloc[idx])
        future = high.iloc[idx + 1 : idx + 1 + horizon]
        if entry <= 0 or future.empty:
            continue
        rows.append(idx)
        labels.append(int(float(future.max()) >= entry * (1.0 + target_pct)))
    X = enriched.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0) if rows else pd.DataFrame()
    y = pd.Series(labels, index=X.index, dtype=int)
    meta = {
        **_empty_path_meta(target_pct, 0.0, horizon),
        "historical_breakout_rate": float(sum(labels) / max(len(labels), 1)) if labels else 0.0,
        "current_atr5_atr20": round(float(latest.iloc[0].get("atr5_atr20", 1.0)), 4) if not latest.empty else 1.0,
        "current_vol10_vol40": round(float(latest.iloc[0].get("realized_vol_10_40", 1.0)), 4) if not latest.empty else 1.0,
        "current_range_pos_60": round(float(latest.iloc[0].get("range_pos_60", 0.5)), 4) if not latest.empty else 0.5,
    }
    return X, y, latest, meta


def _empty_path_meta(target_pct: float, stop_pct: float, horizon: int) -> dict[str, Any]:
    return {
        "horizon_bars": horizon,
        "target_pct": round(target_pct * 100.0, 4),
        "stop_pct": round(stop_pct * 100.0, 4),
    }


def _target_scenarios(params: dict[str, Any], risk_params: dict[str, Any]) -> list[dict[str, Any]]:
    configured = params.get("scenarios") or []
    if configured:
        return [
            {
                "key": str(row.get("key") or f"h{int(row.get('horizon_bars', 40))}_t{float(row.get('target_pct', 30.0)):.0f}"),
                "label": str(row.get("label") or f"{int(row.get('horizon_bars', 40))}日/{float(row.get('target_pct', 30.0)):.0f}%"),
                "horizon_bars": int(row.get("horizon_bars", 40)),
                "target_pct": float(row.get("target_pct", 30.0)),
            }
            for row in configured
        ]
    right_tail = float(risk_params.get("right_tail_target_pct", 50.0))
    return [
        {"key": "tactical_15d_20", "label": "15日/+20%", "horizon_bars": 15, "target_pct": 20.0},
        {"key": "swing_25d_30", "label": "25日/+30%", "horizon_bars": 25, "target_pct": 30.0},
        {"key": "right_tail_40d_target", "label": f"40日/+{right_tail:.0f}%", "horizon_bars": 40, "target_pct": right_tail},
    ]


def _target_ladder_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, dict[str, Any]]:
    df = _prepare(daily_bars)
    horizon = int(params.get("horizon_bars", 25))
    target_pct = float(params.get("target_pct", 25.0)) / 100.0
    stop_pct = float(params.get("stop_pct", risk_params.get("max_stop_distance_pct", 10.0))) / 100.0
    if len(df) < horizon + 90:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_ladder_meta(target_pct, stop_pct, horizon)
    features, _, latest = _feature_label_frame(
        daily_bars,
        params={**params, "horizon_bars": horizon, "target_pct": target_pct * 100.0},
        risk_params=risk_params,
        sequence=True,
    )
    if features.empty:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_ladder_meta(target_pct, stop_pct, horizon)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    amount = df.get("amount", pd.Series([0.0] * len(df), index=df.index)).astype(float)
    enriched = features.copy()
    enriched["target_required_daily_log"] = np.log1p(target_pct) / max(float(horizon), 1.0)
    enriched["target_to_atr14"] = (target_pct / features.get("atr14_pct", pd.Series([0.04] * len(features), index=features.index)).replace(0, np.nan)).fillna(1.0)
    enriched["amount_trend_5_20"] = (
        amount.rolling(5, min_periods=2).mean() / amount.rolling(20, min_periods=5).mean().replace(0, np.nan)
    ).reindex(enriched.index).fillna(1.0)
    rows = []
    labels = []
    days_to_target: list[int] = []
    stop_before_target = 0
    for idx in enriched.index:
        entry = float(close.iloc[idx])
        if entry <= 0:
            continue
        target_day, stop_day = _first_event_days(high, low, idx + 1, min(idx + 1 + horizon, len(df)), entry, target_pct, stop_pct)
        success = target_day is not None and (stop_day is None or target_day <= stop_day)
        rows.append(idx)
        labels.append(int(success))
        if target_day is not None:
            days_to_target.append(target_day)
        if stop_day is not None and (target_day is None or stop_day < target_day):
            stop_before_target += 1
    X = enriched.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0) if rows else pd.DataFrame()
    y = pd.Series(labels, index=X.index, dtype=int)
    meta = {
        **_empty_ladder_meta(target_pct, stop_pct, horizon),
        "avg_days_to_target": float(np.mean(days_to_target)) if days_to_target else None,
        "median_days_to_target": float(np.median(days_to_target)) if days_to_target else None,
        "stop_before_target_rate": float(stop_before_target / max(len(labels), 1)) if labels else 0.0,
    }
    latest = enriched.tail(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return X, y, latest, meta


def _path_shape_mixture_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, dict[str, Any]]:
    df = _prepare(daily_bars)
    horizon = int(params.get("horizon_bars", 25))
    target_pct = float(params.get("target_pct", 25.0)) / 100.0
    stop_pct = float(params.get("stop_pct", risk_params.get("max_stop_distance_pct", 10.0))) / 100.0
    if len(df) < horizon + 90:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_path_shape_meta(target_pct, stop_pct, horizon)
    features, _, latest = _feature_label_frame(
        daily_bars,
        params={**params, "horizon_bars": horizon, "target_pct": target_pct * 100.0},
        risk_params=risk_params,
        sequence=True,
    )
    if features.empty:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_path_shape_meta(target_pct, stop_pct, horizon)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    returns = []
    labels = []
    stop_first_flags = []
    rows = []
    for idx in features.index:
        entry = float(close.iloc[idx])
        if entry <= 0 or idx + horizon >= len(df):
            continue
        future_close = float(close.iloc[idx + horizon])
        target_day, stop_day = _first_event_days(high, low, idx + 1, min(idx + 1 + horizon, len(df)), entry, target_pct, stop_pct)
        hit = target_day is not None and (stop_day is None or target_day <= stop_day)
        stop_first = stop_day is not None and (target_day is None or stop_day < target_day)
        rows.append(idx)
        labels.append(int(hit))
        returns.append(future_close / entry - 1.0)
        stop_first_flags.append(float(stop_first))
    X = features.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0) if rows else pd.DataFrame()
    y = pd.Series(labels, index=X.index, dtype=int)
    latest = features.tail(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    meta = {
        **_empty_path_shape_meta(target_pct, stop_pct, horizon),
        "forward_returns": np.array(returns, dtype=float),
        "stop_first": np.array(stop_first_flags, dtype=float),
    }
    return X, y, latest, meta


def _mfe_mae_surface_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    df = _prepare(daily_bars)
    horizon = int(params.get("horizon_bars", 25))
    target_pct = float(params.get("target_pct", 25.0)) / 100.0
    stop_pct = float(params.get("stop_pct", risk_params.get("max_stop_distance_pct", 10.0))) / 100.0
    if len(df) < horizon + 90:
        return pd.DataFrame(), pd.DataFrame(columns=["mfe", "mae"]), pd.DataFrame(), _empty_mfe_mae_meta(target_pct, stop_pct, horizon)
    features, _, latest = _feature_label_frame(
        daily_bars,
        params={**params, "horizon_bars": horizon, "target_pct": target_pct * 100.0},
        risk_params=risk_params,
        sequence=True,
    )
    if features.empty:
        return pd.DataFrame(), pd.DataFrame(columns=["mfe", "mae"]), pd.DataFrame(), _empty_mfe_mae_meta(target_pct, stop_pct, horizon)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    rows = []
    mfe = []
    mae = []
    for idx in features.index:
        entry = float(close.iloc[idx])
        if entry <= 0 or idx + 1 >= len(df):
            continue
        future_high = high.iloc[idx + 1 : min(idx + 1 + horizon, len(df))]
        future_low = low.iloc[idx + 1 : min(idx + 1 + horizon, len(df))]
        if future_high.empty or future_low.empty:
            continue
        rows.append(idx)
        mfe.append(max(float(future_high.max() / entry - 1.0), 0.0))
        mae.append(max(float(1.0 - future_low.min() / entry), 0.0))
    X = features.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0) if rows else pd.DataFrame()
    y = pd.DataFrame({"mfe": mfe, "mae": mae}, index=X.index)
    latest = features.tail(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return X, y, latest, _empty_mfe_mae_meta(target_pct, stop_pct, horizon)


def _entry_timing_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, dict[str, Any]]:
    df = _prepare(daily_bars)
    entry_window = int(params.get("entry_window_bars", 5))
    horizon = int(params.get("horizon_bars", 25))
    target_pct = float(params.get("target_pct", risk_params.get("right_tail_target_pct", 25.0))) / 100.0
    stop_pct = float(params.get("stop_pct", risk_params.get("max_stop_distance_pct", 10.0))) / 100.0
    pullback_pct = float(params.get("pullback_pct", 4.0)) / 100.0
    if len(df) < horizon + entry_window + 90:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_entry_timing_meta(target_pct, stop_pct, horizon, entry_window, pullback_pct)
    features, _, latest = _feature_label_frame(
        daily_bars,
        params={**params, "horizon_bars": horizon, "target_pct": target_pct * 100.0},
        risk_params=risk_params,
        sequence=True,
    )
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    rows = []
    labels = []
    buy_now_wins = 0
    wait_wins = 0
    wait_fills = 0
    for idx in features.index:
        entry_now = float(close.iloc[idx])
        if entry_now <= 0:
            continue
        future_entry = df.iloc[idx + 1 : idx + 1 + entry_window]
        future_path = df.iloc[idx + 1 : min(idx + 1 + horizon, len(df))]
        if future_entry.empty or future_path.empty:
            continue
        wait_price = entry_now * (1.0 - pullback_pct)
        wait_hit_positions = [j for j in range(idx + 1, min(idx + 1 + entry_window, len(df))) if float(low.iloc[j]) <= wait_price]
        wait_idx = wait_hit_positions[0] if wait_hit_positions else None

        now_target_day, now_stop_day = _first_event_days(high, low, idx + 1, min(idx + 1 + horizon, len(df)), entry_now, target_pct, stop_pct)
        now_success = now_target_day is not None and (now_stop_day is None or now_target_day <= now_stop_day)
        wait_success = False
        if wait_idx is not None:
            wait_fills += 1
            wait_target_day, wait_stop_day = _first_event_days(high, low, wait_idx + 1, min(wait_idx + 1 + horizon, len(df)), wait_price, target_pct, stop_pct)
            wait_success = wait_target_day is not None and (wait_stop_day is None or wait_target_day <= wait_stop_day)
        label = int(now_success and (wait_idx is None or not wait_success or entry_now <= wait_price * (1.0 + 0.012)))
        labels.append(label)
        rows.append(idx)
        buy_now_wins += int(now_success)
        wait_wins += int(wait_success)
    X = features.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0) if rows else pd.DataFrame()
    y = pd.Series(labels, index=X.index, dtype=int)
    sample_count = len(labels)
    meta = {
        "entry_window_bars": entry_window,
        "horizon_bars": horizon,
        "target_pct": round(target_pct * 100.0, 4),
        "stop_pct": round(stop_pct * 100.0, 4),
        "pullback_pct": round(pullback_pct * 100.0, 4),
        "historical_buy_now_rate": float(buy_now_wins / max(sample_count, 1)),
        "historical_wait_fill_rate": float(wait_fills / max(sample_count, 1)),
        "historical_wait_success_rate": float(wait_wins / max(wait_fills, 1)) if wait_fills else 0.0,
    }
    return X, y, latest, meta


def _entry_price_surface_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, dict[str, Any]]:
    df = _prepare(daily_bars)
    entry_window = int(params.get("entry_window_bars", 5))
    horizon = int(params.get("horizon_bars", 25))
    target_pct = float(params.get("target_pct", risk_params.get("right_tail_target_pct", 25.0))) / 100.0
    stop_pct = float(params.get("stop_pct", risk_params.get("max_stop_distance_pct", 10.0))) / 100.0
    pullback_pct = float(params.get("pullback_pct", 4.0)) / 100.0
    breakout_buffer_pct = float(params.get("breakout_buffer_pct", 1.2)) / 100.0
    fill_miss_penalty = float(params.get("fill_miss_penalty", 0.18))
    delay_penalty_per_bar = float(params.get("delay_penalty_per_bar", 0.012))
    stop_penalty_mult = float(params.get("stop_penalty_mult", 1.15))
    route_names = {0: "avoid", 1: "buy_now", 2: "wait_pullback", 3: "breakout_confirm"}
    if len(df) < horizon + entry_window + 90:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_entry_surface_meta(
            target_pct, stop_pct, horizon, entry_window, pullback_pct, breakout_buffer_pct, route_names
        )
    features, _, latest = _feature_label_frame(
        daily_bars,
        params={**params, "horizon_bars": horizon, "target_pct": target_pct * 100.0},
        risk_params=risk_params,
        sequence=True,
    )
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    rolling_high20 = high.rolling(20, min_periods=10).max()
    rolling_low20 = low.rolling(20, min_periods=10).min()
    rolling_high60 = high.rolling(60, min_periods=20).max()
    enriched = features.copy()
    enriched["distance_to_20d_high"] = (rolling_high20 / close - 1.0).reindex(enriched.index).fillna(0.0)
    enriched["distance_to_20d_low"] = (close / rolling_low20 - 1.0).reindex(enriched.index).fillna(0.0)
    enriched["breakout_buffer_distance"] = (rolling_high20 * (1.0 + breakout_buffer_pct) / close - 1.0).reindex(enriched.index).fillna(0.0)
    enriched["range_extension_60"] = (close / rolling_high60 - 1.0).reindex(enriched.index).fillna(0.0)
    latest = enriched.tail(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    rows = []
    labels = []
    route_counts = {name: 0 for name in route_names.values()}
    utility_sums = {name: 0.0 for name in route_names.values()}
    for idx in enriched.index:
        entry_now = float(close.iloc[idx])
        if entry_now <= 0:
            continue
        end_idx = min(idx + 1 + horizon, len(df))
        entry_end_idx = min(idx + 1 + entry_window, len(df))
        if idx + 1 >= end_idx or idx + 1 >= entry_end_idx:
            continue
        pullback_price = entry_now * (1.0 - pullback_pct)
        breakout_price = max(float(rolling_high20.iloc[idx]) if pd.notna(rolling_high20.iloc[idx]) else entry_now, entry_now) * (1.0 + breakout_buffer_pct)
        route_specs = {
            "buy_now": (idx, entry_now, 0, True),
            "wait_pullback": _future_fill_spec(low, idx + 1, entry_end_idx, pullback_price),
            "breakout_confirm": _future_fill_spec(high, idx + 1, entry_end_idx, breakout_price),
        }
        utilities = {
            "avoid": 0.0,
            "buy_now": _entry_route_utility(high, low, route_specs["buy_now"], end_idx, target_pct, stop_pct, fill_miss_penalty, delay_penalty_per_bar, stop_penalty_mult),
            "wait_pullback": _entry_route_utility(high, low, route_specs["wait_pullback"], end_idx, target_pct, stop_pct, fill_miss_penalty, delay_penalty_per_bar, stop_penalty_mult),
            "breakout_confirm": _entry_route_utility(high, low, route_specs["breakout_confirm"], end_idx, target_pct, stop_pct, fill_miss_penalty, delay_penalty_per_bar, stop_penalty_mult),
        }
        best_name = max(utilities, key=utilities.get)
        if utilities[best_name] <= 0.0:
            best_name = "avoid"
        label = next(code for code, name in route_names.items() if name == best_name)
        rows.append(idx)
        labels.append(label)
        route_counts[best_name] += 1
        for name, value in utilities.items():
            utility_sums[name] += float(value)
    X = enriched.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0) if rows else pd.DataFrame()
    y = pd.Series(labels, index=X.index, dtype=int)
    last_close = float(close.iloc[-1]) if len(close) else 0.0
    last_high20 = float(rolling_high20.iloc[-1]) if len(rolling_high20) and pd.notna(rolling_high20.iloc[-1]) else last_close
    sample_count = max(len(labels), 1)
    meta = {
        **_empty_entry_surface_meta(target_pct, stop_pct, horizon, entry_window, pullback_pct, breakout_buffer_pct, route_names),
        "route_base_rates": {name: round(count / sample_count, 6) for name, count in route_counts.items()},
        "route_avg_utilities": {name: round(value / sample_count, 6) for name, value in utility_sums.items()},
        "suggested_prices": {
            "buy_now": round(last_close, 4),
            "wait_pullback": round(last_close * (1.0 - pullback_pct), 4),
            "breakout_confirm": round(max(last_high20, last_close) * (1.0 + breakout_buffer_pct), 4),
        },
    }
    return X, y, latest, meta


def _future_fill_spec(series: pd.Series, start_idx: int, end_idx: int, price: float) -> tuple[int, float, int, bool]:
    for delay, j in enumerate(range(start_idx, end_idx), start=1):
        value = float(series.iloc[j])
        if (series.name == "low" and value <= price) or (series.name == "high" and value >= price):
            return j, price, delay, True
    return start_idx, price, max(end_idx - start_idx, 0), False


def _entry_route_utility(
    high: pd.Series,
    low: pd.Series,
    spec: tuple[int, float, int, bool],
    end_idx: int,
    target_pct: float,
    stop_pct: float,
    fill_miss_penalty: float,
    delay_penalty_per_bar: float,
    stop_penalty_mult: float,
) -> float:
    fill_idx, entry, delay, filled = spec
    if not filled or entry <= 0:
        return -fill_miss_penalty
    target_day, stop_day = _first_event_days(high, low, fill_idx + 1, end_idx, entry, target_pct, stop_pct)
    if target_day is not None and (stop_day is None or target_day <= stop_day):
        return target_pct - delay * delay_penalty_per_bar
    if stop_day is not None:
        return -stop_pct * stop_penalty_mult - delay * delay_penalty_per_bar
    max_up = float(high.iloc[fill_idx + 1 : end_idx].max() / entry - 1.0) if fill_idx + 1 < end_idx else 0.0
    max_down = float(1.0 - low.iloc[fill_idx + 1 : end_idx].min() / entry) if fill_idx + 1 < end_idx else 0.0
    return 0.35 * max_up - 0.55 * max_down - delay * delay_penalty_per_bar


def _first_event_days(
    high: pd.Series,
    low: pd.Series,
    start_idx: int,
    end_idx: int,
    entry: float,
    target_pct: float,
    stop_pct: float,
) -> tuple[int | None, int | None]:
    target = entry * (1.0 + target_pct)
    stop = entry * (1.0 - stop_pct)
    hit_target_day = None
    hit_stop_day = None
    for day, j in enumerate(range(start_idx, end_idx), start=1):
        if hit_target_day is None and float(high.iloc[j]) >= target:
            hit_target_day = day
        if hit_stop_day is None and float(low.iloc[j]) <= stop:
            hit_stop_day = day
        if hit_target_day is not None or hit_stop_day is not None:
            break
    return hit_target_day, hit_stop_day


def _target_stop_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, dict[str, Any]]:
    df = _prepare(daily_bars)
    horizon = int(params.get("horizon_bars", 40))
    target_pct = float(params.get("target_pct", risk_params.get("right_tail_target_pct", 30.0))) / 100.0
    stop_pct = float(params.get("stop_pct", risk_params.get("max_stop_distance_pct", 10.0))) / 100.0
    if len(df) < horizon + 90:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_target_stop_meta(target_pct, stop_pct, horizon)
    base_features, _, latest = _feature_label_frame(
        daily_bars,
        params={**params, "horizon_bars": horizon, "target_pct": target_pct * 100.0},
        risk_params=risk_params,
        sequence=True,
    )
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    rows = []
    labels = []
    days_to_target: list[int] = []
    days_to_stop: list[int] = []
    neither = 0
    for idx in base_features.index:
        entry = float(close.iloc[idx])
        if entry <= 0:
            continue
        target = entry * (1.0 + target_pct)
        stop = entry * (1.0 - stop_pct)
        hit_target_day = None
        hit_stop_day = None
        for day, j in enumerate(range(idx + 1, min(idx + 1 + horizon, len(df))), start=1):
            if hit_target_day is None and float(high.iloc[j]) >= target:
                hit_target_day = day
            if hit_stop_day is None and float(low.iloc[j]) <= stop:
                hit_stop_day = day
            if hit_target_day is not None or hit_stop_day is not None:
                break
        if hit_target_day is None and hit_stop_day is None:
            neither += 1
            continue
        rows.append(idx)
        target_first = hit_target_day is not None and (hit_stop_day is None or hit_target_day <= hit_stop_day)
        labels.append(int(target_first))
        if hit_target_day is not None:
            days_to_target.append(hit_target_day)
        if hit_stop_day is not None:
            days_to_stop.append(hit_stop_day)
    X = base_features.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0) if rows else pd.DataFrame()
    y = pd.Series(labels, index=X.index, dtype=int)
    total_paths = len(labels) + neither
    meta = {
        "target_first_rate": float(sum(labels) / max(len(labels), 1)) if labels else 0.0,
        "stop_first_rate": float((len(labels) - sum(labels)) / max(len(labels), 1)) if labels else 0.0,
        "neither_rate": float(neither / max(total_paths, 1)),
        "avg_days_to_target": float(np.mean(days_to_target)) if days_to_target else None,
        "avg_days_to_stop": float(np.mean(days_to_stop)) if days_to_stop else None,
        "target_pct": round(target_pct * 100.0, 4),
        "stop_pct": round(stop_pct * 100.0, 4),
        "horizon_bars": horizon,
    }
    return X, y, latest, meta


def _stop_loss_hazard_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, dict[str, Any]]:
    df = _prepare(daily_bars)
    horizon = int(params.get("horizon_bars", 20))
    target_pct = float(params.get("target_pct", 20.0)) / 100.0
    stop_pct = float(params.get("stop_pct", risk_params.get("max_stop_distance_pct", 10.0))) / 100.0
    if len(df) < horizon + 90:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_stop_hazard_meta(target_pct, stop_pct, horizon)
    features, _, latest = _feature_label_frame(
        daily_bars,
        params={**params, "horizon_bars": horizon, "target_pct": target_pct * 100.0},
        risk_params=risk_params,
        sequence=True,
    )
    if features.empty:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_stop_hazard_meta(target_pct, stop_pct, horizon)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    rolling_high20 = high.rolling(20, min_periods=10).max()
    rolling_low20 = low.rolling(20, min_periods=10).min()
    enriched = features.copy()
    enriched["drawdown_from_20d_high"] = (close / rolling_high20 - 1.0).reindex(enriched.index).fillna(0.0)
    enriched["support_buffer_20d"] = (close / rolling_low20 - 1.0).reindex(enriched.index).fillna(0.0)
    enriched["stop_to_atr14"] = (stop_pct / features.get("atr14_pct", pd.Series([0.04] * len(features), index=features.index)).replace(0, np.nan)).fillna(1.0)
    rows = []
    labels = []
    stop_days: list[int] = []
    target_first = 0
    for idx in enriched.index:
        entry = float(close.iloc[idx])
        if entry <= 0:
            continue
        target_day, stop_day = _first_event_days(high, low, idx + 1, min(idx + 1 + horizon, len(df)), entry, target_pct, stop_pct)
        stop_first = stop_day is not None and (target_day is None or stop_day < target_day)
        rows.append(idx)
        labels.append(int(stop_first))
        if stop_day is not None:
            stop_days.append(stop_day)
        target_first += int(target_day is not None and (stop_day is None or target_day <= stop_day))
    X = enriched.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0) if rows else pd.DataFrame()
    y = pd.Series(labels, index=X.index, dtype=int)
    meta = {
        **_empty_stop_hazard_meta(target_pct, stop_pct, horizon),
        "historical_target_first_rate": float(target_first / max(len(labels), 1)) if labels else 0.0,
        "avg_days_to_stop": float(np.mean(stop_days)) if stop_days else None,
        "median_days_to_stop": float(np.median(stop_days)) if stop_days else None,
    }
    latest = enriched.tail(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return X, y, latest, meta


def _gap_risk_open_frame(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, dict[str, Any]]:
    df = _prepare(daily_bars)
    threshold_pct = float(params.get("adverse_gap_threshold_pct", risk_params.get("open_gap_risk_pct", 3.0)))
    if len(df) < 110:
        return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame(), _empty_gap_open_meta(threshold_pct)
    close = df["close"].astype(float)
    open_ = df["open"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    amount = df.get("amount", pd.Series([0.0] * len(df), index=df.index)).astype(float)
    returns = close.pct_change().fillna(0.0)
    gap = (open_ / close.shift(1).replace(0, np.nan) - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    intraday = (close / open_.replace(0, np.nan) - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    tr = pd.concat([(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    feat = pd.DataFrame(index=df.index)
    for w in [3, 5, 10, 20, 40]:
        feat[f"ret_{w}"] = close.pct_change(w).fillna(0.0)
        feat[f"gap_abs_mean_{w}"] = gap.abs().rolling(w, min_periods=max(2, w // 3)).mean().fillna(0.0)
        feat[f"vol_{w}"] = returns.rolling(w, min_periods=max(2, w // 3)).std().fillna(0.0)
    feat["latest_gap"] = gap.fillna(0.0)
    feat["latest_intraday_ret"] = intraday.fillna(0.0)
    feat["atr14_pct"] = (tr.rolling(14, min_periods=5).mean() / close.replace(0, np.nan)).fillna(0.04)
    feat["amount_ratio_5_20"] = (
        amount.rolling(5, min_periods=2).mean() / amount.rolling(20, min_periods=5).mean().replace(0, np.nan)
    ).fillna(1.0)
    rows = []
    labels = []
    adverse_gaps: list[float] = []
    threshold = -abs(threshold_pct) / 100.0
    for idx in range(60, len(df) - 1):
        next_gap = float(gap.iloc[idx + 1])
        rows.append(idx)
        labels.append(int(next_gap <= threshold))
        if next_gap <= threshold:
            adverse_gaps.append(next_gap)
    X = feat.loc[rows].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = pd.Series(labels, index=X.index, dtype=int)
    latest = feat.tail(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    meta = {
        **_empty_gap_open_meta(threshold_pct),
        "historical_adverse_gap_rate": float(sum(labels) / max(len(labels), 1)) if labels else 0.0,
        "avg_adverse_gap_pct": float(np.mean(adverse_gaps) * 100.0) if adverse_gaps else None,
        "latest_gap_pct": round(float(gap.iloc[-1]) * 100.0, 4) if len(gap) else 0.0,
        "latest_intraday_ret_pct": round(float(intraday.iloc[-1]) * 100.0, 4) if len(intraday) else 0.0,
    }
    return X, y, latest, meta


def _empty_target_stop_meta(target_pct: float, stop_pct: float, horizon: int) -> dict[str, Any]:
    return {
        "target_first_rate": 0.0,
        "stop_first_rate": 0.0,
        "neither_rate": 1.0,
        "avg_days_to_target": None,
        "avg_days_to_stop": None,
        "target_pct": round(target_pct * 100.0, 4),
        "stop_pct": round(stop_pct * 100.0, 4),
        "horizon_bars": horizon,
    }


def _empty_gap_open_meta(threshold_pct: float) -> dict[str, Any]:
    return {
        "adverse_gap_threshold_pct": round(float(threshold_pct), 4),
        "historical_adverse_gap_rate": 0.0,
        "avg_adverse_gap_pct": None,
        "latest_gap_pct": 0.0,
        "latest_intraday_ret_pct": 0.0,
    }


def _empty_stop_hazard_meta(target_pct: float, stop_pct: float, horizon: int) -> dict[str, Any]:
    return {
        "horizon_bars": horizon,
        "target_pct": round(target_pct * 100.0, 4),
        "stop_pct": round(stop_pct * 100.0, 4),
        "historical_target_first_rate": 0.0,
        "avg_days_to_stop": None,
        "median_days_to_stop": None,
    }


def _empty_entry_timing_meta(target_pct: float, stop_pct: float, horizon: int, entry_window: int, pullback_pct: float) -> dict[str, Any]:
    return {
        "entry_window_bars": entry_window,
        "horizon_bars": horizon,
        "target_pct": round(target_pct * 100.0, 4),
        "stop_pct": round(stop_pct * 100.0, 4),
        "pullback_pct": round(pullback_pct * 100.0, 4),
        "historical_buy_now_rate": 0.0,
        "historical_wait_fill_rate": 0.0,
        "historical_wait_success_rate": 0.0,
    }


def _empty_ladder_meta(target_pct: float, stop_pct: float, horizon: int) -> dict[str, Any]:
    return {
        "horizon_bars": horizon,
        "target_pct": round(target_pct * 100.0, 4),
        "stop_pct": round(stop_pct * 100.0, 4),
        "avg_days_to_target": None,
        "median_days_to_target": None,
        "stop_before_target_rate": 0.0,
    }


def _empty_path_shape_meta(target_pct: float, stop_pct: float, horizon: int) -> dict[str, Any]:
    return {
        "horizon_bars": horizon,
        "target_pct": round(target_pct * 100.0, 4),
        "stop_pct": round(stop_pct * 100.0, 4),
        "forward_returns": np.array([], dtype=float),
        "stop_first": np.array([], dtype=float),
    }


def _empty_mfe_mae_meta(target_pct: float, stop_pct: float, horizon: int) -> dict[str, Any]:
    return {
        "horizon_bars": horizon,
        "target_pct": round(target_pct * 100.0, 4),
        "stop_pct": round(stop_pct * 100.0, 4),
    }


def _empty_entry_surface_meta(
    target_pct: float,
    stop_pct: float,
    horizon: int,
    entry_window: int,
    pullback_pct: float,
    breakout_buffer_pct: float,
    route_names: dict[int, str],
) -> dict[str, Any]:
    return {
        "entry_window_bars": entry_window,
        "horizon_bars": horizon,
        "target_pct": round(target_pct * 100.0, 4),
        "stop_pct": round(stop_pct * 100.0, 4),
        "pullback_pct": round(pullback_pct * 100.0, 4),
        "breakout_buffer_pct": round(breakout_buffer_pct * 100.0, 4),
        "route_names": route_names,
        "route_base_rates": {name: 0.0 for name in route_names.values()},
        "route_avg_utilities": {name: 0.0 for name in route_names.values()},
        "suggested_prices": {},
    }


def _clip01(value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    if not np.isfinite(v):
        v = 0.0
    return float(max(0.0, min(1.0, v)))


def _positive_probability(model: Any, frame: pd.DataFrame) -> float:
    probs = _positive_probabilities(model, frame)
    return _clip01(probs[0]) if len(probs) else 0.0


def _positive_probabilities(model: Any, frame: pd.DataFrame) -> np.ndarray:
    if frame is None or len(frame) == 0:
        return np.array([])
    raw = np.asarray(model.predict_proba(frame), dtype=float)
    if raw.ndim == 1:
        return np.asarray([_clip01(value) for value in raw], dtype=float)
    if raw.shape[1] == 0:
        return np.zeros(raw.shape[0], dtype=float)
    classes = list(getattr(model, "classes_", []))
    if 1 in classes:
        col = classes.index(1)
        return np.asarray([_clip01(value) for value in raw[:, col]], dtype=float)
    if len(classes) == 1:
        fill = 1.0 if classes[0] == 1 else 0.0
        return np.full(raw.shape[0], fill, dtype=float)
    col = min(1, raw.shape[1] - 1)
    return np.asarray([_clip01(value) for value in raw[:, col]], dtype=float)


def _prepare(daily_bars: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "open", "high", "low", "close"}
    if daily_bars.empty or not required.issubset(daily_bars.columns):
        return pd.DataFrame()
    df = daily_bars.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    for col in ["open", "high", "low", "close", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["trade_date", "open", "high", "low", "close"]).sort_values("trade_date").reset_index(drop=True)


def _auc_proxy(y_true: np.ndarray, y_score: np.ndarray) -> float | None:
    pos = y_score[y_true == 1]
    neg = y_score[y_true == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    wins = sum(float(p > n) + 0.5 * float(p == n) for p in pos for n in neg)
    return float(wins / (len(pos) * len(neg)))


def _missing(reason: str, model_name: str) -> MetaModelProfile:
    return MetaModelProfile(
        available=False,
        reason=reason,
        model_name=model_name,
        sample_count=0,
        positive_rate=None,
        probability=None,
        oos_hit_rate=None,
        oos_auc_proxy=None,
        feature_snapshot={},
    )
