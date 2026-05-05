"""Objective functions for Stock Edge parameter tuning.

The objective is intentionally separate from the report builder. Weekend global
preset training and pre-report per-stock overlay tuning should optimize the
same prediction-execution target without changing report code.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PredictionObjectiveInputs:
    """Normalized metrics for one candidate parameter set.

    All fields are continuous and should be scaled to roughly ``[0, 1]`` before
    scoring, except penalties which also use ``[0, 1]`` where higher is worse.
    The caller is responsible for computing the metrics from replay labels.
    """

    hit_target_40d_quality: float
    expected_return_40d: float
    entry_fill_quality: float
    reward_risk: float
    calibration_quality: float
    expected_drawdown: float
    stop_first_rate: float
    turnover_liquidity_penalty: float

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_OBJECTIVE_WEIGHTS: dict[str, float] = {
    "hit_target_40d_quality": 0.30,
    "expected_return_40d": 0.20,
    "entry_fill_quality": 0.15,
    "reward_risk": 0.15,
    "calibration_quality": 0.10,
    "expected_drawdown": -0.15,
    "stop_first_rate": -0.10,
    "turnover_liquidity_penalty": -0.05,
}


def score_prediction_objective(
    metrics: PredictionObjectiveInputs,
    *,
    weights: dict[str, float] | None = None,
) -> float:
    """Score a tuning candidate by prediction-execution quality.

    This is not a raw return maximizer. It rewards a candidate only when it
    improves the final executable prediction: target hit quality, expected
    return, fill feasibility, reward/risk, and probability calibration.
    Drawdown, stop-first behavior, and liquidity/turnover costs are explicit
    penalties.
    """
    w = {**DEFAULT_OBJECTIVE_WEIGHTS, **(weights or {})}
    data = metrics.to_dict()
    score = 0.0
    for key, weight in w.items():
        score += float(weight) * _clip(float(data.get(key, 0.0)), 0.0, 1.0)
    return round(score, 6)


def continuous_overlay_bounds() -> dict[str, tuple[float, float]]:
    """Return first-pass continuous parameter bounds for overlay search.

    The optimizer should search inside these ranges without introducing hard
    discrete branches. These are deliberately conservative; single-stock tuning
    should personalize around the global preset, not rewrite the strategy.
    """
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
        "signal_weights.right_tail_meta_gbm": (0.20, 1.80),
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
        "risk.right_tail_target_pct": (20.0, 60.0),
        "t0.max_size_pct_of_base": (5.0, 30.0),
    }


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
