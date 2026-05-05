"""Prediction surface synthesis for Stock Edge.

This layer converts the strategy matrix into executable probabilities and
return quantiles. It is YAML-driven so offline calibration, global presets, and
pre-report overlays can tune the numeric surface without changing report code.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PredictionSurface:
    prob_hit_20_40d: float
    prob_hit_30_40d: float
    prob_hit_50_40d: float
    prob_stop_first: float
    entry_fill_probability: float
    expected_return_40d: float
    expected_drawdown_40d: float
    return_p10_40d: float
    return_p50_40d: float
    return_p90_40d: float
    opportunities: list[dict[str, Any]]
    best_opportunity: dict[str, Any] | None
    model_version: str
    calibrated: bool
    drivers: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_prediction_surface(
    *,
    strategy_matrix: dict[str, Any],
    params: dict[str, Any],
    close: float,
    entry_high: float | None,
    stop_price: float | None,
    atr14: float | None = None,
) -> PredictionSurface:
    """Build a continuous prediction surface from current structured evidence."""
    cfg = params.get("prediction_surface", {})
    risk = params.get("risk", {})
    score = _float(strategy_matrix.get("aggregate_score"), 0.5)
    edge = (score - 0.5) * 2.0
    clusters = _cluster_scores(strategy_matrix)
    risk_danger = max(0.0, 0.5 - clusters.get("risk_warning", 0.5)) * 2.0
    trend = _centered(clusters.get("trend_breakout", 0.5))
    pullback = _centered(clusters.get("pullback_continuation", 0.5))
    order_flow = _centered(clusters.get("order_flow_smart_money", 0.5))
    sector = _centered(clusters.get("sw_l2_sector_leadership", 0.5))
    model = _centered(clusters.get("model_ensemble", 0.5))
    fundamental = _centered(clusters.get("fundamentals_quality", 0.5))
    volatility = _volatility_proxy(close=close, atr14=atr14)

    hit50_cfg = cfg.get("hit_50", {})
    logit50 = (
        _p(hit50_cfg, "intercept", -2.85)
        + _p(hit50_cfg, "edge_coef", 2.10) * edge
        + _p(hit50_cfg, "trend_coef", 0.45) * trend
        + _p(hit50_cfg, "pullback_coef", 0.30) * pullback
        + _p(hit50_cfg, "order_flow_coef", 0.42) * order_flow
        + _p(hit50_cfg, "sector_coef", 0.38) * sector
        + _p(hit50_cfg, "model_coef", 0.45) * model
        + _p(hit50_cfg, "fundamental_coef", 0.18) * fundamental
        - _p(hit50_cfg, "risk_penalty_coef", 1.25) * risk_danger
    )
    floor = _p(cfg, "probability_floor", 0.01)
    ceiling = _p(cfg, "probability_ceiling", 0.62)
    prob50 = _clip(_sigmoid(logit50), floor, ceiling)
    hit30_cfg = cfg.get("hit_30", {})
    prob30 = _clip(prob50 + _p(hit30_cfg, "lift_from_hit_50", 0.11) + max(edge, 0.0) * _p(hit30_cfg, "edge_lift", 0.08), floor, 0.86)
    hit20_cfg = cfg.get("hit_20", {})
    prob20 = _clip(prob30 + _p(hit20_cfg, "lift_from_hit_30", 0.13) + max(edge, 0.0) * _p(hit20_cfg, "edge_lift", 0.10), floor, 0.94)
    replay = _replay_probability_adjustment(strategy_matrix)
    prob20 = _blend_replay_probability(prob20, replay.get("tactical_15d_20"))
    prob30 = _blend_replay_probability(prob30, replay.get("swing_25d_30"))
    prob50 = _blend_replay_probability(prob50, replay.get("right_tail_40d_50"))
    path_replay = _target_stop_replay_adjustment(strategy_matrix)
    prob20 = _blend_replay_probability(prob20, path_replay.get("tactical_15d_20"))
    prob30 = _blend_replay_probability(prob30, path_replay.get("swing_25d_30"))
    prob50 = _blend_replay_probability(prob50, path_replay.get("right_tail_40d_50"))

    stop_cfg = cfg.get("stop_first", {})
    prob_stop = _clip(
        _sigmoid(
            _p(stop_cfg, "intercept", -0.65)
            + _p(stop_cfg, "edge_coef", -1.15) * edge
            + _p(stop_cfg, "risk_coef", 1.35) * risk_danger
            + _p(stop_cfg, "volatility_coef", 0.25) * volatility
        ),
        0.03,
        0.88,
    )
    stop_path = _target_stop_stop_adjustment(path_replay)
    if stop_path:
        alpha = _clip(stop_path.get("alpha", 0.0), 0.0, 0.40)
        prob_stop = _clip((1.0 - alpha) * prob_stop + alpha * stop_path.get("stop_probability", prob_stop), 0.03, 0.88)
    fill_cfg = cfg.get("entry_fill", {})
    distance_pct = _entry_distance_pct(close=close, entry_high=entry_high)
    entry_fill = _clip(
        _sigmoid(
            _p(fill_cfg, "intercept", 0.35)
            + _p(fill_cfg, "edge_coef", 0.55) * edge
            + _p(fill_cfg, "distance_coef", -0.08) * distance_pct
            + _p(fill_cfg, "pullback_coef", 0.35) * pullback
        ),
        0.05,
        0.95,
    )
    entry_fill_replay = _entry_fill_replay_adjustment(strategy_matrix)
    if entry_fill_replay:
        alpha = _clip(entry_fill_replay.get("alpha", 0.0), 0.0, 0.45)
        replay_fill = _clip(entry_fill_replay.get("clean_fill_rate", entry_fill), 0.0, 0.95)
        entry_fill = _clip((1.0 - alpha) * entry_fill + alpha * replay_fill, 0.0, 0.95)

    q_cfg = cfg.get("return_quantiles", {})
    stop_distance = _stop_distance_pct(close=close, stop_price=stop_price, risk=risk)
    p10 = _clip(_p(q_cfg, "p10_floor", -0.16) + _p(q_cfg, "p10_stop_multiplier", -0.75) * stop_distance, -0.35, 0.08)
    p50 = _clip(_p(q_cfg, "p50_base", 0.04) + _p(q_cfg, "p50_edge_coef", 0.22) * edge - 0.08 * risk_danger, -0.12, 0.38)
    p90 = _clip(
        _p(q_cfg, "p90_base", 0.18)
        + _p(q_cfg, "p90_edge_coef", 0.45) * max(edge, -0.2)
        + _p(q_cfg, "p90_hit50_coef", 0.30) * prob50,
        0.02,
        0.95,
    )
    exp_cfg = cfg.get("expected_return", {})
    expected_return = _clip(
        _p(exp_cfg, "base", -0.02)
        + _p(exp_cfg, "hit20_weight", 0.10) * prob20
        + _p(exp_cfg, "hit30_weight", 0.10) * prob30
        + _p(exp_cfg, "hit50_weight", 0.22) * prob50
        - _p(exp_cfg, "stop_penalty", 0.12) * prob_stop,
        -0.18,
        0.55,
    )
    dd_cfg = cfg.get("expected_drawdown", {})
    expected_drawdown = _clip(
        _p(dd_cfg, "base", 0.16)
        + _p(dd_cfg, "edge_coef", -0.06) * edge
        + _p(dd_cfg, "risk_coef", 0.06) * risk_danger,
        _p(dd_cfg, "stop_floor", 0.04),
        0.35,
    )

    opportunities = _build_opportunities(
        cfg=cfg,
        close=close,
        entry_high=entry_high,
        stop_price=stop_price,
        edge=edge,
        prob20=prob20,
        prob30=prob30,
        prob50=prob50,
        prob_stop=prob_stop,
        stop_distance=stop_distance,
        path_replay=path_replay,
    )
    best_opportunity = _best_opportunity(opportunities)

    return PredictionSurface(
        prob_hit_20_40d=round(prob20, 4),
        prob_hit_30_40d=round(prob30, 4),
        prob_hit_50_40d=round(prob50, 4),
        prob_stop_first=round(prob_stop, 4),
        entry_fill_probability=round(entry_fill, 4),
        expected_return_40d=round(expected_return, 4),
        expected_drawdown_40d=round(expected_drawdown, 4),
        return_p10_40d=round(p10, 4),
        return_p50_40d=round(p50, 4),
        return_p90_40d=round(p90, 4),
        opportunities=opportunities,
        best_opportunity=best_opportunity,
        model_version=str(cfg.get("model_version", "prediction_surface_v1")),
        calibrated=bool(cfg.get("calibrated", False)),
        drivers={
            "aggregate_score": round(score, 4),
            "edge": round(edge, 4),
            "trend": round(trend, 4),
            "pullback": round(pullback, 4),
            "order_flow": round(order_flow, 4),
            "sector": round(sector, 4),
            "model": round(model, 4),
            "fundamental": round(fundamental, 4),
            "risk_danger": round(risk_danger, 4),
            "volatility": round(volatility, 4),
            "entry_distance_pct": round(distance_pct, 4),
            "historical_replay_alpha": round(max((row or {}).get("alpha", 0.0) for row in replay.values()), 4) if replay else 0.0,
            "target_stop_replay_alpha": round(max((row or {}).get("alpha", 0.0) for row in path_replay.values()), 4) if path_replay else 0.0,
            "entry_fill_replay_alpha": round(entry_fill_replay.get("alpha", 0.0), 4) if entry_fill_replay else 0.0,
        },
    )


def _build_opportunities(
    *,
    cfg: dict[str, Any],
    close: float,
    entry_high: float | None,
    stop_price: float | None,
    edge: float,
    prob20: float,
    prob30: float,
    prob50: float,
    prob_stop: float,
    stop_distance: float,
    path_replay: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    base_probs = {"hit_20": prob20, "hit_30": prob30, "hit_50": prob50}
    entry = entry_high if entry_high and entry_high > 0 else close
    rows: list[dict[str, Any]] = []
    for key, spec in (cfg.get("opportunities") or {}).items():
        if not isinstance(spec, dict):
            continue
        return_pct = _p(spec, "return_pct", 20.0)
        horizon_days = int(_p(spec, "horizon_days", 20.0))
        base = base_probs.get(str(spec.get("probability_base") or "hit_20"), prob20)
        prob = _clip(
            base * _p(spec, "horizon_multiplier", 1.0) + max(edge, 0.0) * _p(spec, "edge_multiplier", 0.0),
            0.0,
            0.95,
        )
        target_price = entry * (1.0 + return_pct / 100.0) if entry > 0 else None
        expected_value = prob * (return_pct / 100.0) - prob_stop * stop_distance
        min_prob = _p(spec, "min_probability", 0.10)
        rows.append(
            {
                "key": str(key),
                "label": str(spec.get("label") or key),
                "horizon_days": horizon_days,
                "return_pct": round(return_pct, 2),
                "probability": round(prob, 4),
                "target_price": round(target_price, 4) if target_price else None,
                "expected_value": round(expected_value, 4),
                "meets_probability_floor": prob >= min_prob,
                "min_probability": min_prob,
                "stop_price": round(stop_price, 4) if stop_price else None,
                **_opportunity_path_fields(path_replay.get(str(key))),
            }
        )
    rows.sort(key=lambda row: (row["meets_probability_floor"], row["expected_value"], row["probability"]), reverse=True)
    return rows


def _best_opportunity(opportunities: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in opportunities:
        if row.get("meets_probability_floor") and row.get("expected_value", -1.0) > 0:
            return row
    return opportunities[0] if opportunities else None


def _cluster_scores(strategy_matrix: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in strategy_matrix.get("cluster_plans") or []:
        cluster = str(row.get("cluster"))
        out[cluster] = _float(row.get("display_score"), 0.5)
    return out


def _replay_probability_adjustment(strategy_matrix: dict[str, Any]) -> dict[str, dict[str, float]]:
    for signal in strategy_matrix.get("signals") or []:
        if signal.get("key") != "historical_replay_edge":
            continue
        extra = signal.get("extra") or {}
        out: dict[str, dict[str, float]] = {}
        for row in extra.get("target_stats") or []:
            sample_count = float(row.get("sample_count") or extra.get("analog_count") or 0.0)
            alpha = _clip(sample_count / (sample_count + 32.0), 0.0, 0.45)
            out[str(row.get("key"))] = {
                "probability": _clip(_float(row.get("hit_rate"), 0.0), 0.0, 0.95),
                "alpha": alpha,
            }
        return out
    return {}


def _blend_replay_probability(model_prob: float, replay: dict[str, float] | None) -> float:
    if not replay:
        return model_prob
    alpha = _clip(_float(replay.get("alpha"), 0.0), 0.0, 0.45)
    probability = _clip(_float(replay.get("probability"), model_prob), 0.0, 0.95)
    return _clip((1.0 - alpha) * model_prob + alpha * probability, 0.0, 0.95)


def _entry_fill_replay_adjustment(strategy_matrix: dict[str, Any]) -> dict[str, float]:
    for signal in strategy_matrix.get("signals") or []:
        if signal.get("key") != "entry_fill_replay":
            continue
        extra = signal.get("extra") or {}
        sample_count = float(extra.get("sample_count") or 0.0)
        clean_fill = _float(extra.get("clean_fill_rate"), _float(extra.get("fill_rate"), 0.0))
        stop_rate = _float(extra.get("stop_before_fill_rate"), 0.0)
        alpha = _clip(sample_count / (sample_count + 48.0), 0.0, 0.40)
        return {
            "clean_fill_rate": _clip(clean_fill * (1.0 - 0.35 * stop_rate), 0.0, 0.95),
            "alpha": alpha,
        }
    return {}


def _target_stop_replay_adjustment(strategy_matrix: dict[str, Any]) -> dict[str, dict[str, float]]:
    for signal in strategy_matrix.get("signals") or []:
        if signal.get("key") != "target_stop_replay":
            continue
        extra = signal.get("extra") or {}
        out: dict[str, dict[str, float]] = {}
        for row in extra.get("target_stats") or []:
            sample_count = float(row.get("sample_count") or extra.get("sample_count") or 0.0)
            alpha = _clip(sample_count / (sample_count + 64.0), 0.0, 0.35)
            out[str(row.get("key"))] = {
                "probability": _clip(_float(row.get("target_first_rate"), 0.0), 0.0, 0.95),
                "stop_probability": _clip(_float(row.get("stop_first_rate"), 0.0), 0.0, 0.95),
                "avg_days_to_target": _float(row.get("avg_days_to_target"), -1.0),
                "avg_days_to_stop": _float(row.get("avg_days_to_stop"), -1.0),
                "alpha": alpha,
            }
        return out
    return {}


def _target_stop_stop_adjustment(path_replay: dict[str, dict[str, float]]) -> dict[str, float]:
    if not path_replay:
        return {}
    weights = [max(0.0, _float(row.get("alpha"), 0.0)) for row in path_replay.values()]
    total = sum(weights)
    if total <= 0:
        return {}
    rows = list(path_replay.values())
    stop_probability = sum(_float(row.get("stop_probability"), 0.0) * weight for row, weight in zip(rows, weights, strict=False)) / total
    return {"stop_probability": _clip(stop_probability, 0.0, 0.95), "alpha": _clip(max(weights), 0.0, 0.40)}


def _opportunity_path_fields(row: dict[str, float] | None) -> dict[str, Any]:
    if not row:
        return {}
    days_to_target = _float(row.get("avg_days_to_target"), -1.0)
    days_to_stop = _float(row.get("avg_days_to_stop"), -1.0)
    return {
        "target_first_probability": round(_clip(_float(row.get("probability"), 0.0), 0.0, 0.95), 4),
        "stop_first_probability": round(_clip(_float(row.get("stop_probability"), 0.0), 0.0, 0.95), 4),
        "avg_days_to_target": round(days_to_target, 2) if days_to_target >= 0 else None,
        "avg_days_to_stop": round(days_to_stop, 2) if days_to_stop >= 0 else None,
    }


def _centered(value: float) -> float:
    return (value - 0.5) * 2.0


def _entry_distance_pct(*, close: float, entry_high: float | None) -> float:
    if close <= 0 or entry_high is None or entry_high <= 0:
        return 0.0
    return abs(entry_high / close - 1.0) * 100.0


def _stop_distance_pct(*, close: float, stop_price: float | None, risk: dict[str, Any]) -> float:
    if close > 0 and stop_price and stop_price > 0:
        return _clip((close / stop_price - 1.0), 0.02, 0.35)
    return float(risk.get("max_stop_distance_pct", 12.0)) / 100.0


def _volatility_proxy(*, close: float, atr14: float | None) -> float:
    if close <= 0 or not atr14:
        return 0.0
    return _clip(float(atr14) / close / 0.06, 0.0, 2.0)


def _p(cfg: dict[str, Any], key: str, default: float) -> float:
    try:
        out = float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _sigmoid(value: float) -> float:
    value = _float(value, 0.0)
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _clip(value: float, low: float, high: float) -> float:
    value = _float(value, 0.0)
    return max(low, min(high, value))
