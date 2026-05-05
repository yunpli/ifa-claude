"""First-pass continuous optimizers for Stock Edge tuning.

These optimizers are intentionally lightweight and deterministic. They provide
standalone run capability for weekend global presets and pre-report single-stock
overlays while the heavier replay/ML stack is still being built.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
import time
from collections.abc import Mapping
from typing import Any, Callable

import pandas as pd

from ifa.families.stock.params import params_hash

from .objectives import build_composite_objective, continuous_overlay_bounds, score_prediction_objective
from .tuning_artifact import TuningArtifact


def fit_pre_report_overlay(
    daily_bars: pd.DataFrame,
    *,
    ts_code: str,
    as_of_trade_date: dt.date,
    base_params: Mapping[str, Any],
    max_candidates: int = 64,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    progress_every: int = 10,
) -> TuningArtifact:
    """Tune a bounded continuous parameter overlay for one stock.

    The optimizer uses only local historical daily bars up to `as_of_trade_date`.
    It does not perform report-time walk-forward adaptation. It searches a small
    deterministic candidate set around the global/base preset and returns the
    best overlay artifact for the prediction-execution objective.
    """
    frame = _clean_daily(daily_bars, as_of_trade_date)
    overlay, metrics, score, candidates = _search_overlay(
        {"single": frame},
        base_params=base_params,
        seed=f"{ts_code}:{as_of_trade_date:%Y%m%d}:overlay",
        max_candidates=max_candidates,
        on_progress=on_progress,
        progress_every=progress_every,
    )
    return TuningArtifact(
        ts_code=ts_code,
        as_of_trade_date=as_of_trade_date,
        kind="pre_report_overlay",
        base_param_hash=params_hash(dict(base_params)),
        overlay=overlay,
        objective_score=score,
        metrics=metrics,
        candidate_count=candidates,
        history_start=frame["trade_date"].iloc[0] if not frame.empty else None,
        history_end=frame["trade_date"].iloc[-1] if not frame.empty else None,
        history_rows=len(frame),
        created_at=dt.datetime.now(dt.timezone.utc),
        namespace=f"stock_edge/tuning/{ts_code.replace('.', '_')}/{as_of_trade_date:%Y%m%d}",
        objective_version=str(metrics.get("objective_version", "stock_edge_5_10_20_v1")),
    )


def fit_global_preset(
    bars_by_stock: Mapping[str, pd.DataFrame],
    *,
    as_of_date: dt.date,
    base_params: Mapping[str, Any],
    universe: str = "top_liquidity_500",
    max_candidates: int = 96,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    progress_every: int = 10,
) -> TuningArtifact:
    """Tune a shared global preset across a stock universe."""
    cleaned = {
        code: _clean_daily(frame, as_of_date)
        for code, frame in bars_by_stock.items()
        if frame is not None and not frame.empty
    }
    overlay, metrics, score, candidates = _search_overlay(
        cleaned,
        base_params=base_params,
        seed=f"{universe}:{as_of_date:%Y%m%d}:global",
        max_candidates=max_candidates,
        on_progress=on_progress,
        progress_every=progress_every,
    )
    rows = sum(len(frame) for frame in cleaned.values())
    starts = [frame["trade_date"].iloc[0] for frame in cleaned.values() if not frame.empty]
    ends = [frame["trade_date"].iloc[-1] for frame in cleaned.values() if not frame.empty]
    metrics = dict(metrics)
    metrics["input_stock_count"] = len(cleaned)
    metrics["stock_count"] = sum(1 for frame in cleaned.values() if len(frame) >= 80)
    return TuningArtifact(
        ts_code="__GLOBAL__",
        as_of_trade_date=as_of_date,
        kind="global_preset",
        base_param_hash=params_hash(dict(base_params)),
        overlay=overlay,
        objective_score=score,
        metrics=metrics,
        candidate_count=candidates,
        history_start=min(starts) if starts else None,
        history_end=max(ends) if ends else None,
        history_rows=rows,
        created_at=dt.datetime.now(dt.timezone.utc),
        namespace=f"stock_edge/global_preset/{universe}/{as_of_date:%Y%m%d}",
        objective_version=str(metrics.get("objective_version", "stock_edge_5_10_20_v1")),
    )


def _search_overlay(
    frames: Mapping[str, pd.DataFrame],
    *,
    base_params: Mapping[str, Any],
    seed: str,
    max_candidates: int,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    progress_every: int = 10,
) -> tuple[dict[str, Any], dict[str, Any], float, int]:
    valid_frames = {k: v for k, v in frames.items() if len(v) >= 80}
    if not valid_frames:
        return {}, {"reason": "history_too_short", "sample_count": 0}, 0.0, 0

    best_overlay: dict[str, Any] = {}
    best_metrics: dict[str, Any] = {}
    best_score = -999.0
    candidates = _candidate_overlays(base_params, seed=seed, max_candidates=max_candidates)
    started = time.monotonic()
    total = len(candidates)
    for idx, overlay in enumerate(candidates, start=1):
        metrics = _evaluate_overlay(valid_frames, overlay, base_params)
        weights = dict(base_params.get("tuning", {}).get("objective", {}).get("composite_weights", {}))
        score = score_prediction_objective(metrics, weights=weights)
        if score > best_score:
            best_overlay = overlay
            best_metrics = metrics
            best_score = score
        if on_progress and (idx == 1 or idx == total or idx % max(1, progress_every) == 0):
            elapsed = time.monotonic() - started
            eta = elapsed / idx * max(total - idx, 0) if idx else 0.0
            on_progress({
                "candidate": idx,
                "total": total,
                "score": score,
                "best_score": best_score,
                "elapsed_seconds": round(elapsed, 2),
                "eta_seconds": round(eta, 2),
            })
    return best_overlay, best_metrics, best_score, len(candidates)


def _candidate_overlays(base_params: Mapping[str, Any], *, seed: str, max_candidates: int) -> list[dict[str, Any]]:
    bounds = _search_bounds(base_params)
    keys = [
        "aggregate.raw_edge_scale",
        "aggregate.buy_threshold",
        "aggregate.watch_threshold",
        "smooth_scoring.momentum_center_pct",
        "smooth_scoring.momentum_width_pct",
        "smooth_scoring.support_distance_mid_pct",
        "smooth_scoring.support_distance_scale_pct",
        "cluster_weights.trend_breakout",
        "cluster_weights.pullback_continuation",
        "cluster_weights.reversal_mean_reversion",
        "cluster_weights.order_flow_smart_money",
        "cluster_weights.sw_l2_sector_leadership",
        "cluster_weights.fundamentals_quality",
        "cluster_weights.model_ensemble",
        "cluster_weights.intraday_t0_execution",
        "cluster_weights.risk_warning",
        "signal_weights.trend_quality_r2",
        "signal_weights.volume_price_divergence",
        "signal_weights.candle_reversal_structure",
        "signal_weights.gap_risk_open_model",
        "signal_weights.regime_adaptive_weight_model",
        "signal_weights.limit_up_event_path_model",
        "signal_weights.peer_financial_alpha_model",
        "signal_weights.position_sizing_model",
        "signal_weights.strategy_validation_decay",
        "signal_weights.quantile_return_forecaster",
        "signal_weights.entry_fill_classifier",
        "signal_weights.conformal_return_band",
        "signal_weights.stop_first_classifier",
        "signal_weights.isotonic_score_calibrator",
        "signal_weights.right_tail_meta_gbm",
        "signal_weights.temporal_fusion_sequence_ranker",
        "signal_weights.regime_adaptive_weight_model",
        "signal_weights.position_sizing_model",
        "signal_weights.target_stop_survival_model",
        "signal_weights.stop_loss_hazard_model",
        "signal_weights.multi_horizon_target_classifier",
        "signal_weights.target_ladder_probability_model",
        "signal_weights.path_shape_mixture_model",
        "signal_weights.mfe_mae_surface_model",
        "signal_weights.forward_entry_timing_model",
        "signal_weights.entry_price_surface_model",
        "signal_weights.pullback_rebound_classifier",
        "signal_weights.squeeze_breakout_classifier",
        "signal_weights.model_stack_blender",
        "signal_weights.analog_kronos_nearest_neighbors",
        "signal_weights.kronos_path_cluster_transition",
        "signal_weights.peer_research_auto_trigger",
        "signal_weights.hierarchical_sector_shrinkage",
        "signal_weights.northbound_regime",
        "signal_weights.market_margin_impulse",
        "signal_weights.block_trade_pressure",
        "signal_weights.event_catalyst_llm",
        "signal_weights.fundamental_contradiction_llm",
        "signal_weights.sector_diffusion_breadth",
        "signal_weights.volume_profile_support",
        "signal_weights.vwap_reclaim_execution",
        "signal_weights.auction_imbalance_proxy",
        "risk.max_entry_distance_from_support_pct",
        "risk.max_stop_distance_pct",
        "risk.right_tail_target_pct",
    ]
    base = {key: _get_param(base_params, key) for key in keys}
    rng = random.Random(int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12], 16))
    overlays: list[dict[str, Any]] = [{}]
    for _ in range(max(1, max_candidates - 1)):
        overlay: dict[str, Any] = {}
        for key in keys:
            low, high = bounds[key]
            center = float(base[key]) if base[key] is not None else (low + high) / 2.0
            width = (high - low) * 0.22
            value = max(low, min(high, rng.gauss(center, width)))
            overlay[key] = round(value, 6)
        if overlay["aggregate.watch_threshold"] >= overlay["aggregate.buy_threshold"]:
            overlay["aggregate.watch_threshold"] = round(max(bounds["aggregate.watch_threshold"][0], overlay["aggregate.buy_threshold"] - 0.08), 6)
        overlays.append(overlay)
    return overlays


def _evaluate_overlay(
    frames: Mapping[str, pd.DataFrame],
    overlay: Mapping[str, Any],
    base_params: Mapping[str, Any],
) -> dict[str, Any]:
    signal_count = 0
    fill_count = 0
    predicted_scores: list[float] = []
    horizon_stats = {5: _empty_horizon_stats(), 10: _empty_horizon_stats(), 20: _empty_horizon_stats()}
    legacy_40d = _empty_legacy_stats()

    for frame in frames.values():
        enriched = _features(frame, overlay, base_params)
        for idx in range(60, len(enriched) - 21):
            row = enriched.iloc[idx]
            score = float(row["candidate_score"])
            buy_threshold = float(_overlay_value(overlay, base_params, "aggregate.buy_threshold"))
            if score < buy_threshold:
                continue
            entry_low, entry_high = _entry_zone(row, overlay, base_params)
            next_5d = enriched.iloc[idx + 1 : idx + 6]
            fill_idx = _first_fill_index(next_5d, entry_low=entry_low, entry_high=entry_high)
            signal_count += 1
            if fill_idx is None:
                predicted_scores.append(score)
                continue
            fill_count += 1
            entry = entry_high
            if entry <= 0:
                predicted_scores.append(score)
                continue
            stop_distance = float(_overlay_value(overlay, base_params, "risk.max_stop_distance_pct") or 10.0) / 100.0
            for horizon in (5, 10, 20):
                future = enriched.iloc[fill_idx + 1 : fill_idx + 1 + horizon]
                _update_horizon_stats(
                    horizon_stats[horizon],
                    future,
                    entry=entry,
                    stop_distance=stop_distance,
                    target_return=_target_return_for_horizon(horizon, overlay, base_params),
                )
            future_40 = enriched.iloc[fill_idx + 1 : fill_idx + 41]
            _update_legacy_40d(legacy_40d, future_40, entry=entry, stop_distance=stop_distance, overlay=overlay, base_params=base_params)
            predicted_scores.append(score)

    if signal_count == 0:
        empty_objective = build_composite_objective(
            _empty_horizon_objective(),
            _empty_horizon_objective(),
            _empty_horizon_objective(),
            calibration_quality=0.0,
            turnover_liquidity_penalty=1.0,
        )
        return {"sample_count": 0, "fill_rate_5d": 0.0, **empty_objective, "legacy_40d_audit": {}}

    fill_rate = fill_count / signal_count
    sample_factor = min(1.0, math.sqrt(signal_count / 60.0))
    objective_5d = _horizon_objective(horizon_stats[5], signal_count=signal_count, fill_rate=fill_rate, sample_factor=sample_factor, horizon=5)
    objective_10d = _horizon_objective(horizon_stats[10], signal_count=signal_count, fill_rate=fill_rate, sample_factor=sample_factor, horizon=10)
    objective_20d = _horizon_objective(horizon_stats[20], signal_count=signal_count, fill_rate=fill_rate, sample_factor=sample_factor, horizon=20)
    mean_pred = sum(predicted_scores) / max(1, len(predicted_scores))
    avg_positive = (
        objective_5d["positive_return_rate"] +
        objective_10d["positive_return_rate"] +
        objective_20d["positive_return_rate"]
    ) / 3.0
    calibration_quality = max(0.0, 1.0 - abs(mean_pred - avg_positive) / 0.75)
    trade_rate = signal_count / max(1, sum(max(0, len(f) - 100) for f in frames.values()))
    turnover_penalty = max(0.0, min(1.0, trade_rate / 0.35))
    objective_payload = build_composite_objective(
        objective_5d,
        objective_10d,
        objective_20d,
        calibration_quality=_clip(calibration_quality, 0.0, 1.0),
        turnover_liquidity_penalty=turnover_penalty,
        strategy_decay_penalty=0.0,
        weights=dict(base_params.get("tuning", {}).get("objective", {}).get("composite_weights", {})),
    )
    return {
        "sample_count": signal_count,
        "fill_rate_5d": round(fill_rate, 6),
        "trade_rate": round(trade_rate, 6),
        **objective_payload,
        "legacy_40d_audit": _legacy_40d_metrics(legacy_40d),
    }


def _empty_horizon_stats() -> dict[str, Any]:
    return {
        "count": 0,
        "positive": 0,
        "target_first": 0,
        "stop_first": 0,
        "returns": [],
        "drawdowns": [],
        "mfe": [],
        "mae": [],
        "reward_risks": [],
    }


def _empty_legacy_stats() -> dict[str, Any]:
    return {"count": 0, "target": 0, "stop": 0, "returns": [], "drawdowns": []}


def _empty_horizon_objective() -> dict[str, float]:
    return {
        "positive_return_rate": 0.0,
        "target_first_rate": 0.0,
        "stop_first_rate": 1.0,
        "avg_return": 0.0,
        "median_return": 0.0,
        "avg_drawdown": 1.0,
        "mfe_mae_ratio": 0.0,
        "positive_return_quality": 0.0,
        "target_first_quality": 0.0,
        "entry_fill_quality": 0.0,
        "reward_risk": 0.0,
        "risk_adjusted_return": 0.0,
        "drawdown_penalty": 1.0,
        "stop_first_penalty": 1.0,
        "liquidity_penalty": 1.0,
        "chase_failure_penalty": 1.0,
        "overheat_penalty": 0.0,
        "decay_penalty": 0.0,
        "auxiliary_penalty": 0.0,
    }


def _target_return_for_horizon(horizon: int, overlay: Mapping[str, Any], base_params: Mapping[str, Any]) -> float:
    legacy_target = float(_overlay_value(overlay, base_params, "risk.right_tail_target_pct") or 25.0) / 100.0
    defaults = {5: 0.05, 10: 0.08, 20: min(0.20, max(0.12, legacy_target * 0.55))}
    return defaults[horizon]


def _update_horizon_stats(
    stats: dict[str, Any],
    future: pd.DataFrame,
    *,
    entry: float,
    stop_distance: float,
    target_return: float,
) -> None:
    if future.empty or entry <= 0:
        return
    final_close = float(future.iloc[-1]["close"])
    max_high = float(future["high"].max())
    min_low = float(future["low"].min())
    target_price = entry * (1.0 + target_return)
    stop_price = entry * (1.0 - stop_distance)
    event = _first_path_event(future, target_price=target_price, stop_price=stop_price)
    ret = final_close / entry - 1.0
    drawdown = max(0.0, 1.0 - min_low / entry)
    mfe = max(0.0, max_high / entry - 1.0)
    mae = max(0.0, 1.0 - min_low / entry)
    stats["count"] += 1
    stats["positive"] += int(ret > 0)
    stats["target_first"] += int(event == "target")
    stats["stop_first"] += int(event == "stop")
    stats["returns"].append(ret)
    stats["drawdowns"].append(drawdown)
    stats["mfe"].append(mfe)
    stats["mae"].append(mae)
    stats["reward_risks"].append(mfe / max(0.03, mae, stop_distance))


def _horizon_objective(
    stats: Mapping[str, Any],
    *,
    signal_count: int,
    fill_rate: float,
    sample_factor: float,
    horizon: int,
) -> dict[str, float]:
    count = int(stats.get("count") or 0)
    if count <= 0:
        return _empty_horizon_objective()
    returns = list(stats.get("returns") or [])
    drawdowns = list(stats.get("drawdowns") or [])
    reward_risks = list(stats.get("reward_risks") or [])
    mfe = list(stats.get("mfe") or [])
    mae = list(stats.get("mae") or [])
    positive_rate = float(stats.get("positive") or 0) / max(1, count)
    target_first_rate = float(stats.get("target_first") or 0) / max(1, count)
    stop_first_rate = float(stats.get("stop_first") or 0) / max(1, count)
    avg_return = sum(returns) / max(1, len(returns))
    median_return = float(pd.Series(returns).median()) if returns else 0.0
    avg_drawdown = sum(drawdowns) / max(1, len(drawdowns))
    avg_reward_risk = sum(reward_risks) / max(1, len(reward_risks))
    avg_mfe = sum(mfe) / max(1, len(mfe))
    avg_mae = sum(mae) / max(1, len(mae))
    return_scale = {5: 0.12, 10: 0.18, 20: 0.28}[horizon]
    drawdown_scale = {5: 0.10, 10: 0.14, 20: 0.20}[horizon]
    chase_failure = max(0.0, 1.0 - fill_rate)
    liquidity_penalty = max(0.0, min(1.0, signal_count / 20000.0))
    return {
        "positive_return_rate": round(positive_rate, 6),
        "target_first_rate": round(target_first_rate, 6),
        "stop_first_rate": round(stop_first_rate, 6),
        "avg_return": round(avg_return, 6),
        "median_return": round(median_return, 6),
        "avg_drawdown": round(avg_drawdown, 6),
        "avg_mfe": round(avg_mfe, 6),
        "avg_mae": round(avg_mae, 6),
        "mfe_mae_ratio": round(avg_mfe / max(0.01, avg_mae), 6),
        "positive_return_quality": _clip(positive_rate * sample_factor, 0.0, 1.0),
        "target_first_quality": _clip(target_first_rate * sample_factor, 0.0, 1.0),
        "entry_fill_quality": _clip(fill_rate * sample_factor, 0.0, 1.0),
        "reward_risk": _clip(avg_reward_risk / 3.0, 0.0, 1.0),
        "risk_adjusted_return": _clip((avg_return + return_scale / 2.0) / (return_scale * 1.5), 0.0, 1.0),
        "drawdown_penalty": _clip(avg_drawdown / drawdown_scale, 0.0, 1.0),
        "stop_first_penalty": _clip(stop_first_rate, 0.0, 1.0),
        "liquidity_penalty": round(liquidity_penalty, 6),
        "chase_failure_penalty": round(chase_failure, 6),
        "overheat_penalty": _clip(max(0.0, avg_return - return_scale) / max(0.01, return_scale), 0.0, 1.0),
        "decay_penalty": 0.0,
        "auxiliary_penalty": 0.0,
    }


def _update_legacy_40d(
    stats: dict[str, Any],
    future: pd.DataFrame,
    *,
    entry: float,
    stop_distance: float,
    overlay: Mapping[str, Any],
    base_params: Mapping[str, Any],
) -> None:
    if future.empty or entry <= 0:
        return
    target_return = float(_overlay_value(overlay, base_params, "risk.right_tail_target_pct") or 25.0) / 100.0
    target_price = entry * (1.0 + target_return)
    stop_price = entry * (1.0 - stop_distance)
    event = _first_path_event(future, target_price=target_price, stop_price=stop_price)
    stats["count"] += 1
    stats["target"] += int(event == "target")
    stats["stop"] += int(event == "stop")
    stats["returns"].append(float(future.iloc[-1]["close"]) / entry - 1.0)
    stats["drawdowns"].append(max(0.0, 1.0 - float(future["low"].min()) / entry))


def _legacy_40d_metrics(stats: Mapping[str, Any]) -> dict[str, float | str]:
    count = int(stats.get("count") or 0)
    returns = list(stats.get("returns") or [])
    drawdowns = list(stats.get("drawdowns") or [])
    return {
        "role": "legacy_audit_only_not_main_objective",
        "sample_count": count,
        "hit_target_40d_rate": round(float(stats.get("target") or 0) / max(1, count), 6),
        "stop_first_40d_rate": round(float(stats.get("stop") or 0) / max(1, count), 6),
        "avg_return_40d": round(sum(returns) / max(1, len(returns)), 6),
        "avg_drawdown_40d": round(sum(drawdowns) / max(1, len(drawdowns)), 6),
    }


def _features(frame: pd.DataFrame, overlay: Mapping[str, Any], base_params: Mapping[str, Any]) -> pd.DataFrame:
    df = frame.copy()
    df["ret_5"] = df["close"].pct_change(5)
    df["ret_20"] = df["close"].pct_change(20)
    df["range_high_60"] = df["high"].rolling(60, min_periods=20).max()
    df["range_low_60"] = df["low"].rolling(60, min_periods=20).min()
    width = (df["range_high_60"] - df["range_low_60"]).replace(0, pd.NA)
    df["range_pos"] = ((df["close"] - df["range_low_60"]) / width).fillna(0.5)
    df["drawdown_20"] = (df["close"] / df["close"].rolling(20, min_periods=10).max() - 1.0).fillna(0.0)
    amount = pd.to_numeric(df.get("amount", pd.Series([0] * len(df))), errors="coerce").fillna(0.0)
    df["amount_ratio"] = (amount / amount.rolling(20, min_periods=5).mean().replace(0, pd.NA)).fillna(1.0)
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14_pct"] = (tr.rolling(14, min_periods=5).mean() / df["close"].replace(0, pd.NA)).fillna(0.04)

    momentum_center = float(_overlay_value(overlay, base_params, "smooth_scoring.momentum_center_pct")) / 100.0
    momentum_width = max(0.01, float(_overlay_value(overlay, base_params, "smooth_scoring.momentum_width_pct")) / 100.0)
    trend_mult = _component_multiplier(
        overlay,
        base_params,
        "cluster_weights.trend_breakout",
        "signal_weights.trend_quality_r2",
        "signal_weights.volume_price_divergence",
    )
    pullback_mult = _component_multiplier(overlay, base_params, "cluster_weights.pullback_continuation")
    flow_mult = _component_multiplier(
        overlay,
        base_params,
        "cluster_weights.order_flow_smart_money",
        "signal_weights.northbound_regime",
        "signal_weights.block_trade_pressure",
        "signal_weights.event_catalyst_llm",
    )
    reversal_mult = _component_multiplier(
        overlay,
        base_params,
        "cluster_weights.reversal_mean_reversion",
        "signal_weights.candle_reversal_structure",
    )
    model_mult = _component_multiplier(
        overlay,
        base_params,
        "cluster_weights.model_ensemble",
        "signal_weights.strategy_validation_decay",
        "signal_weights.isotonic_score_calibrator",
        "signal_weights.right_tail_meta_gbm",
        "signal_weights.temporal_fusion_sequence_ranker",
        "signal_weights.target_stop_survival_model",
        "signal_weights.stop_loss_hazard_model",
        "signal_weights.multi_horizon_target_classifier",
        "signal_weights.target_ladder_probability_model",
        "signal_weights.path_shape_mixture_model",
        "signal_weights.mfe_mae_surface_model",
        "signal_weights.entry_price_surface_model",
        "signal_weights.model_stack_blender",
        "signal_weights.analog_kronos_nearest_neighbors",
        "signal_weights.kronos_path_cluster_transition",
    )
    fundamental_mult = _component_multiplier(
        overlay,
        base_params,
        "cluster_weights.fundamentals_quality",
        "signal_weights.peer_research_auto_trigger",
        "signal_weights.peer_financial_alpha_model",
        "signal_weights.fundamental_contradiction_llm",
    )
    execution_mult = _component_multiplier(
        overlay,
        base_params,
        "cluster_weights.intraday_t0_execution",
        "signal_weights.entry_fill_classifier",
        "signal_weights.forward_entry_timing_model",
        "signal_weights.entry_price_surface_model",
        "signal_weights.gap_risk_open_model",
    )
    risk_mult = _component_multiplier(
        overlay,
        base_params,
        "cluster_weights.risk_warning",
        "signal_weights.market_margin_impulse",
        "signal_weights.stop_loss_hazard_model",
    )
    components = [
        (0.28, trend_mult, ((df["ret_20"].fillna(0.0) - momentum_center) / momentum_width).apply(math.tanh)),
        (0.20, trend_mult, (df["ret_5"].fillna(0.0) / max(0.03, momentum_width / 2.0)).apply(math.tanh)),
        (0.16, pullback_mult, ((df["range_pos"].fillna(0.5) - 0.45) / 0.22).apply(math.tanh)),
        (0.16, flow_mult, ((df["amount_ratio"].fillna(1.0) - 1.0) / 0.6).apply(math.tanh)),
        (0.12, reversal_mult, ((df["drawdown_20"].fillna(0.0) + 0.08) / 0.08).apply(math.tanh)),
        (0.08, model_mult, ((df["ret_20"].fillna(0.0) + df["ret_5"].fillna(0.0)) / 0.18).apply(math.tanh)),
        (0.05, fundamental_mult, ((df["range_pos"].fillna(0.5) - 0.40) / 0.25).apply(math.tanh)),
        (0.06, execution_mult, ((df["range_pos"].fillna(0.5) - 0.35) / 0.22).apply(lambda v: -math.tanh(v))),
        (-0.08, risk_mult, (df["drawdown_20"].fillna(0.0).abs() / 0.18).clip(0.0, 1.5)),
    ]
    normalizer = max(0.10, sum(abs(coef) * max(0.05, mult) for coef, mult, _series in components))
    raw = sum(coef * max(0.0, mult) * series for coef, mult, series in components) / normalizer
    raw_edge_scale = float(_overlay_value(overlay, base_params, "aggregate.raw_edge_scale"))
    df["candidate_score"] = (0.50 + raw * raw_edge_scale).clip(0.0, 1.0).fillna(0.0)
    return df


def _clean_daily(daily_bars: pd.DataFrame, as_of_date: dt.date) -> pd.DataFrame:
    required = {"trade_date", "open", "high", "low", "close"}
    if daily_bars.empty or not required.issubset(daily_bars.columns):
        return pd.DataFrame(columns=sorted(required))
    df = daily_bars.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df = df[df["trade_date"] <= as_of_date].sort_values("trade_date").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def _overlay_value(overlay: Mapping[str, Any], base_params: Mapping[str, Any], dotted_key: str) -> Any:
    if dotted_key in overlay:
        return overlay[dotted_key]
    return _get_param(base_params, dotted_key)


def _component_multiplier(overlay: Mapping[str, Any], base_params: Mapping[str, Any], *keys: str) -> float:
    values = []
    for key in keys:
        value = _overlay_value(overlay, base_params, key)
        if value is not None:
            values.append(float(value))
    if not values:
        return 1.0
    return max(0.0, min(sum(values) / len(values), 3.0))


def _entry_zone(row: pd.Series, overlay: Mapping[str, Any], base_params: Mapping[str, Any]) -> tuple[float, float]:
    close = float(row["close"])
    score = _clip(float(row.get("candidate_score", 0.5)), 0.0, 1.0)
    atr_pct = _clip(float(row.get("atr14_pct", 0.04)), 0.01, 0.16)
    max_distance_pct = float(_overlay_value(overlay, base_params, "risk.max_entry_distance_from_support_pct") or 8.0) / 100.0
    support_mid_pct = float(_overlay_value(overlay, base_params, "smooth_scoring.support_distance_mid_pct") or 8.0) / 100.0
    support_scale_pct = float(_overlay_value(overlay, base_params, "smooth_scoring.support_distance_scale_pct") or 3.0) / 100.0
    pullback_pct = _clip((0.55 + (1.0 - score) * 0.70) * min(max_distance_pct, support_mid_pct), 0.003, 0.080)
    band_width_pct = _clip(0.35 * atr_pct + 0.25 * support_scale_pct, 0.004, 0.045)
    entry_high = close * (1.0 - pullback_pct)
    entry_low = entry_high * (1.0 - band_width_pct)
    return entry_low, entry_high


def _first_fill_index(future: pd.DataFrame, *, entry_low: float, entry_high: float) -> int | None:
    for idx, row in future.iterrows():
        if float(row["low"]) <= entry_high and float(row["high"]) >= entry_low:
            return int(idx)
    return None


def _first_path_event(future: pd.DataFrame, *, target_price: float, stop_price: float) -> str | None:
    for _, row in future.iterrows():
        hit_stop = float(row["low"]) <= stop_price
        hit_target = float(row["high"]) >= target_price
        if hit_stop and hit_target:
            open_price = float(row.get("open", 0.0))
            return "target" if abs(target_price - open_price) < abs(open_price - stop_price) else "stop"
        if hit_stop:
            return "stop"
        if hit_target:
            return "target"
    return None


def _get_param(params: Mapping[str, Any], dotted_key: str) -> Any:
    current: Any = params.get("strategy_matrix", params)
    parts = dotted_key.split(".")
    if parts[0] == "risk":
        current = params
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _search_bounds(base_params: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    bounds = continuous_overlay_bounds()
    raw = base_params.get("tuning", {}).get("search_bounds", {})
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if isinstance(value, (list, tuple)) and len(value) == 2:
                low, high = float(value[0]), float(value[1])
                if low < high:
                    bounds[str(key)] = (low, high)
    return bounds
