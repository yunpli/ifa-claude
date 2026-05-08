"""Objective functions for Stock Edge parameter tuning.

The production Stock Edge v2.2 objective is horizon-specific. The main score
optimizes 5/10/20 trading-day executable decisions; legacy 40d right-tail
statistics may be emitted for audit, but they must not contribute to the main
objective.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

OBJECTIVE_VERSION = "stock_edge_5_10_20_v1"


@dataclass(frozen=True)
class HorizonObjectiveInputs:
    """Normalized metrics for one horizon.

    Quality fields use roughly ``[0, 1]`` where higher is better. Penalty fields
    also use ``[0, 1]`` where higher is worse.
    """

    positive_return_quality: float
    target_first_quality: float
    entry_fill_quality: float
    reward_risk: float
    risk_adjusted_return: float
    drawdown_penalty: float
    stop_first_penalty: float
    liquidity_penalty: float
    chase_failure_penalty: float = 0.0
    overheat_penalty: float = 0.0
    decay_penalty: float = 0.0
    auxiliary_penalty: float = 0.0
    rank_ic_quality: float = 0.0
    top_bucket_return_quality: float = 0.0
    top_bottom_spread_quality: float = 0.0
    bucket_monotonicity_quality: float = 0.0
    top_bucket_win_quality: float = 0.0
    top_bucket_left_tail_penalty: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class PredictionObjectiveInputs:
    """Three-horizon objective inputs for a candidate parameter overlay."""

    objective_5d: HorizonObjectiveInputs
    objective_10d: HorizonObjectiveInputs
    objective_20d: HorizonObjectiveInputs
    calibration_quality: float = 0.0
    turnover_liquidity_penalty: float = 0.0
    strategy_decay_penalty: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_version": OBJECTIVE_VERSION,
            "objective_5d": self.objective_5d.to_dict(),
            "objective_10d": self.objective_10d.to_dict(),
            "objective_20d": self.objective_20d.to_dict(),
            "composite_objective": {
                "calibration_quality": self.calibration_quality,
                "turnover_liquidity_penalty": self.turnover_liquidity_penalty,
                "strategy_decay_penalty": self.strategy_decay_penalty,
            },
        }


DEFAULT_HORIZON_WEIGHTS: dict[str, float] = {
    # Outcome-first stock selection objective:
    # reward score-vs-realized forward-return ranking and top-bucket payoff
    # directly. Target-first and entry-fill remain execution diagnostics, not
    # the main optimization target.
    "rank_ic_quality": 0.28,
    "top_bucket_return_quality": 0.18,
    "top_bottom_spread_quality": 0.14,
    "bucket_monotonicity_quality": 0.10,
    "top_bucket_win_quality": 0.08,
    "positive_return_quality": 0.08,
    "target_first_quality": 0.06,
    "entry_fill_quality": 0.04,
    "reward_risk": 0.08,
    "risk_adjusted_return": 0.08,
    "drawdown_penalty": -0.14,
    "stop_first_penalty": -0.10,
    "top_bucket_left_tail_penalty": -0.12,
    "liquidity_penalty": -0.05,
    "chase_failure_penalty": -0.05,
    "overheat_penalty": -0.04,
    "decay_penalty": -0.04,
    "auxiliary_penalty": -0.03,
}

DEFAULT_COMPOSITE_WEIGHTS: dict[str, float] = {
    "horizon_5d": 0.34,
    "horizon_10d": 0.33,
    "horizon_20d": 0.33,
    "calibration_quality": 0.08,
    "turnover_liquidity_penalty": -0.05,
    "strategy_decay_penalty": -0.04,
}


def score_horizon_objective(
    metrics: HorizonObjectiveInputs | Mapping[str, Any],
    *,
    weights: Mapping[str, float] | None = None,
) -> float:
    data = metrics.to_dict() if isinstance(metrics, HorizonObjectiveInputs) else dict(metrics)
    w = {**DEFAULT_HORIZON_WEIGHTS, **dict(weights or {})}
    score = 0.0
    for key, weight in w.items():
        score += float(weight) * _clip(float(data.get(key, 0.0) or 0.0), 0.0, 1.0)
    return round(score, 6)


def score_prediction_objective(
    metrics: PredictionObjectiveInputs | Mapping[str, Any],
    *,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Score a tuning candidate by 5/10/20 decision quality.

    The return value is a composite signal-quality score, not a calibrated
    probability. Legacy 40d audit metrics are intentionally ignored here.
    """
    data = metrics.to_dict() if isinstance(metrics, PredictionObjectiveInputs) else dict(metrics)
    composite = dict(data.get("composite_objective") or {})
    if "score" in composite:
        return round(float(composite["score"]), 6)

    cw = {**DEFAULT_COMPOSITE_WEIGHTS, **dict(weights or {})}
    horizon_weights = dict(data.get("horizon_weights") or {})
    horizon_scores = {
        "horizon_5d": score_horizon_objective(data.get("objective_5d") or {}, weights=horizon_weights),
        "horizon_10d": score_horizon_objective(data.get("objective_10d") or {}, weights=horizon_weights),
        "horizon_20d": score_horizon_objective(data.get("objective_20d") or {}, weights=horizon_weights),
    }
    score = 0.0
    for key, value in horizon_scores.items():
        score += float(cw.get(key, 0.0)) * value
    score += float(cw.get("calibration_quality", 0.0)) * _clip(float(composite.get("calibration_quality", 0.0) or 0.0), 0.0, 1.0)
    score += float(cw.get("turnover_liquidity_penalty", 0.0)) * _clip(float(composite.get("turnover_liquidity_penalty", 0.0) or 0.0), 0.0, 1.0)
    score += float(cw.get("strategy_decay_penalty", 0.0)) * _clip(float(composite.get("strategy_decay_penalty", 0.0) or 0.0), 0.0, 1.0)
    return round(score, 6)


def build_composite_objective(
    objective_5d: Mapping[str, Any],
    objective_10d: Mapping[str, Any],
    objective_20d: Mapping[str, Any],
    *,
    calibration_quality: float,
    turnover_liquidity_penalty: float,
    strategy_decay_penalty: float = 0.0,
    weights: Mapping[str, float] | None = None,
    horizon_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Return the artifact-ready composite objective block."""
    payload = {
        "objective_version": OBJECTIVE_VERSION,
        "objective_5d": dict(objective_5d),
        "objective_10d": dict(objective_10d),
        "objective_20d": dict(objective_20d),
        "horizon_weights": dict(horizon_weights or {}),
        "composite_objective": {
            "calibration_quality": round(calibration_quality, 6),
            "turnover_liquidity_penalty": round(turnover_liquidity_penalty, 6),
            "strategy_decay_penalty": round(strategy_decay_penalty, 6),
        },
    }
    score = score_prediction_objective(payload, weights=weights)
    payload["composite_objective"]["score"] = score
    payload["composite_objective"]["horizon_scores"] = {
        "5d": score_horizon_objective(objective_5d, weights=horizon_weights),
        "10d": score_horizon_objective(objective_10d, weights=horizon_weights),
        "20d": score_horizon_objective(objective_20d, weights=horizon_weights),
    }
    return payload


def continuous_overlay_bounds() -> dict[str, tuple[float, float]]:
    """Return first-pass continuous parameter bounds for overlay search.

    Includes both the legacy strategy_matrix-level weights AND the actually-load-bearing
    decision_layer.horizons.<h>.weights/base_score/raw_edge_scale/thresholds. The legacy
    weights (signal_weights/cluster_weights) feed `_apply_param_weights` inside the matrix
    and only nudge `aggregate_score`; the production 5/10/20 decisions are decided by
    decision_layer.* params, which is what panel-based tuning should focus on.
    """
    bounds = _base_overlay_bounds()
    bounds.update(_decision_layer_bounds())
    return bounds


def panel_only_overlay_bounds() -> dict[str, tuple[float, float]]:
    """Bounds restricted to params that actually affect cached signal aggregation.

    A pre-built replay panel caches signal SCORES computed under baseline matrix params.
    Re-aggregating those scores depends only on `decision_layer.horizons.*` (weights /
    base_score / raw_edge_scale / thresholds). Legacy `signal_weights.*` / `cluster_weights.*`
    / `smooth_scoring.*` would require rebuilding the panel to take effect, so panel-based
    tuners must restrict search to this slice.
    """
    return _decision_layer_bounds()


def _decision_layer_bounds() -> dict[str, tuple[float, float]]:
    """Decision-layer search bounds: per-horizon signal weights + scoring params + thresholds.

    Drawn from `decision_layer.DEFAULT_KEYS` + reasonable production-grade band:
    - weights: [0.30, 1.80] around 1.0 baseline
    - base_score: [0.45, 0.55] — small wiggle around 0.50
    - raw_edge_scale: [0.30, 0.70] — controls how far edge translates to score
    - thresholds.buy: [0.62, 0.78] etc. — preserve ordering buy > watch > wait > avoid via post-clip
    """
    from ifa.families.stock.decision_layer import DEFAULT_KEYS

    out: dict[str, tuple[float, float]] = {}
    horizons = ("5d", "10d", "20d")
    for h in horizons:
        keys = DEFAULT_KEYS.get(h, {})
        # Allow zero-out: signals that are inverted in this universe/period should
        # be turned off rather than just minimally weighted. Bound floor was 0.30
        # but a signal with rank IC = -0.34 still drags ranking down at any weight > 0.
        for key in list(keys.get("positive", [])) + list(keys.get("risk", [])):
            out[f"decision_layer.horizons.{h}.weights.{key}"] = (0.0, 1.80)
        out[f"decision_layer.horizons.{h}.weights.risk_penalty_weight"] = (0.0, 1.50)
        out[f"decision_layer.horizons.{h}.base_score"] = (0.45, 0.55)
        out[f"decision_layer.horizons.{h}.raw_edge_scale"] = (0.30, 0.70)
        # Thresholds — bounds chosen to keep ordering buy > watch > wait > avoid
        if h == "5d":
            out[f"decision_layer.horizons.{h}.thresholds.buy"] = (0.62, 0.78)
            out[f"decision_layer.horizons.{h}.thresholds.watch"] = (0.50, 0.62)
            out[f"decision_layer.horizons.{h}.thresholds.wait"] = (0.40, 0.50)
            out[f"decision_layer.horizons.{h}.thresholds.avoid"] = (0.32, 0.42)
        elif h == "10d":
            out[f"decision_layer.horizons.{h}.thresholds.buy"] = (0.60, 0.76)
            out[f"decision_layer.horizons.{h}.thresholds.watch"] = (0.48, 0.60)
            out[f"decision_layer.horizons.{h}.thresholds.wait"] = (0.38, 0.48)
            out[f"decision_layer.horizons.{h}.thresholds.avoid"] = (0.30, 0.40)
        else:  # 20d
            out[f"decision_layer.horizons.{h}.thresholds.buy"] = (0.58, 0.74)
            out[f"decision_layer.horizons.{h}.thresholds.watch"] = (0.46, 0.58)
            out[f"decision_layer.horizons.{h}.thresholds.wait"] = (0.36, 0.46)
            out[f"decision_layer.horizons.{h}.thresholds.avoid"] = (0.28, 0.38)
    return out


def _base_overlay_bounds() -> dict[str, tuple[float, float]]:
    """Legacy strategy_matrix-level overlay bounds (kept for backward compatibility)."""
    return {
        "aggregate.raw_edge_scale": (0.25, 0.85),
        "aggregate.buy_threshold": (0.58, 0.78),
        "aggregate.watch_threshold": (0.42, 0.60),
        "smooth_scoring.support_distance_mid_pct": (3.0, 12.0),
        "smooth_scoring.support_distance_scale_pct": (1.0, 8.0),
        "smooth_scoring.breakout_distance_scale_pct": (2.0, 12.0),
        "smooth_scoring.momentum_center_pct": (0.0, 12.0),
        "smooth_scoring.momentum_width_pct": (3.0, 18.0),
        "smooth_scoring.stock_moneyflow_scale_wan": (2_000.0, 50_000.0),
        "smooth_scoring.sector_flow_scale_wan": (100_000.0, 1_500_000.0),
        "cluster_weights.trend_breakout": (0.30, 1.80),
        "cluster_weights.pullback_continuation": (0.30, 1.80),
        "cluster_weights.reversal_mean_reversion": (0.20, 1.50),
        "cluster_weights.order_flow_smart_money": (0.30, 2.00),
        "cluster_weights.sw_l2_sector_leadership": (0.30, 2.00),
        "cluster_weights.fundamentals_quality": (0.20, 1.50),
        "cluster_weights.model_ensemble": (0.20, 2.00),
        "cluster_weights.intraday_t0_execution": (0.00, 1.20),
        "cluster_weights.risk_warning": (0.40, 1.80),
        "signal_weights.trend_quality_r2": (0.30, 1.80),
        "signal_weights.volume_price_divergence": (0.30, 1.80),
        "signal_weights.candle_reversal_structure": (0.30, 1.60),
        "signal_weights.gap_risk_open_model": (0.20, 1.70),
        "signal_weights.regime_adaptive_weight_model": (0.20, 1.60),
        "signal_weights.limit_up_event_path_model": (0.00, 1.50),
        "signal_weights.peer_financial_alpha_model": (0.20, 1.70),
        "signal_weights.position_sizing_model": (0.20, 1.50),
        "signal_weights.strategy_validation_decay": (0.20, 1.60),
        "signal_weights.quantile_return_forecaster": (0.20, 1.80),
        "signal_weights.entry_fill_classifier": (0.20, 1.60),
        "signal_weights.conformal_return_band": (0.20, 1.60),
        "signal_weights.stop_first_classifier": (0.20, 1.80),
        "signal_weights.isotonic_score_calibrator": (0.20, 1.60),
        "signal_weights.right_tail_meta_gbm": (0.00, 1.20),
        "signal_weights.temporal_fusion_sequence_ranker": (0.20, 1.80),
        "signal_weights.target_stop_survival_model": (0.20, 1.80),
        "signal_weights.stop_loss_hazard_model": (0.20, 1.80),
        "signal_weights.multi_horizon_target_classifier": (0.20, 1.80),
        "signal_weights.target_ladder_probability_model": (0.20, 1.80),
        "signal_weights.path_shape_mixture_model": (0.20, 1.70),
        "signal_weights.mfe_mae_surface_model": (0.20, 1.80),
        "signal_weights.forward_entry_timing_model": (0.20, 1.60),
        "signal_weights.entry_price_surface_model": (0.20, 1.70),
        "signal_weights.pullback_rebound_classifier": (0.20, 1.70),
        "signal_weights.squeeze_breakout_classifier": (0.20, 1.70),
        "signal_weights.model_stack_blender": (0.20, 1.80),
        "signal_weights.analog_kronos_nearest_neighbors": (0.20, 1.80),
        "signal_weights.kronos_path_cluster_transition": (0.20, 1.80),
        "signal_weights.peer_research_auto_trigger": (0.00, 1.20),
        "signal_weights.hierarchical_sector_shrinkage": (0.20, 1.60),
        "signal_weights.northbound_regime": (0.20, 1.50),
        "signal_weights.market_margin_impulse": (0.20, 1.50),
        "signal_weights.block_trade_pressure": (0.00, 1.30),
        "signal_weights.event_catalyst_llm": (0.00, 1.40),
        "signal_weights.fundamental_contradiction_llm": (0.00, 1.40),
        "signal_weights.sector_diffusion_breadth": (0.20, 1.80),
        "signal_weights.volume_profile_support": (0.00, 1.20),
        "signal_weights.vwap_reclaim_execution": (0.00, 1.20),
        "signal_weights.auction_imbalance_proxy": (0.00, 1.20),
        "risk.max_entry_distance_from_support_pct": (3.0, 14.0),
        "risk.max_stop_distance_pct": (6.0, 18.0),
        "risk.right_tail_target_pct": (12.0, 35.0),
        "t0.max_size_pct_of_base": (5.0, 30.0),
    }


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
