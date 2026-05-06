"""Multi-strategy signal matrix for the Stock Edge rule baseline.

This is not an ML model. It is the production-facing scaffold that makes each
rule/statistical/TA/fundamental signal explicit, auditable, and replaceable by
trained models later.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Literal

import pandas as pd

from ifa.families.stock.data.snapshot import StockEdgeSnapshot
from ifa.families.stock.features import build_support_resistance, compute_technical_summary
from ifa.families.stock.features.support_resistance import nearest_resistance, nearest_support
from ifa.families.stock.strategies.entry_fill_replay import build_entry_fill_replay_stats
from ifa.families.stock.strategies.flow_persistence import build_flow_persistence_profile
from ifa.families.stock.strategies.fundamental_dislocation import build_fundamental_dislocation_profile
from ifa.families.stock.strategies.historical_replay import build_historical_replay_stats
from ifa.families.stock.strategies.intraday_profile import build_intraday_profile
from ifa.families.stock.strategies.liquidity_slippage import build_liquidity_slippage_profile
from ifa.families.stock.strategies.meta_models import (
    build_entry_price_surface_model,
    build_forward_entry_timing_model,
    build_gap_risk_open_model,
    build_mfe_mae_surface_model,
    build_multi_horizon_target_model,
    build_path_shape_mixture_model,
    build_pullback_rebound_model,
    build_right_tail_meta_gbm,
    build_squeeze_breakout_model,
    build_stop_loss_hazard_model,
    build_target_ladder_probability_model,
    build_target_stop_survival_model,
    build_temporal_sequence_ranker,
)
from ifa.families.stock.strategies.path_forecast import build_path_forecast_profile
from ifa.families.stock.strategies.peer_fundamental_spread import build_peer_fundamental_spread_profile
from ifa.families.stock.strategies.price_action import build_price_action_profile
from ifa.families.stock.strategies.sector_diffusion import build_sector_diffusion_profile
from ifa.families.stock.strategies.target_stop_replay import build_target_stop_replay_stats
from ifa.families.stock.strategies.t0_uplift import build_t0_uplift_profile
from ifa.families.stock.strategies.validation_decay import build_validation_decay_profile
from ifa.families.stock.strategies.vwap_execution import build_vwap_execution_profile

SignalDirection = Literal["positive", "neutral", "negative"]
SignalStatus = Literal["active", "degraded", "missing"]


@dataclass(frozen=True)
class StrategySignal:
    key: str
    name: str
    family: str
    algorithm: str
    direction: SignalDirection
    score: float
    weight: float
    status: SignalStatus
    evidence: str
    data_source: str
    cluster: str = ""
    extra: dict | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["cluster_label"] = _cluster_label(self.cluster)
        if self.extra is None:
            data.pop("extra", None)
        return data


def compute_strategy_matrix(snapshot: StockEdgeSnapshot) -> dict:
    matrix_params = snapshot.ctx.params.get("strategy_matrix", {})
    daily = snapshot.daily_bars.require()
    tech = compute_technical_summary(daily)
    levels = build_support_resistance(daily)
    support = nearest_support(levels, tech.close)
    resistance = nearest_resistance(levels, tech.close)
    signals = [
        _trend_signal(tech, matrix_params),
        _support_pullback_signal(tech, support, matrix_params),
        _breakout_pressure_signal(tech, resistance, matrix_params),
        _momentum_signal(tech, matrix_params),
        _volume_confirmation_signal(snapshot, matrix_params),
        _volatility_structure_signal(tech, matrix_params),
        _liquidity_slippage_signal(snapshot, matrix_params),
        _range_position_signal(snapshot, matrix_params),
        _volatility_contraction_signal(snapshot, matrix_params),
        _drawdown_recovery_signal(snapshot, matrix_params),
        _gap_risk_signal(snapshot, matrix_params),
        _gap_risk_open_model_signal(snapshot, matrix_params),
        _auction_imbalance_proxy_signal(snapshot, matrix_params),
        _trend_quality_signal(snapshot, matrix_params),
        _candle_reversal_signal(snapshot, matrix_params),
        _volume_price_divergence_signal(snapshot, matrix_params),
        _historical_replay_signal(snapshot, matrix_params),
        _target_stop_replay_signal(snapshot, matrix_params),
        _entry_fill_replay_signal(snapshot, matrix_params),
        _entry_fill_classifier_signal(snapshot, matrix_params),
        _quantile_return_forecaster_signal(snapshot, matrix_params),
        _conformal_return_band_signal(snapshot, matrix_params),
        _stop_first_classifier_signal(snapshot, matrix_params),
        _isotonic_score_calibrator_signal(snapshot, matrix_params),
        _right_tail_meta_gbm_signal(snapshot, matrix_params),
        _temporal_sequence_ranker_signal(snapshot, matrix_params),
        _target_stop_survival_signal(snapshot, matrix_params),
        _stop_loss_hazard_signal(snapshot, matrix_params),
        _multi_horizon_target_signal(snapshot, matrix_params),
        _target_ladder_probability_signal(snapshot, matrix_params),
        _path_shape_mixture_signal(snapshot, matrix_params),
        _mfe_mae_surface_signal(snapshot, matrix_params),
        _forward_entry_timing_signal(snapshot, matrix_params),
        _entry_price_surface_signal(snapshot, matrix_params),
        _pullback_rebound_ml_signal(snapshot, matrix_params),
        _squeeze_breakout_ml_signal(snapshot, matrix_params),
        _moneyflow_signal(snapshot),
        _orderflow_mix_signal(snapshot, matrix_params),
        _northbound_regime_signal(snapshot, matrix_params),
        _market_margin_impulse_signal(snapshot, matrix_params),
        _block_trade_pressure_signal(snapshot, matrix_params),
        _lhb_institution_hotmoney_signal(snapshot, matrix_params),
        _flow_persistence_signal(snapshot, matrix_params),
        _limit_up_microstructure_signal(snapshot, matrix_params),
        _limit_up_event_path_signal(snapshot, matrix_params),
        _event_catalyst_llm_signal(snapshot, matrix_params),
        _regime_adaptive_weight_signal(snapshot, matrix_params),
        _smartmoney_sw_l2_signal(snapshot, matrix_params),
        _sector_diffusion_signal(snapshot, matrix_params),
        _same_sector_leadership_signal(snapshot, matrix_params),
        _peer_relative_momentum_signal(snapshot, matrix_params),
        _peer_fundamental_spread_signal(snapshot, matrix_params),
        _peer_financial_alpha_signal(snapshot, matrix_params),
        _hierarchical_sector_shrinkage_signal(snapshot, matrix_params),
        _daily_basic_style_signal(snapshot, matrix_params),
        _fundamental_lineup_signal(snapshot),
        _fundamental_price_dislocation_signal(snapshot, matrix_params),
        _smartmoney_sector_ml_signal(snapshot, matrix_params),
        _ningbo_active_ml_signal(snapshot, matrix_params),
        _kronos_pattern_signal(snapshot),
        _kronos_analog_signal(snapshot, matrix_params),
        _kronos_path_cluster_transition_signal(snapshot, matrix_params),
        _peer_research_auto_trigger_signal(snapshot, matrix_params),
        _fundamental_contradiction_llm_signal(snapshot, matrix_params),
        _strategy_validation_decay_signal(snapshot, matrix_params),
        _llm_regime_cache_signal(snapshot, matrix_params),
        _llm_counterfactual_cache_signal(snapshot, matrix_params),
        _intraday_availability_signal(snapshot, matrix_params),
        _volume_profile_support_signal(snapshot, matrix_params),
        _vwap_reclaim_execution_signal(snapshot, matrix_params),
        _t0_uplift_signal(snapshot, matrix_params),
    ]
    signals.extend(_ta_family_signals(snapshot, matrix_params))
    signals.append(_position_sizing_model_signal(signals, snapshot, matrix_params))
    signals.append(_model_stack_blender_signal(signals, matrix_params))
    signals = [_apply_param_weights(signal, matrix_params, snapshot) for signal in signals]
    weighted = sum(s.score * s.weight for s in signals if s.status != "missing")
    weights = sum(s.weight for s in signals if s.status != "missing")
    raw = weighted / weights if weights else 0.0
    aggregate = matrix_params.get("aggregate", {})
    base_score = float(aggregate.get("base_score", 0.5))
    raw_edge_scale = float(aggregate.get("raw_edge_scale", 0.5))
    aggregate_score = max(0.0, min(1.0, base_score + raw * raw_edge_scale))
    positives = sum(1 for s in signals if s.direction == "positive")
    negatives = sum(1 for s in signals if s.direction == "negative")
    return {
        "model_version": str(matrix_params.get("model_version", "heuristic_v0")),
        "aggregate_score": round(aggregate_score, 4),
        "raw_edge": round(raw, 4),
        "positive_count": positives,
        "negative_count": negatives,
        "cluster_plans": _cluster_trade_plans(
            signals,
            close=tech.close,
            support=support,
            resistance=resistance,
            params=matrix_params,
            risk_params=snapshot.ctx.params.get("risk", {}),
        ),
        "covered_ta_families": sorted(
            {
                s.key.removeprefix("ta_family_")
                for s in signals
                if s.key.startswith("ta_family_") and s.status != "missing"
            }
        ),
        "signals": [s.to_dict() for s in signals],
    }


def _cluster_trade_plans(
    signals: list[StrategySignal],
    *,
    close: float,
    support,
    resistance,
    params: dict,
    risk_params: dict,
) -> list[dict]:
    """Aggregate signals into a small set of strategy clusters.

    The cluster score remains continuous. The action label is a presentation
    layer so users can read the report quickly without losing the underlying
    parameter surface used for future tuning.
    """
    order = [
        "trend_breakout",
        "pullback_continuation",
        "reversal_mean_reversion",
        "order_flow_smart_money",
        "sw_l2_sector_leadership",
        "fundamentals_quality",
        "model_ensemble",
        "intraday_t0_execution",
        "risk_warning",
    ]
    thresholds = params.get("aggregate", {})
    buy_threshold = (float(thresholds.get("buy_threshold", 0.68)) - 0.5) / 0.5
    watch_threshold = (float(thresholds.get("watch_threshold", 0.50)) - 0.5) / 0.5
    right_tail = float(risk_params.get("right_tail_target_pct", 50.0))
    out: list[dict] = []
    for cluster in order:
        cluster_signals = [s for s in signals if s.cluster == cluster and s.status != "missing"]
        if not cluster_signals:
            continue
        weight_sum = sum(s.weight for s in cluster_signals)
        raw = sum(s.score * s.weight for s in cluster_signals) / weight_sum if weight_sum else 0.0
        score = _clip(raw, -1.0, 1.0)
        plan = _cluster_price_plan(cluster, score, close, support, resistance, right_tail)
        action = _cluster_action(cluster, score, buy_threshold, watch_threshold)
        out.append(
            {
                "cluster": cluster,
                "cluster_label": _cluster_label(cluster),
                "score": round(score, 4),
                "display_score": round(0.5 + score * 0.5, 4),
                "action": action,
                "action_label": {"buy": "可交易", "watch": "观察", "avoid": "回避", "risk": "风险优先"}.get(action, action),
                "entry_low": plan["entry_low"],
                "entry_high": plan["entry_high"],
                "target_price": plan["target_price"],
                "target_return_pct": plan["target_return_pct"],
                "stop_price": plan["stop_price"],
                "holding_days": "20-40",
                "evidence": "；".join(s.evidence for s in cluster_signals[:3]),
                "top_signals": [s.name for s in sorted(cluster_signals, key=lambda s: abs(s.score * s.weight), reverse=True)[:4]],
            }
        )
    return out


def _cluster_price_plan(cluster: str, score: float, close: float, support, resistance, right_tail: float) -> dict:
    support_price = float(support.price) if support is not None else close * 0.95
    resistance_price = float(resistance.price) if resistance is not None else close * 1.12
    if cluster == "trend_breakout":
        entry_low = close * 0.995
        entry_high = min(close * 1.018, max(close, resistance_price * 1.006))
        stop = max(support_price * 0.985, close * 0.90)
    elif cluster == "pullback_continuation":
        entry_low = support_price * 0.995
        entry_high = min(close, support_price * 1.025)
        stop = support_price * 0.965
    elif cluster == "reversal_mean_reversion":
        entry_low = support_price * 0.980
        entry_high = support_price * 1.015
        stop = support_price * 0.945
    elif cluster == "intraday_t0_execution":
        entry_low = close * 0.985
        entry_high = close * 1.005
        stop = close * 0.965
    elif cluster == "risk_warning":
        entry_low = close * 0.0
        entry_high = close * 0.0
        stop = max(support_price * 0.965, close * 0.88)
    else:
        entry_low = max(support_price * 0.995, close * 0.970)
        entry_high = min(close * 1.010, max(close, support_price * 1.035))
        stop = min(support_price * 0.965, close * 0.92)
    target_floor = max(resistance_price, entry_high * (1.0 + right_tail / 100.0))
    # Positive cluster score moves the operational target toward the right-tail target;
    # weak score keeps it closer to the first visible resistance.
    target = resistance_price + max(score, 0.0) * (target_floor - resistance_price)
    if cluster == "risk_warning":
        target = 0.0
    base = entry_high if entry_high > 0 else close
    target_return = (target / base - 1.0) * 100.0 if target > 0 and base > 0 else None
    return {
        "entry_low": round(entry_low, 4) if entry_low > 0 else None,
        "entry_high": round(entry_high, 4) if entry_high > 0 else None,
        "target_price": round(target, 4) if target > 0 else None,
        "target_return_pct": round(target_return, 2) if target_return is not None else None,
        "stop_price": round(stop, 4) if stop > 0 else None,
    }


def _cluster_action(cluster: str, score: float, buy_threshold: float, watch_threshold: float) -> str:
    if cluster == "risk_warning":
        return "risk" if score <= -abs(watch_threshold) else "watch"
    if score >= buy_threshold:
        return "buy"
    if score >= watch_threshold:
        return "watch"
    return "avoid"


def _trend_signal(tech, params: dict) -> StrategySignal:
    if tech.ma20 is None or tech.ma60 is None:
        return StrategySignal("trend_following", "趋势样本不足", "规则", "均线距离连续评分", "neutral", 0.0, 0.80, "degraded", "均线样本不足。", "smartmoney.raw_daily")
    close_vs_ma20 = (tech.close / tech.ma20 - 1.0) * 100.0
    ma20_vs_ma60 = (tech.ma20 / tech.ma60 - 1.0) * 100.0 if tech.ma60 else 0.0
    score = 0.42 * _tanh_scaled(close_vs_ma20, 3.0) + 0.38 * _tanh_scaled(ma20_vs_ma60, 3.0)
    score = _clip(score, -0.70, 0.70)
    direction = _direction(score, params)
    name = "趋势延续" if direction == "positive" else ("弱趋势" if direction == "negative" else "趋势中性")
    return StrategySignal(
        "trend_following",
        name,
        "规则",
        "MA20/MA60 距离连续评分",
        direction,
        score,
        1.15,
        "active",
        f"收盘/MA20 {close_vs_ma20:+.2f}%，MA20/MA60 {ma20_vs_ma60:+.2f}%。",
        "smartmoney.raw_daily",
    )


def _support_pullback_signal(tech, support, params: dict) -> StrategySignal:
    if support is None:
        return StrategySignal("support_pullback", "支撑回踩", "规则", "最近支撑距离", "neutral", 0.0, 0.95, "missing", "缺少可用支撑锚。", "support_resistance")
    distance = abs(float(support.distance_pct))
    smooth = _smooth(params)
    mid = float(smooth.get("support_distance_mid_pct", 8.0))
    scale = float(smooth.get("support_distance_scale_pct", 3.0))
    amp = float(smooth.get("support_score_amplitude", 0.65))
    score = -amp * math.tanh((distance - mid) / max(scale, 1e-6))
    direction = _direction(score, params)
    name = "支撑附近" if direction == "positive" else ("远离支撑" if direction == "negative" else "支撑距离中性")
    return StrategySignal(
        "support_pullback",
        name,
        "规则",
        "支撑距离平滑衰减",
        direction,
        _clip(score, -0.65, 0.65),
        0.95,
        "active",
        f"最近支撑 {support.price:.2f}，距现价 {support.distance_pct:+.2f}%。",
        "support_resistance",
    )


def _breakout_pressure_signal(tech, resistance, params: dict) -> StrategySignal:
    if resistance is None:
        return StrategySignal("breakout_pressure", "压力突破", "规则", "最近压力距离", "positive", 0.25, 0.80, "degraded", "上方暂无明确压力位。", "support_resistance")
    distance = float(resistance.distance_pct)
    smooth = _smooth(params)
    scale = float(smooth.get("breakout_distance_scale_pct", 6.0))
    amp = float(smooth.get("breakout_score_amplitude", 0.45))
    score = amp * math.exp(-max(distance, 0.0) / max(scale, 1e-6))
    direction = _direction(score, params)
    name = "临近压力" if direction == "positive" else "压力距离中性"
    return StrategySignal("breakout_pressure", name, "统计", "压力距离指数衰减", direction, _clip(score, 0.0, 0.45), 0.85, "active", f"最近压力 {resistance.price:.2f}，距现价 {distance:+.2f}%。", "support_resistance")


def _momentum_signal(tech, params: dict) -> StrategySignal:
    ret = tech.return_5d_pct
    if ret is None:
        return StrategySignal("momentum_5d", "5日动量", "统计", "5日收益率", "neutral", 0.0, 0.85, "missing", "5日收益率不可用。", "smartmoney.raw_daily")
    smooth = _smooth(params)
    center = float(smooth.get("momentum_center_pct", 6.0))
    width = float(smooth.get("momentum_width_pct", 8.0))
    overheat_center = float(smooth.get("momentum_overheat_center_pct", 20.0))
    overheat_scale = float(smooth.get("momentum_overheat_scale_pct", 4.0))
    weak_center = float(smooth.get("momentum_weak_center_pct", -5.0))
    weak_scale = float(smooth.get("momentum_weak_scale_pct", 4.0))
    healthy = 0.50 * math.exp(-((ret - center) / max(width, 1e-6)) ** 2)
    overheat_penalty = 0.45 * _sigmoid((ret - overheat_center) / max(overheat_scale, 1e-6))
    weak_penalty = 0.45 * _sigmoid((weak_center - ret) / max(weak_scale, 1e-6))
    score = _clip(healthy - overheat_penalty - weak_penalty, -0.50, 0.50)
    direction = _direction(score, params)
    name = "健康动量" if direction == "positive" else ("动量风险" if direction == "negative" else "动量中性")
    return StrategySignal("momentum_5d", name, "统计", "5日收益率连续收益曲线", direction, score, 0.95, "active", f"5日涨跌幅 {ret:+.2f}%。", "smartmoney.raw_daily")


def _moneyflow_signal(snapshot: StockEdgeSnapshot) -> StrategySignal:
    df = snapshot.moneyflow.data
    if df is None or df.empty or "net_mf_amount" not in df.columns:
        return StrategySignal("moneyflow_7d", "资金流确认", "资金", "7日主力净流", "neutral", 0.0, 0.95, "missing", "本地资金流不可用。", "smartmoney.raw_moneyflow")
    net_sum = float(pd.to_numeric(df["net_mf_amount"], errors="coerce").tail(7).sum())
    scale = float(_smooth(snapshot.ctx.params.get("strategy_matrix", {})).get("stock_moneyflow_scale_wan", 10000.0))
    score = 0.45 * _tanh_scaled(net_sum, scale)
    direction = _direction(score, snapshot.ctx.params.get("strategy_matrix", {}))
    name = "资金净流入" if direction == "positive" else ("资金净流出" if direction == "negative" else "资金中性")
    return StrategySignal("moneyflow_7d", name, "资金", "7日主力净流连续评分", direction, score, 1.05, "active", f"近7条资金流合计 {net_sum:.2f}。", "smartmoney.raw_moneyflow")


def _volume_confirmation_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    df = snapshot.daily_bars.data
    if df is None or df.empty or "amount" not in df.columns or len(df) < 20:
        return StrategySignal("volume_confirmation", "量能确认", "统计", "5日/20日成交额连续比值", "neutral", 0.0, 0.80, "missing", "成交额样本不足。", "smartmoney.raw_daily")
    amounts = pd.to_numeric(df.sort_values("trade_date")["amount"], errors="coerce")
    avg5 = float(amounts.tail(5).mean())
    avg20 = float(amounts.tail(20).mean())
    if avg20 <= 0:
        return StrategySignal("volume_confirmation", "量能确认", "统计", "5日/20日成交额连续比值", "neutral", 0.0, 0.80, "missing", "20日成交额均值不可用。", "smartmoney.raw_daily")
    ratio = avg5 / avg20
    score = 0.32 * _tanh_scaled(math.log(max(ratio, 1e-6)), 0.35)
    direction = _direction(score, params)
    name = "量能放大" if direction == "positive" else ("量能收缩" if direction == "negative" else "量能中性")
    return StrategySignal("volume_confirmation", name, "统计", "5日/20日成交额连续比值", direction, score, 0.85, "active", f"5日/20日成交额比 {ratio:.2f}。", "smartmoney.raw_daily")


def _volatility_structure_signal(tech, params: dict) -> StrategySignal:
    if tech.atr14 is None or tech.close <= 0:
        return StrategySignal("volatility_structure", "波动结构", "风险", "ATR/价格连续评分", "neutral", 0.0, 0.75, "missing", "ATR 样本不足。", "smartmoney.raw_daily")
    atr_pct = tech.atr14 / tech.close * 100.0
    # 20-40d swing交易需要波动，但极端波动通常意味着止损质量变差。
    comfort = 5.0
    width = 4.0
    high_vol_penalty = 0.36 * _sigmoid((atr_pct - 12.0) / 4.0)
    tradable_vol_bonus = 0.18 * math.exp(-((atr_pct - comfort) / width) ** 2)
    score = _clip(tradable_vol_bonus - high_vol_penalty, -0.36, 0.20)
    direction = _direction(score, params)
    name = "波动适中" if direction == "positive" else ("高波动风险" if direction == "negative" else "波动中性")
    return StrategySignal("volatility_structure", name, "风险", "ATR/价格连续评分", direction, score, 0.80, "active", f"ATR14/收盘价 {atr_pct:.2f}%。", "smartmoney.raw_daily")


def _liquidity_slippage_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    liquidity_params = params.get("liquidity_slippage") or {}
    profile = build_liquidity_slippage_profile(
        snapshot.daily_bars.require(),
        snapshot.daily_basic.data if snapshot.daily_basic else None,
        params=liquidity_params,
    )
    if not profile.available:
        return StrategySignal(
            "liquidity_slippage",
            "流动性/滑点风险",
            "风险",
            "capacity/slippage continuous score",
            "neutral",
            0.0,
            0.75,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily / raw_daily_basic",
        )
    direction = _direction(profile.score, params)
    name = "流动性友好" if direction == "positive" else ("滑点/容量风险" if direction == "negative" else "流动性中性")
    return StrategySignal(
        "liquidity_slippage",
        name,
        "风险",
        "capacity/slippage continuous score",
        direction,
        profile.score,
        0.75,
        "active",
        f"20日均成交额 {profile.avg_amount_yuan / 1e8:.2f} 亿，估算滑点 {profile.estimated_slippage_bps:.1f}bp，容量分 {profile.capacity_score:.2f}。",
        "smartmoney.raw_daily / raw_daily_basic",
        extra=profile.to_dict(),
    )


def _range_position_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    df = snapshot.daily_bars.data
    if df is None or df.empty or not {"high", "low", "close"}.issubset(df.columns) or len(df) < 40:
        return StrategySignal("range_position", "区间位置", "统计", "60日位置连续评分", "neutral", 0.0, 0.70, "missing", "区间样本不足。", "smartmoney.raw_daily")
    tail = df.sort_values("trade_date").tail(min(len(df), 60))
    high = float(pd.to_numeric(tail["high"], errors="coerce").max())
    low = float(pd.to_numeric(tail["low"], errors="coerce").min())
    close = float(pd.to_numeric(tail["close"], errors="coerce").iloc[-1])
    if high <= low:
        return StrategySignal("range_position", "区间位置", "统计", "60日位置连续评分", "neutral", 0.0, 0.70, "missing", "60日高低点不可用。", "smartmoney.raw_daily")
    pos = (close - low) / (high - low)
    # Favor upper-middle structure: enough strength, but not fully extended.
    score = 0.34 * math.exp(-((pos - 0.68) / 0.23) ** 2) - 0.18 * _sigmoid((pos - 0.94) / 0.04)
    direction = _direction(score, params)
    name = "区间强势但未极端" if direction == "positive" else ("逼近区间极值" if direction == "negative" else "区间位置中性")
    return StrategySignal("range_position", name, "统计", "60日位置连续评分", direction, _clip(score, -0.22, 0.34), 0.70, "active", f"收盘位于60日区间 {pos:.1%} 分位。", "smartmoney.raw_daily")


def _volatility_contraction_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    df = snapshot.daily_bars.data
    required = {"high", "low", "close"}
    if df is None or df.empty or not required.issubset(df.columns) or len(df) < 30:
        return StrategySignal("volatility_contraction", "波动收敛", "统计", "真实波幅收敛连续评分", "neutral", 0.0, 0.70, "missing", "波动样本不足。", "smartmoney.raw_daily")
    ordered = df.sort_values("trade_date").copy()
    high = pd.to_numeric(ordered["high"], errors="coerce")
    low = pd.to_numeric(ordered["low"], errors="coerce")
    close = pd.to_numeric(ordered["close"], errors="coerce")
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    short = float(tr.tail(5).mean())
    long = float(tr.tail(30).mean())
    if long <= 0:
        return StrategySignal("volatility_contraction", "波动收敛", "统计", "真实波幅收敛连续评分", "neutral", 0.0, 0.70, "missing", "长期波幅不可用。", "smartmoney.raw_daily")
    ratio = short / long
    score = 0.26 * math.exp(-((ratio - 0.72) / 0.22) ** 2) - 0.20 * _sigmoid((ratio - 1.45) / 0.22)
    direction = _direction(score, params)
    name = "波动收敛蓄势" if direction == "positive" else ("波动扩张风险" if direction == "negative" else "波动收敛中性")
    return StrategySignal("volatility_contraction", name, "统计", "5日/30日真实波幅比", direction, _clip(score, -0.24, 0.28), 0.72, "active", f"5日/30日真实波幅比 {ratio:.2f}。", "smartmoney.raw_daily")


def _drawdown_recovery_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    df = snapshot.daily_bars.data
    if df is None or df.empty or not {"high", "close"}.issubset(df.columns) or len(df) < 30:
        return StrategySignal("drawdown_recovery", "回撤修复", "统计", "20日回撤修复连续评分", "neutral", 0.0, 0.65, "missing", "回撤样本不足。", "smartmoney.raw_daily")
    tail = df.sort_values("trade_date").tail(min(len(df), 60))
    close = pd.to_numeric(tail["close"], errors="coerce")
    high20 = float(pd.to_numeric(tail["high"], errors="coerce").tail(20).max())
    low20 = float(pd.to_numeric(tail["close"], errors="coerce").tail(20).min())
    latest = float(close.iloc[-1])
    drawdown = (latest / high20 - 1.0) * 100 if high20 else 0.0
    recovery = (latest / low20 - 1.0) * 100 if low20 else 0.0
    healthy = 0.24 * math.exp(-((drawdown + 8.0) / 8.0) ** 2)
    deep_penalty = 0.28 * _sigmoid((abs(drawdown) - 25.0) / 5.0)
    score = healthy + 0.10 * _tanh_scaled(recovery, 12.0) - deep_penalty
    direction = _direction(score, params)
    name = "回撤修复" if direction == "positive" else ("深回撤风险" if direction == "negative" else "回撤中性")
    return StrategySignal("drawdown_recovery", name, "统计", "20日回撤修复连续评分", direction, _clip(score, -0.30, 0.32), 0.68, "active", f"距20日高点 {drawdown:+.2f}%，相对20日低点修复 {recovery:+.2f}%。", "smartmoney.raw_daily")


def _gap_risk_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    df = snapshot.daily_bars.data
    if df is None or df.empty or not {"open", "close"}.issubset(df.columns) or len(df) < 6:
        return StrategySignal("gap_risk", "跳空风险", "风险", "5日跳空连续评分", "neutral", 0.0, 0.70, "missing", "跳空样本不足。", "smartmoney.raw_daily")
    ordered = df.sort_values("trade_date").copy()
    close = pd.to_numeric(ordered["close"], errors="coerce")
    open_ = pd.to_numeric(ordered["open"], errors="coerce")
    gaps = (open_ / close.shift(1) - 1.0) * 100.0
    recent = gaps.tail(5).dropna()
    if recent.empty:
        return StrategySignal("gap_risk", "跳空风险", "风险", "5日跳空连续评分", "neutral", 0.0, 0.70, "missing", "跳空数据不可用。", "smartmoney.raw_daily")
    max_abs = float(recent.abs().max())
    avg_abs = float(recent.abs().mean())
    score = -0.26 * _sigmoid((max_abs - 6.0) / 1.5) - 0.12 * _sigmoid((avg_abs - 3.0) / 1.0)
    direction = _direction(score, params)
    name = "跳空可控" if direction == "neutral" else "跳空执行风险"
    return StrategySignal("gap_risk", name, "风险", "5日跳空连续评分", direction, _clip(score, -0.35, 0.0), 0.70, "active", f"近5日最大跳空 {max_abs:.2f}%，平均跳空 {avg_abs:.2f}%。", "smartmoney.raw_daily")


def _gap_risk_open_model_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_gap_risk_open_model(
        snapshot.daily_bars.require(),
        params=params.get("gap_risk_open_model") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "gap_risk_open_model",
            "开盘跳空风险模型",
            "ML",
            "next-open adverse gap random forest",
            "neutral",
            0.0,
            0.58,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    probability = float(profile.probability or 0.0)
    base = float(profile.positive_rate or 0.0)
    extra = profile.extra or {}
    latest_gap = float(extra.get("latest_gap_pct") or 0.0)
    latest_ret = float(extra.get("latest_intraday_ret_pct") or 0.0)
    score = -(
        0.52 * _tanh_scaled((probability - base) * 100.0, 15.0)
        + 0.24 * _tanh_scaled((probability - 0.30) * 100.0, 18.0)
        + 0.14 * _tanh_scaled(abs(latest_gap) - 3.0, 2.0)
        - 0.10 * _tanh_scaled(latest_ret, 5.0)
    )
    direction = _direction(score, params)
    return StrategySignal(
        "gap_risk_open_model",
        "次日跳空风险偏高" if direction == "negative" else ("次日跳空风险可控" if direction == "positive" else "次日跳空风险中性"),
        "ML",
        "next-open adverse gap random forest",
        direction,
        _clip(score, -0.40, 0.24),
        0.58,
        "active",
        f"{profile.sample_count} 个开盘标签；不利跳空概率 {probability:.1%}，历史基准 {base:.1%}，最新跳空 {latest_gap:+.2f}%，当日承接 {latest_ret:+.2f}%。",
        "smartmoney.raw_daily / sklearn.ensemble",
        extra=profile.to_dict(),
    )


def _price_action_profile(snapshot: StockEdgeSnapshot, params: dict):
    return build_price_action_profile(snapshot.daily_bars.require(), params=params.get("price_action") or {})


def _trend_quality_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = _price_action_profile(snapshot, params)
    if not profile.available:
        return StrategySignal(
            "trend_quality_r2",
            "趋势质量",
            "统计学习",
            "log-price regression R2/slope",
            "neutral",
            0.0,
            0.70,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    direction = _direction(profile.trend_quality_score, params)
    name = "趋势质量高" if direction == "positive" else ("趋势质量弱" if direction == "negative" else "趋势质量中性")
    return StrategySignal(
        "trend_quality_r2",
        name,
        "统计学习",
        "log-price regression R2/slope",
        direction,
        profile.trend_quality_score,
        0.72,
        "active",
        f"20日等效斜率 {profile.trend_slope_20d_pct:+.2f}%，R2 {profile.trend_r2:.2f}。",
        "smartmoney.raw_daily",
        extra=profile.to_dict(),
    )


def _candle_reversal_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = _price_action_profile(snapshot, params)
    if not profile.available:
        return StrategySignal(
            "candle_reversal_structure",
            "K线反转结构",
            "规则",
            "shadow/close-location reversal score",
            "neutral",
            0.0,
            0.65,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    direction = _direction(profile.candle_reversal_score, params)
    name = "下影反转" if direction == "positive" else ("上影派发风险" if direction == "negative" else "K线结构中性")
    return StrategySignal(
        "candle_reversal_structure",
        name,
        "规则",
        "shadow/close-location reversal score",
        direction,
        profile.candle_reversal_score,
        0.68,
        "active",
        f"收盘位置 {profile.latest_close_location:.0%}，下影 {profile.lower_shadow_ratio:.0%}，上影 {profile.upper_shadow_ratio:.0%}。",
        "smartmoney.raw_daily",
        extra=profile.to_dict(),
    )


def _volume_price_divergence_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = _price_action_profile(snapshot, params)
    if not profile.available:
        return StrategySignal(
            "volume_price_divergence",
            "量价背离",
            "统计",
            "10d price/amount divergence",
            "neutral",
            0.0,
            0.65,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    direction = _direction(profile.volume_price_divergence_score, params)
    name = "量价确认" if direction == "positive" else ("量价背离风险" if direction == "negative" else "量价中性")
    return StrategySignal(
        "volume_price_divergence",
        name,
        "统计",
        "10d price/amount divergence",
        direction,
        profile.volume_price_divergence_score,
        0.66,
        "active",
        f"10日价格趋势 {profile.price_trend_10d_pct:+.2f}%，成交额趋势 {profile.amount_trend_10d_pct:+.2f}%。",
        "smartmoney.raw_daily",
        extra=profile.to_dict(),
    )


def _auction_imbalance_proxy_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    df = snapshot.daily_bars.data
    if df is None or df.empty or len(df) < 2:
        return StrategySignal("auction_imbalance_proxy", "开盘失衡代理", "执行", "open-gap and close-retention proxy", "neutral", 0.0, 0.48, "missing", "日线开盘/收盘样本不足。", "smartmoney.raw_daily")
    ordered = df.sort_values("trade_date").reset_index(drop=True)
    latest = ordered.iloc[-1]
    prev = ordered.iloc[-2]
    close_prev = _as_float(prev.get("close"))
    open_price = _as_float(latest.get("open"))
    close = _as_float(latest.get("close"))
    high = _as_float(latest.get("high"))
    low = _as_float(latest.get("low"))
    if not close_prev or not open_price or not close or not high or not low:
        return StrategySignal("auction_imbalance_proxy", "开盘失衡代理", "执行", "open-gap and close-retention proxy", "neutral", 0.0, 0.48, "degraded", "开盘失衡字段不完整。", "smartmoney.raw_daily")
    gap_pct = (open_price / close_prev - 1.0) * 100.0
    retention_pct = (close / open_price - 1.0) * 100.0
    day_pos = (close - low) / max(high - low, 1e-6)
    chase_penalty = max(0.0, gap_pct - float((params.get("auction_imbalance_proxy") or {}).get("gap_chase_threshold_pct", 5.0)))
    score = (
        0.42 * _tanh_scaled(retention_pct, 3.0)
        + 0.25 * _tanh_scaled(gap_pct, 4.5)
        + 0.20 * (day_pos * 2.0 - 1.0)
        - 0.30 * _tanh_scaled(chase_penalty, 3.0)
    )
    score = _clip(score, -0.40, 0.35)
    direction = _direction(score, params)
    name = "开盘承接较强" if direction == "positive" else ("高开回落/竞价失衡" if direction == "negative" else "开盘结构中性")
    return StrategySignal(
        "auction_imbalance_proxy",
        name,
        "执行",
        "open-gap and close-retention proxy",
        direction,
        score,
        0.48,
        "active",
        f"开盘跳空 {gap_pct:+.2f}%，收盘相对开盘 {retention_pct:+.2f}%，日内位置 {day_pos:.0%}。",
        "smartmoney.raw_daily",
        extra={"gap_pct": round(gap_pct, 4), "retention_pct": round(retention_pct, 4), "day_position": round(day_pos, 4)},
    )


def _historical_replay_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    replay_params = params.get("historical_replay") or {}
    stats = build_historical_replay_stats(
        snapshot.daily_bars.require(),
        replay_params=replay_params,
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not stats.available:
        return StrategySignal(
            "historical_replay_edge",
            "历史相似形态 replay",
            "统计学习",
            "single-stock analog replay",
            "neutral",
            0.0,
            0.80,
            "degraded",
            stats.reason,
            "smartmoney.raw_daily",
        )
    probability = float(stats.best_probability or 0.0)
    expected_value = float(stats.best_expected_value or 0.0)
    sample_factor = min(1.0, stats.analog_count / max(float(replay_params.get("max_analogs", 24)), 1.0))
    score = sample_factor * (0.65 * _tanh_scaled(expected_value * 100.0, 8.0) + 0.35 * _tanh_scaled((probability - 0.30) * 100.0, 18.0))
    direction = _direction(score, params)
    name = "历史相似形态顺风" if direction == "positive" else ("历史相似形态逆风" if direction == "negative" else "历史 replay 中性")
    return StrategySignal(
        "historical_replay_edge",
        name,
        "统计学习",
        "single-stock analog replay",
        direction,
        _clip(score, -0.42, 0.45),
        0.80,
        "active",
        f"{stats.analog_count} 个相似历史片段；最佳 {stats.best_label or '—'} 命中率 {probability:.1%}，期望值 {expected_value:.1%}，相似度 {stats.avg_similarity or 0:.2f}。",
        "smartmoney.raw_daily",
        extra=stats.to_dict(),
    )


def _target_stop_replay_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    replay_params = params.get("target_stop_replay") or {}
    stats = build_target_stop_replay_stats(
        snapshot.daily_bars.require(),
        params=replay_params,
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not stats.available:
        return StrategySignal(
            "target_stop_replay",
            "目标/止损路径 replay",
            "统计学习",
            "target/stop first-event replay",
            "neutral",
            0.0,
            0.72,
            "degraded",
            stats.reason,
            "smartmoney.raw_daily",
        )
    best = stats.target_stats[0] if stats.target_stats else {}
    target_rate = float(best.get("target_first_rate") or 0.0)
    stop_rate = float(best.get("stop_first_rate") or 0.0)
    expected_value = float(best.get("expected_value") or 0.0)
    time_efficiency = 0.0
    if best.get("avg_days_to_target") is not None and best.get("horizon_days"):
        time_efficiency = 1.0 - float(best["avg_days_to_target"]) / max(float(best["horizon_days"]), 1.0)
    score = (
        0.52 * _tanh_scaled(expected_value * 100.0, 7.0)
        + 0.32 * _tanh_scaled((target_rate - stop_rate) * 100.0, 25.0)
        + 0.16 * _clip(time_efficiency, -1.0, 1.0)
    )
    score = _clip(score, -0.42, 0.45)
    direction = _direction(score, params)
    name = "目标先触发占优" if direction == "positive" else ("止损先触发风险" if direction == "negative" else "路径胜率中性")
    avg_target = best.get("avg_days_to_target")
    avg_stop = best.get("avg_days_to_stop")
    return StrategySignal(
        "target_stop_replay",
        name,
        "统计学习",
        "target/stop first-event replay",
        direction,
        score,
        0.72,
        "active",
        f"{stats.best_label or '最佳目标'}：目标先到 {target_rate:.1%}，止损先到 {stop_rate:.1%}，目标均 {avg_target or '—'} 天，止损均 {avg_stop or '—'} 天。",
        "smartmoney.raw_daily",
        extra=stats.to_dict(),
    )


def _entry_fill_replay_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    fill_params = params.get("entry_fill_replay") or {}
    stats = build_entry_fill_replay_stats(snapshot.daily_bars.require(), params=fill_params)
    if not stats.available:
        return StrategySignal(
            "entry_fill_replay",
            "入场成交 replay",
            "统计学习",
            "support/ATR entry fill replay",
            "neutral",
            0.0,
            0.65,
            "degraded",
            stats.reason,
            "smartmoney.raw_daily",
        )
    fill_rate = float(stats.fill_rate or 0.0)
    clean = float(stats.clean_fill_rate or 0.0)
    stop = float(stats.stop_before_fill_rate or 0.0)
    clean_weight = float(fill_params.get("clean_fill_weight", 0.70))
    fill_weight = float(fill_params.get("fill_rate_weight", 0.30))
    stop_penalty = float(fill_params.get("stop_penalty_weight", 0.80))
    raw = clean_weight * (clean - 0.35) + fill_weight * (fill_rate - 0.45) - stop_penalty * stop
    score = _clip(raw * 1.4, -0.35, 0.35)
    direction = _direction(score, params)
    name = "入场成交友好" if direction == "positive" else ("入场先破位风险" if direction == "negative" else "入场成交中性")
    return StrategySignal(
        "entry_fill_replay",
        name,
        "统计学习",
        "support/ATR entry fill replay",
        direction,
        score,
        0.65,
        "active",
        f"历史 {stats.sample_count} 个入场样本；成交率 {fill_rate:.1%}，干净成交 {clean:.1%}，先破位 {stop:.1%}。",
        "smartmoney.raw_daily",
        extra=stats.to_dict(),
    )


def _entry_fill_classifier_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    fill_params = params.get("entry_fill_classifier") or params.get("entry_fill_replay") or {}
    stats = build_entry_fill_replay_stats(snapshot.daily_bars.require(), params=fill_params)
    if not stats.available:
        return StrategySignal(
            "entry_fill_classifier",
            "入场概率分类器",
            "ML",
            "logistic entry-fill classifier from replay labels",
            "neutral",
            0.0,
            0.58,
            "degraded",
            stats.reason,
            "smartmoney.raw_daily",
        )
    fill_rate = float(stats.fill_rate or 0.0)
    clean = float(stats.clean_fill_rate or 0.0)
    stop = float(stats.stop_before_fill_rate or 0.0)
    horizon = max(1.0, float(fill_params.get("horizon_days", 5)))
    days = float(stats.avg_days_to_fill or horizon)
    logit = 2.4 * (fill_rate - 0.45) + 2.0 * (clean - 0.35) - 2.2 * stop - 0.35 * max(0.0, days / horizon - 0.45)
    probability = _sigmoid(logit)
    score = _clip((probability - 0.50) * 0.78, -0.32, 0.36)
    direction = _direction(score, params)
    name = "未来5日成交概率高" if direction == "positive" else ("未来5日成交质量弱" if direction == "negative" else "未来5日成交概率中性")
    return StrategySignal(
        "entry_fill_classifier",
        name,
        "ML",
        "logistic entry-fill classifier from replay labels",
        direction,
        score,
        0.58,
        "active",
        f"成交概率 {probability:.1%}；replay 成交 {fill_rate:.1%}、干净成交 {clean:.1%}、先破位 {stop:.1%}、平均 {days:.1f} 日成交。",
        "smartmoney.raw_daily",
        extra={**stats.to_dict(), "predicted_fill_probability": round(probability, 4)},
    )


def _path_forecast_profile(snapshot: StockEdgeSnapshot, params: dict):
    return build_path_forecast_profile(
        snapshot.daily_bars.require(),
        params=params.get("path_forecast") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )


def _quantile_return_forecaster_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = _path_forecast_profile(snapshot, params)
    if not profile.available:
        return StrategySignal(
            "quantile_return_forecaster",
            "收益分位预测",
            "统计学习",
            "weighted historical path quantile forecast",
            "neutral",
            0.0,
            0.62,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    p50 = float(profile.p50_return or 0.0)
    p90 = float(profile.p90_return or 0.0)
    expected = float(profile.expected_return or 0.0)
    target = float(profile.return_pct or 20.0) / 100.0
    score = (
        0.44 * _tanh_scaled((p50 - 0.04) * 100.0, 10.0)
        + 0.34 * _tanh_scaled((p90 - 0.55 * target) * 100.0, 18.0)
        + 0.22 * _tanh_scaled(expected * 100.0, 12.0)
    )
    score = _clip(score, -0.42, 0.45)
    direction = _direction(score, params)
    name = "收益分位右偏" if direction == "positive" else ("收益分位左偏" if direction == "negative" else "收益分位中性")
    return StrategySignal(
        "quantile_return_forecaster",
        name,
        "统计学习",
        "weighted historical path quantile forecast",
        direction,
        score,
        0.62,
        "active",
        f"{profile.best_label or '最佳路径'}：P10 {profile.p10_return or 0:+.1%}，P50 {p50:+.1%}，P90 {p90:+.1%}，期望 {expected:+.1%}。",
        "smartmoney.raw_daily",
        extra=profile.to_dict(),
    )


def _conformal_return_band_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = _path_forecast_profile(snapshot, params)
    if not profile.available:
        return StrategySignal(
            "conformal_return_band",
            "收益置信带",
            "统计学习",
            "weighted conformal-style return band",
            "neutral",
            0.0,
            0.50,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    p10 = float(profile.p10_return or 0.0)
    p90 = float(profile.p90_return or 0.0)
    width = float(profile.conformal_width or 0.0)
    stop_distance = float(snapshot.ctx.params.get("risk", {}).get("max_stop_distance_pct", 12.0)) / 100.0
    downside_margin = p10 + 0.75 * stop_distance
    width_penalty = max(0.0, width - 0.42)
    score = _clip(0.70 * _tanh_scaled(downside_margin * 100.0, 8.0) - 0.30 * _tanh_scaled(width_penalty * 100.0, 20.0), -0.42, 0.32)
    direction = _direction(score, params)
    name = "收益置信带可控" if direction == "positive" else ("收益置信带过宽/左尾重" if direction == "negative" else "收益置信带中性")
    return StrategySignal(
        "conformal_return_band",
        name,
        "统计学习",
        "weighted conformal-style return band",
        direction,
        score,
        0.50,
        "active",
        f"{profile.best_label or '最佳路径'}：P10 {p10:+.1%}，P90 {p90:+.1%}，带宽 {width:.1%}。",
        "smartmoney.raw_daily",
        extra=profile.to_dict(),
    )


def _stop_first_classifier_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = _path_forecast_profile(snapshot, params)
    if not profile.available:
        return StrategySignal(
            "stop_first_classifier",
            "先止损概率",
            "统计学习",
            "weighted stop-first path classifier",
            "neutral",
            0.0,
            0.58,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    stop_prob = float(profile.stop_first_probability or 0.0)
    hit_prob = float(profile.right_tail_probability or 0.0)
    score = _clip(0.65 * _tanh_scaled((0.34 - stop_prob) * 100.0, 16.0) + 0.35 * _tanh_scaled((hit_prob - stop_prob) * 100.0, 22.0), -0.45, 0.35)
    direction = _direction(score, params)
    name = "先止损概率较低" if direction == "positive" else ("先止损概率偏高" if direction == "negative" else "先止损概率中性")
    return StrategySignal(
        "stop_first_classifier",
        name,
        "统计学习",
        "weighted stop-first path classifier",
        direction,
        score,
        0.58,
        "active",
        f"{profile.best_label or '最佳路径'}：目标触达 {hit_prob:.1%}，先止损 {stop_prob:.1%}，平均最大回撤 {profile.avg_max_drawdown or 0:.1%}。",
        "smartmoney.raw_daily",
        extra=profile.to_dict(),
    )


def _isotonic_score_calibrator_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    calib = _build_monotonic_score_calibration(
        snapshot.daily_bars.require(),
        snapshot.ctx.params.get("risk", {}),
        params.get("isotonic_score_calibrator") or {},
    )
    if not calib.get("available"):
        return StrategySignal(
            "isotonic_score_calibrator",
            "单调概率校准",
            "统计学习",
            "single-stock monotonic bin calibration",
            "neutral",
            0.0,
            0.58,
            "degraded",
            calib.get("reason") or "历史样本不足，无法做单调校准。",
            "smartmoney.raw_daily",
        )
    calibrated = float(calib.get("calibrated_probability") or 0.0)
    base = float(calib.get("base_hit_rate") or 0.0)
    sample_factor = min(1.0, math.sqrt(float(calib.get("sample_count") or 0.0) / 80.0))
    score = sample_factor * (0.70 * _tanh_scaled((calibrated - base) * 100.0, 12.0) + 0.30 * _tanh_scaled((calibrated - 0.18) * 100.0, 14.0))
    direction = _direction(score, params)
    return StrategySignal(
        "isotonic_score_calibrator",
        "校准概率支持当前分数" if direction == "positive" else ("校准概率压制当前分数" if direction == "negative" else "校准概率中性"),
        "统计学习",
        "single-stock monotonic bin calibration",
        direction,
        _clip(score, -0.34, 0.36),
        0.58,
        "active",
        f"{calib.get('sample_count')} 个历史标签；当前分箱校准命中率 {calibrated:.1%}，全样本基准 {base:.1%}。",
        "smartmoney.raw_daily",
        extra=calib,
    )


def _build_monotonic_score_calibration(daily: pd.DataFrame, risk_params: dict, params: dict) -> dict:
    min_samples = int(params.get("min_samples", 60))
    target_pct = float(risk_params.get("right_tail_target_pct", params.get("target_pct", 30.0))) / 100.0
    horizon = int(params.get("horizon_bars", 40))
    if daily.empty or len(daily) < max(100, min_samples + horizon + 20):
        return {"available": False, "reason": f"历史行数 {len(daily)}，低于单调校准所需样本。"}
    df = daily.copy()
    for col in ["open", "high", "low", "close", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["high", "low", "close"]).reset_index(drop=True)
    if len(df) < max(100, min_samples + horizon + 20):
        return {"available": False, "reason": "清洗后历史样本不足。"}
    df["ret_5"] = df["close"].pct_change(5).fillna(0.0)
    df["ret_20"] = df["close"].pct_change(20).fillna(0.0)
    df["range_high_60"] = df["high"].rolling(60, min_periods=20).max()
    df["range_low_60"] = df["low"].rolling(60, min_periods=20).min()
    width = (df["range_high_60"] - df["range_low_60"]).replace(0, pd.NA)
    df["range_pos"] = ((df["close"] - df["range_low_60"]) / width).fillna(0.5)
    amount = pd.to_numeric(df.get("amount", pd.Series([0.0] * len(df))), errors="coerce").fillna(0.0)
    amount_ratio = (amount / amount.rolling(20, min_periods=5).mean().replace(0, pd.NA)).fillna(1.0)
    df["raw_score"] = (
        0.35 * (df["ret_20"] / 0.18).apply(math.tanh)
        + 0.25 * (df["ret_5"] / 0.08).apply(math.tanh)
        + 0.20 * ((df["range_pos"] - 0.45) / 0.22).apply(math.tanh)
        + 0.20 * ((amount_ratio - 1.0) / 0.65).apply(math.tanh)
    )
    rows = []
    for idx in range(60, len(df) - horizon):
        entry = float(df.iloc[idx]["close"])
        future = df.iloc[idx + 1 : idx + 1 + horizon]
        if entry <= 0 or future.empty:
            continue
        rows.append({
            "raw_score": float(df.iloc[idx]["raw_score"]),
            "hit": float(future["high"].max() >= entry * (1.0 + target_pct)),
        })
    samples = pd.DataFrame(rows)
    if len(samples) < min_samples or samples["hit"].nunique() < 2:
        return {"available": False, "reason": f"有效校准标签 {len(samples)} 个，或标签缺少正负样本。"}
    samples = samples.sort_values("raw_score").reset_index(drop=True)
    bins = int(max(3, min(int(params.get("bins", 5)), len(samples) // 12)))
    samples["bin"] = pd.qcut(samples.index, q=bins, labels=False, duplicates="drop")
    grouped = samples.groupby("bin", observed=True).agg(
        score_min=("raw_score", "min"),
        score_max=("raw_score", "max"),
        hit_rate=("hit", "mean"),
        count=("hit", "size"),
    ).reset_index(drop=True)
    grouped["calibrated_hit_rate"] = grouped["hit_rate"].cummax()
    current_score = float(df.iloc[-1]["raw_score"])
    selected = grouped[(grouped["score_min"] <= current_score) & (grouped["score_max"] >= current_score)]
    if selected.empty:
        selected = grouped.tail(1) if current_score > float(grouped["score_max"].max()) else grouped.head(1)
    row = selected.iloc[0]
    return {
        "available": True,
        "sample_count": int(len(samples)),
        "target_pct": round(target_pct, 4),
        "horizon_bars": horizon,
        "current_raw_score": round(current_score, 6),
        "base_hit_rate": float(samples["hit"].mean()),
        "calibrated_probability": float(row["calibrated_hit_rate"]),
        "selected_bin_count": int(row["count"]),
        "bins": grouped[["score_min", "score_max", "hit_rate", "calibrated_hit_rate", "count"]].round(6).to_dict("records"),
    }


def _right_tail_meta_gbm_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_right_tail_meta_gbm(
        snapshot.daily_bars.require(),
        params=params.get("right_tail_meta_gbm") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "right_tail_meta_gbm",
            "右尾收益 GBM",
            "ML",
            "single-stock hist-gradient-boosting right-tail classifier",
            "neutral",
            0.0,
            0.74,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    prob = float(profile.probability or 0.0)
    base = float(profile.positive_rate or 0.0)
    auc = float(profile.oos_auc_proxy or 0.5)
    score = 0.52 * _tanh_scaled((prob - base) * 100.0, 16.0) + 0.28 * _tanh_scaled((prob - 0.22) * 100.0, 16.0) + 0.20 * _tanh_scaled((auc - 0.50) * 100.0, 16.0)
    direction = _direction(score, params)
    return StrategySignal(
        "right_tail_meta_gbm",
        "右尾 GBM 支持" if direction == "positive" else ("右尾 GBM 压制" if direction == "negative" else "右尾 GBM 中性"),
        "ML",
        "single-stock hist-gradient-boosting right-tail classifier",
        direction,
        _clip(score, -0.42, 0.44),
        0.74,
        "active",
        f"{profile.sample_count} 个训练标签；当前右尾概率 {prob:.1%}，历史正例率 {base:.1%}，OOS AUC proxy {_fmt_optional_float(auc)}。",
        "smartmoney.raw_daily / sklearn",
        extra=profile.to_dict(),
    )


def _temporal_sequence_ranker_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_temporal_sequence_ranker(
        snapshot.daily_bars.require(),
        params=params.get("temporal_fusion_sequence_ranker") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "temporal_fusion_sequence_ranker",
            "多周期序列排序",
            "DL",
            "single-stock MLP sequence ranker",
            "neutral",
            0.0,
            0.70,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    prob = float(profile.probability or 0.0)
    base = float(profile.positive_rate or 0.0)
    oos_hit = float(profile.oos_hit_rate or base)
    score = 0.46 * _tanh_scaled((prob - base) * 100.0, 16.0) + 0.34 * _tanh_scaled((oos_hit - base) * 100.0, 14.0) + 0.20 * _tanh_scaled((prob - 0.22) * 100.0, 18.0)
    direction = _direction(score, params)
    return StrategySignal(
        "temporal_fusion_sequence_ranker",
        "序列模型右尾顺风" if direction == "positive" else ("序列模型右尾不足" if direction == "negative" else "序列模型中性"),
        "DL",
        "single-stock MLP sequence ranker",
        direction,
        _clip(score, -0.40, 0.42),
        0.70,
        "active",
        f"{profile.sample_count} 个序列标签；当前右尾概率 {prob:.1%}，历史正例率 {base:.1%}，OOS 高分命中 {_fmt_optional_float(oos_hit)}。",
        "smartmoney.raw_daily / sklearn.neural_network",
        extra=profile.to_dict(),
    )


def _target_stop_survival_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_target_stop_survival_model(
        snapshot.daily_bars.require(),
        params=params.get("target_stop_survival_model") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "target_stop_survival_model",
            "目标/止损生存模型",
            "ML",
            "single-stock target-before-stop survival forest",
            "neutral",
            0.0,
            0.70,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    prob = float(profile.probability or 0.0)
    base = float(profile.positive_rate or 0.0)
    oos_hit = float(profile.oos_hit_rate or base)
    extra = profile.extra or {}
    stop_prob = float(extra.get("stop_first_probability") or max(0.0, 1.0 - prob))
    edge = prob - stop_prob
    score = (
        0.44 * _tanh_scaled(edge * 100.0, 24.0)
        + 0.34 * _tanh_scaled((prob - base) * 100.0, 16.0)
        + 0.22 * _tanh_scaled((oos_hit - base) * 100.0, 14.0)
    )
    direction = _direction(score, params)
    return StrategySignal(
        "target_stop_survival_model",
        "目标先到生存模型" if direction == "positive" else ("止损先到风险模型" if direction == "negative" else "目标/止损生存中性"),
        "ML",
        "single-stock target-before-stop survival forest",
        direction,
        _clip(score, -0.42, 0.44),
        0.70,
        "active",
        f"{profile.sample_count} 个路径标签；目标先到概率 {prob:.1%}，止损代理 {stop_prob:.1%}，历史目标先到 {base:.1%}，OOS 高分命中 {_fmt_optional_float(oos_hit)}。",
        "smartmoney.raw_daily / sklearn.ensemble",
        extra=profile.to_dict(),
    )


def _stop_loss_hazard_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_stop_loss_hazard_model(
        snapshot.daily_bars.require(),
        params=params.get("stop_loss_hazard_model") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "stop_loss_hazard_model",
            "止损危险率模型",
            "ML",
            "single-stock stop-loss hazard forest",
            "neutral",
            0.0,
            0.64,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    stop_prob = float(profile.probability or 0.0)
    base = float(profile.positive_rate or 0.0)
    oos_hit = float(profile.oos_hit_rate or base)
    extra = profile.extra or {}
    target_first = float(extra.get("historical_target_first_rate") or 0.0)
    score = -(
        0.52 * _tanh_scaled((stop_prob - base) * 100.0, 15.0)
        + 0.26 * _tanh_scaled((stop_prob - 0.32) * 100.0, 18.0)
        + 0.14 * _tanh_scaled((oos_hit - base) * 100.0, 15.0)
        - 0.08 * _tanh_scaled((target_first - base) * 100.0, 18.0)
    )
    direction = _direction(score, params)
    eta = extra.get("median_days_to_stop") or extra.get("avg_days_to_stop")
    eta_text = f"，历史止损中位 {float(eta):.1f} 日" if eta is not None else ""
    return StrategySignal(
        "stop_loss_hazard_model",
        "止损危险率偏高" if direction == "negative" else ("止损危险率可控" if direction == "positive" else "止损危险率中性"),
        "ML",
        "single-stock stop-loss hazard forest",
        direction,
        _clip(score, -0.42, 0.34),
        0.64,
        "active",
        f"{profile.sample_count} 个止损路径标签；当前止损危险率 {stop_prob:.1%}，历史基准 {base:.1%}，目标先到历史 {target_first:.1%}{eta_text}。",
        "smartmoney.raw_daily / sklearn.ensemble",
        extra=profile.to_dict(),
    )


def _multi_horizon_target_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_multi_horizon_target_model(
        snapshot.daily_bars.require(),
        params=params.get("multi_horizon_target_classifier") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "multi_horizon_target_classifier",
            "多目标周期模型",
            "ML",
            "multi-horizon right-tail target classifiers",
            "neutral",
            0.0,
            0.68,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    extra = profile.extra or {}
    rows = [row for row in extra.get("rows", []) if row.get("available")]
    if not rows:
        return StrategySignal(
            "multi_horizon_target_classifier",
            "多目标周期模型",
            "ML",
            "multi-horizon right-tail target classifiers",
            "neutral",
            0.0,
            0.68,
            "degraded",
            "多目标周期模型没有可用场景。",
            "smartmoney.raw_daily",
            extra=profile.to_dict(),
        )
    best = max(rows, key=lambda row: float(row.get("probability") or 0.0) - float(row.get("base_rate") or 0.0))
    probability = float(best.get("probability") or 0.0)
    base = float(best.get("base_rate") or 0.0)
    expected_proxy = float(best.get("expected_return_proxy") or 0.0)
    score = (
        0.50 * _tanh_scaled((probability - base) * 100.0, 15.0)
        + 0.34 * _tanh_scaled((expected_proxy - 7.0), 7.0)
        + 0.16 * _tanh_scaled((probability - 0.22) * 100.0, 14.0)
    )
    direction = _direction(score, params)
    summary = "；".join(
        f"{row.get('label')} {float(row.get('probability') or 0.0):.1%}/{float(row.get('base_rate') or 0.0):.1%}"
        for row in rows[:3]
    )
    return StrategySignal(
        "multi_horizon_target_classifier",
        "多目标周期右尾顺风" if direction == "positive" else ("多目标周期右尾不足" if direction == "negative" else "多目标周期中性"),
        "ML",
        "multi-horizon right-tail target classifiers",
        direction,
        _clip(score, -0.40, 0.44),
        0.68,
        "active",
        f"最佳 {best.get('label')}，当前概率 {probability:.1%}，历史基准 {base:.1%}；场景 {summary}。",
        "smartmoney.raw_daily / sklearn",
        extra=profile.to_dict(),
    )


def _target_ladder_probability_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_target_ladder_probability_model(
        snapshot.daily_bars.require(),
        params=params.get("target_ladder_probability_model") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "target_ladder_probability_model",
            "目标阶梯概率模型",
            "ML",
            "target ladder probability classifiers",
            "neutral",
            0.0,
            0.66,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    extra = profile.extra or {}
    rows = [row for row in extra.get("rows", []) if row.get("available")]
    if not rows:
        return StrategySignal(
            "target_ladder_probability_model",
            "目标阶梯概率模型",
            "ML",
            "target ladder probability classifiers",
            "neutral",
            0.0,
            0.66,
            "degraded",
            "目标阶梯概率模型没有可用场景。",
            "smartmoney.raw_daily",
            extra=profile.to_dict(),
        )
    best = max(rows, key=lambda row: float(row.get("probability") or 0.0) - float(row.get("stop_before_target_rate") or 0.0))
    probability = float(best.get("probability") or 0.0)
    base = float(best.get("base_rate") or 0.0)
    stop_before = float(best.get("stop_before_target_rate") or 0.0)
    expected_proxy = float(best.get("expected_return_proxy") or 0.0)
    ratio = float(best.get("target_stop_ratio") or 0.0)
    score = (
        0.42 * _tanh_scaled((probability - base) * 100.0, 15.0)
        + 0.24 * _tanh_scaled((expected_proxy - 5.0), 8.0)
        + 0.20 * _tanh_scaled((ratio - 1.6) * 10.0, 9.0)
        - 0.14 * _tanh_scaled((stop_before - 0.32) * 100.0, 18.0)
    )
    direction = _direction(score, params)
    summary = "；".join(
        f"{row.get('label')} P={float(row.get('probability') or 0.0):.1%}/止损先到={float(row.get('stop_before_target_rate') or 0.0):.1%}"
        for row in rows[:3]
    )
    eta = best.get("median_days_to_target") or best.get("avg_days_to_target")
    eta_text = f"，历史命中中位 {float(eta):.1f} 日" if eta is not None else ""
    return StrategySignal(
        "target_ladder_probability_model",
        "目标阶梯右尾顺风" if direction == "positive" else ("目标阶梯性价比不足" if direction == "negative" else "目标阶梯中性"),
        "ML",
        "target ladder probability classifiers",
        direction,
        _clip(score, -0.40, 0.42),
        0.66,
        "active",
        f"最佳 {best.get('label')}，当前概率 {probability:.1%}，历史基准 {base:.1%}，止损先到 {stop_before:.1%}{eta_text}；{summary}。",
        "smartmoney.raw_daily / sklearn",
        extra=profile.to_dict(),
    )


def _path_shape_mixture_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_path_shape_mixture_model(
        snapshot.daily_bars.require(),
        params=params.get("path_shape_mixture_model") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "path_shape_mixture_model",
            "路径形态混合模型",
            "ML",
            "single-stock Gaussian mixture path-shape model",
            "neutral",
            0.0,
            0.64,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    probability = float(profile.probability or 0.0)
    base = float(profile.positive_rate or 0.0)
    extra = profile.extra or {}
    expected_return = float(extra.get("expected_forward_return_pct") or 0.0)
    stop_rate = float(extra.get("weighted_stop_first_rate") or 0.0)
    dominant = extra.get("dominant_cluster") or {}
    score = (
        0.42 * _tanh_scaled((probability - base) * 100.0, 15.0)
        + 0.30 * _tanh_scaled(expected_return, 10.0)
        - 0.20 * _tanh_scaled((stop_rate - 0.30) * 100.0, 18.0)
        + 0.08 * _tanh_scaled((float(dominant.get("posterior") or 0.0) - 0.35) * 100.0, 20.0)
    )
    direction = _direction(score, params)
    return StrategySignal(
        "path_shape_mixture_model",
        "路径簇右尾占优" if direction == "positive" else ("路径簇风险偏高" if direction == "negative" else "路径簇中性"),
        "ML",
        "single-stock Gaussian mixture path-shape model",
        direction,
        _clip(score, -0.38, 0.40),
        0.64,
        "active",
        f"{profile.sample_count} 个路径样本；当前簇加权目标概率 {probability:.1%}，历史基准 {base:.1%}，预期{int(extra.get('horizon_bars') or 0)}日收益 {expected_return:+.2f}%，止损先到 {stop_rate:.1%}。",
        "smartmoney.raw_daily / sklearn.mixture",
        extra=profile.to_dict(),
    )


def _mfe_mae_surface_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_mfe_mae_surface_model(
        snapshot.daily_bars.require(),
        params=params.get("mfe_mae_surface_model") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "mfe_mae_surface_model",
            "MFE/MAE收益风险面",
            "ML",
            "MFE/MAE gradient boosting regressors",
            "neutral",
            0.0,
            0.66,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    probability = float(profile.probability or 0.0)
    base = float(profile.positive_rate or 0.0)
    extra = profile.extra or {}
    mfe = float(extra.get("expected_mfe_pct") or 0.0)
    mae = float(extra.get("expected_mae_pct") or 0.0)
    rr = float(extra.get("expected_reward_risk") or 0.0)
    score = (
        0.36 * _tanh_scaled((probability - base) * 100.0, 15.0)
        + 0.34 * _tanh_scaled((mfe - float(extra.get("target_pct") or 25.0)), 10.0)
        + 0.20 * _tanh_scaled((rr - 1.6) * 10.0, 8.0)
        - 0.10 * _tanh_scaled((mae - float(extra.get("stop_pct") or 10.0)), 6.0)
    )
    direction = _direction(score, params)
    return StrategySignal(
        "mfe_mae_surface_model",
        "收益风险面顺风" if direction == "positive" else ("收益风险面不划算" if direction == "negative" else "收益风险面中性"),
        "ML",
        "MFE/MAE gradient boosting regressors",
        direction,
        _clip(score, -0.40, 0.42),
        0.66,
        "active",
        f"{profile.sample_count} 个 MFE/MAE 标签；预测最大上行 {mfe:.2f}%，最大不利 {mae:.2f}%，收益风险比 {rr:.2f}，目标概率代理 {probability:.1%}。",
        "smartmoney.raw_daily / sklearn",
        extra=profile.to_dict(),
    )


def _forward_entry_timing_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_forward_entry_timing_model(
        snapshot.daily_bars.require(),
        params=params.get("forward_entry_timing_model") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "forward_entry_timing_model",
            "未来5日择时模型",
            "ML",
            "forward entry timing random forest",
            "neutral",
            0.0,
            0.60,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    probability = float(profile.probability or 0.0)
    base = float(profile.positive_rate or 0.0)
    extra = profile.extra or {}
    wait_fill = float(extra.get("historical_wait_fill_rate") or 0.0)
    wait_success = float(extra.get("historical_wait_success_rate") or 0.0)
    score = 0.58 * _tanh_scaled((probability - base) * 100.0, 14.0) + 0.24 * _tanh_scaled((probability - 0.50) * 100.0, 18.0) - 0.18 * _tanh_scaled((wait_fill * wait_success - 0.24) * 100.0, 18.0)
    direction = _direction(score, params)
    return StrategySignal(
        "forward_entry_timing_model",
        "当前买点优于等待" if direction == "positive" else ("未来5日等待优先" if direction == "negative" else "择时中性"),
        "ML",
        "forward entry timing random forest",
        direction,
        _clip(score, -0.38, 0.38),
        0.60,
        "active",
        f"{profile.sample_count} 个择时标签；当前买入优先概率 {probability:.1%}，历史基准 {base:.1%}，历史等待成交 {wait_fill:.1%}/成功 {wait_success:.1%}。",
        "smartmoney.raw_daily / sklearn.ensemble",
        extra=profile.to_dict(),
    )


def _entry_price_surface_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_entry_price_surface_model(
        snapshot.daily_bars.require(),
        params=params.get("entry_price_surface_model") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "entry_price_surface_model",
            "买入价格面模型",
            "ML",
            "entry route price-surface random forest",
            "neutral",
            0.0,
            0.64,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    extra = profile.extra or {}
    route_probs = extra.get("route_probabilities") or {}
    route_base = extra.get("route_base_rates") or {}
    best_route = str(extra.get("best_route") or "avoid")
    tradable_prob = float(extra.get("tradable_probability") or 0.0)
    avoid_prob = float(route_probs.get("avoid") or 0.0)
    buy_now_prob = float(route_probs.get("buy_now") or 0.0)
    wait_prob = float(route_probs.get("wait_pullback") or 0.0)
    breakout_prob = float(route_probs.get("breakout_confirm") or 0.0)
    tradable_base = 1.0 - float(route_base.get("avoid") or 0.0)
    route_edge = float(route_probs.get(best_route) or 0.0) - float(route_base.get(best_route) or 0.0)
    score = (
        0.46 * _tanh_scaled((tradable_prob - tradable_base) * 100.0, 16.0)
        + 0.28 * _tanh_scaled(route_edge * 100.0, 14.0)
        + 0.18 * _tanh_scaled((buy_now_prob + 0.75 * wait_prob + 0.65 * breakout_prob - avoid_prob) * 100.0, 28.0)
        + 0.08 * _tanh_scaled(((profile.oos_hit_rate or 0.25) - 0.25) * 100.0, 18.0)
    )
    if best_route == "avoid":
        score -= 0.18 * _tanh_scaled((avoid_prob - 0.34) * 100.0, 16.0)
    direction = _direction(score, params)
    route_label = {
        "buy_now": "今日买入",
        "wait_pullback": "等待回踩",
        "breakout_confirm": "突破确认",
        "avoid": "暂不交易",
    }.get(best_route, best_route)
    prices = extra.get("suggested_prices") or {}
    price_text = "，".join(
        f"{label} {float(prices[key]):.2f}"
        for key, label in [("buy_now", "现价"), ("wait_pullback", "回踩"), ("breakout_confirm", "突破")]
        if prices.get(key) is not None
    )
    return StrategySignal(
        "entry_price_surface_model",
        f"买入路线: {route_label}",
        "ML",
        "entry route price-surface random forest",
        direction,
        _clip(score, -0.40, 0.42),
        0.64,
        "active",
        f"{profile.sample_count} 个历史路线标签；最佳路线 {route_label}，可交易概率 {tradable_prob:.1%}，今日/回踩/突破/回避 {buy_now_prob:.1%}/{wait_prob:.1%}/{breakout_prob:.1%}/{avoid_prob:.1%}；{price_text}。",
        "smartmoney.raw_daily / sklearn.ensemble",
        extra=profile.to_dict(),
    )


def _pullback_rebound_ml_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_pullback_rebound_model(
        snapshot.daily_bars.require(),
        params=params.get("pullback_rebound_classifier") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "pullback_rebound_classifier",
            "回踩反弹模型",
            "ML",
            "single-stock pullback rebound random forest",
            "neutral",
            0.0,
            0.62,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    probability = float(profile.probability or 0.0)
    base = float(profile.positive_rate or 0.0)
    oos_hit = float(profile.oos_hit_rate or base)
    extra = profile.extra or {}
    dist_ma20 = float(extra.get("current_dist_ma20_pct") or 0.0)
    drawdown = float(extra.get("current_drawdown_20_pct") or 0.0)
    score = (
        0.54 * _tanh_scaled((probability - base) * 100.0, 14.0)
        + 0.24 * _tanh_scaled((oos_hit - base) * 100.0, 14.0)
        + 0.22 * _tanh_scaled((-abs(dist_ma20) + 5.0), 5.0)
    )
    direction = _direction(score, params)
    return StrategySignal(
        "pullback_rebound_classifier",
        "回踩反弹概率占优" if direction == "positive" else ("回踩破位风险偏高" if direction == "negative" else "回踩反弹中性"),
        "ML",
        "single-stock pullback rebound random forest",
        direction,
        _clip(score, -0.38, 0.40),
        0.62,
        "active",
        f"{profile.sample_count} 个回踩路径标签；反弹概率 {probability:.1%}，历史基准 {base:.1%}，距MA20 {dist_ma20:+.2f}%，20日回撤 {drawdown:+.2f}%。",
        "smartmoney.raw_daily / sklearn.ensemble",
        extra=profile.to_dict(),
    )


def _squeeze_breakout_ml_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_squeeze_breakout_model(
        snapshot.daily_bars.require(),
        params=params.get("squeeze_breakout_classifier") or {},
        risk_params=snapshot.ctx.params.get("risk", {}),
    )
    if not profile.available:
        return StrategySignal(
            "squeeze_breakout_classifier",
            "收敛突破模型",
            "ML",
            "single-stock volatility squeeze breakout GBM",
            "neutral",
            0.0,
            0.62,
            "degraded",
            profile.reason,
            "smartmoney.raw_daily",
        )
    probability = float(profile.probability or 0.0)
    base = float(profile.positive_rate or 0.0)
    oos_hit = float(profile.oos_hit_rate or base)
    extra = profile.extra or {}
    atr_ratio = float(extra.get("current_atr5_atr20") or 1.0)
    range_pos = float(extra.get("current_range_pos_60") or 0.5)
    squeeze_bonus = _clip(1.0 - atr_ratio, -0.8, 0.8)
    score = (
        0.52 * _tanh_scaled((probability - base) * 100.0, 15.0)
        + 0.22 * _tanh_scaled((oos_hit - base) * 100.0, 14.0)
        + 0.16 * squeeze_bonus
        + 0.10 * _tanh_scaled((range_pos - 0.58) * 100.0, 20.0)
    )
    direction = _direction(score, params)
    return StrategySignal(
        "squeeze_breakout_classifier",
        "收敛突破概率占优" if direction == "positive" else ("收敛突破概率不足" if direction == "negative" else "收敛突破中性"),
        "ML",
        "single-stock volatility squeeze breakout GBM",
        direction,
        _clip(score, -0.38, 0.40),
        0.62,
        "active",
        f"{profile.sample_count} 个收敛突破标签；突破概率 {probability:.1%}，历史基准 {base:.1%}，ATR5/20 {atr_ratio:.2f}，60日位置 {range_pos:.1%}。",
        "smartmoney.raw_daily / sklearn",
        extra=profile.to_dict(),
    )


def _model_stack_blender_signal(signals: list[StrategySignal], params: dict) -> StrategySignal:
    cfg = params.get("model_stack_blender") or {}
    if not cfg.get("enabled", True):
        return StrategySignal("model_stack_blender", "模型融合", "ML", "probability stack blender", "neutral", 0.0, 0.62, "degraded", "model stack blender disabled。", "strategy_matrix")
    rows: list[dict[str, float | str]] = []
    for signal in signals:
        if signal.status == "missing":
            continue
        extra = signal.extra or {}
        prob = _extract_model_probability(signal.key, extra)
        if prob is None:
            continue
        weight = float((cfg.get("source_weights") or {}).get(signal.key, 1.0))
        rows.append({"key": signal.key, "name": signal.name, "probability": _clip(float(prob), 0.0, 0.95), "weight": max(weight, 0.0)})
    min_sources = int(cfg.get("min_sources", 3))
    if len(rows) < min_sources:
        return StrategySignal(
            "model_stack_blender",
            "模型融合样本不足",
            "ML",
            "probability stack blender",
            "neutral",
            0.0,
            0.62,
            "degraded",
            f"可融合模型源 {len(rows)} 个，低于 {min_sources} 个。",
            "strategy_matrix",
            extra={"sources": rows},
        )
    total_w = sum(float(row["weight"]) for row in rows) or 1.0
    mean_prob = sum(float(row["probability"]) * float(row["weight"]) for row in rows) / total_w
    variance = sum(float(row["weight"]) * (float(row["probability"]) - mean_prob) ** 2 for row in rows) / total_w
    dispersion = math.sqrt(max(variance, 0.0))
    base = float(cfg.get("right_tail_base_probability", 0.24))
    score = 0.72 * _tanh_scaled((mean_prob - base) * 100.0, 18.0) - 0.18 * _tanh_scaled(dispersion * 100.0, 18.0)
    score += 0.10 * _tanh_scaled((len(rows) - min_sources) * 10.0, 20.0)
    direction = _direction(score, params)
    strongest = sorted(rows, key=lambda row: float(row["probability"]) * float(row["weight"]), reverse=True)[:3]
    return StrategySignal(
        "model_stack_blender",
        "模型融合右尾顺风" if direction == "positive" else ("模型融合分歧/压制" if direction == "negative" else "模型融合中性"),
        "ML",
        "probability stack blender",
        direction,
        _clip(score, -0.40, 0.42),
        0.64,
        "active",
        f"{len(rows)} 个模型源融合；加权概率 {mean_prob:.1%}，分歧度 {dispersion:.1%}；主驱动 {'、'.join(str(row['name']) for row in strongest)}。",
        "strategy_matrix model probabilities",
        extra={"sources": rows, "weighted_probability": round(mean_prob, 4), "dispersion": round(dispersion, 4)},
    )


def _position_sizing_model_signal(signals: list[StrategySignal], snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    cfg = params.get("position_sizing_model") or {}
    if not cfg.get("enabled", True):
        return StrategySignal("position_sizing_model", "仓位模型", "ML", "continuous position sizing meta model", "neutral", 0.0, 0.46, "degraded", "position sizing model disabled。", "strategy_matrix")
    available = [s for s in signals if s.status != "missing"]
    if not available:
        return StrategySignal("position_sizing_model", "仓位模型", "ML", "continuous position sizing meta model", "neutral", 0.0, 0.46, "missing", "策略信号为空，无法计算仓位。", "strategy_matrix")
    total_w = sum(max(float(s.weight), 0.0) for s in available) or 1.0
    edge = sum(float(s.score) * max(float(s.weight), 0.0) for s in available) / total_w
    risk_scores = [s for s in available if _cluster_for_signal(s.key) == "risk_warning"]
    risk_pressure = sum(max(-float(s.score), 0.0) * max(float(s.weight), 0.0) for s in risk_scores) / max(sum(max(float(s.weight), 0.0) for s in risk_scores), 1.0)
    model_probs = [_extract_model_probability(s.key, s.extra or {}) for s in available]
    model_probs = [float(p) for p in model_probs if p is not None]
    probability = sum(model_probs) / len(model_probs) if model_probs else _clip(0.5 + edge, 0.0, 1.0)
    daily = snapshot.daily_bars.data
    amount = 0.0
    if daily is not None and hasattr(daily, "empty") and not daily.empty and "amount" in daily.columns:
        amount_mean = pd.to_numeric(daily["amount"], errors="coerce").dropna().tail(20).mean()
        amount = float(amount_mean) if pd.notna(amount_mean) and math.isfinite(float(amount_mean)) else 0.0
    liquidity_gate = _clip(math.log1p(max(amount, 0.0)) / math.log(max(float(cfg.get("full_liquidity_amount", 2_000_000.0)), 10.0)), 0.15, 1.05)
    stop_hazard = next((s for s in available if s.key == "stop_loss_hazard_model"), None)
    stop_prob = _as_float(((stop_hazard.extra or {}).get("extra") or {}).get("stop_hazard_probability")) if stop_hazard else None
    if stop_prob is None:
        stop_prob = 0.35 + risk_pressure
    max_fraction = float(cfg.get("max_fraction", 0.35))
    min_fraction = float(cfg.get("min_fraction", 0.0))
    raw_fraction = (
        float(cfg.get("base_fraction", 0.10))
        + float(cfg.get("edge_scale", 0.34)) * max(edge, 0.0)
        + float(cfg.get("probability_scale", 0.18)) * max(probability - 0.45, 0.0)
        - float(cfg.get("risk_penalty", 0.28)) * risk_pressure
        - float(cfg.get("stop_probability_penalty", 0.22)) * max(float(stop_prob) - 0.28, 0.0)
    )
    fraction = _clip(raw_fraction * liquidity_gate, min_fraction, max_fraction)
    if edge <= 0 or probability < float(cfg.get("min_probability_to_size", 0.38)):
        fraction = min(fraction, float(cfg.get("watch_fraction_cap", 0.05)))
    score = _clip((fraction / max(max_fraction, 1e-6) - 0.42) * 0.65, -0.30, 0.34)
    direction = _direction(score, params)
    label = "仓位模型支持开仓" if direction == "positive" else ("仓位模型压制" if direction == "negative" else "仓位模型中性")
    return StrategySignal(
        "position_sizing_model",
        label,
        "ML",
        "continuous position sizing meta model",
        direction,
        score,
        0.46,
        "active",
        f"综合边际 {edge:+.3f}，模型均值概率 {probability:.1%}，风险压力 {risk_pressure:.1%}，流动性闸门 {liquidity_gate:.2f}，建议仓位 {fraction:.1%}。",
        "strategy_matrix / smartmoney.raw_daily",
        extra={
            "recommended_fraction": round(fraction, 6),
            "edge": round(edge, 6),
            "mean_model_probability": round(probability, 6),
            "risk_pressure": round(risk_pressure, 6),
            "liquidity_gate": round(liquidity_gate, 6),
            "stop_hazard_probability": round(float(stop_prob), 6),
        },
    )


def _extract_model_probability(key: str, extra: dict) -> float | None:
    if key in {"right_tail_meta_gbm", "temporal_fusion_sequence_ranker"}:
        return _as_float(extra.get("probability"))
    if key == "target_stop_survival_model":
        return _as_float((extra.get("extra") or {}).get("target_first_probability") or extra.get("probability"))
    if key == "stop_loss_hazard_model":
        nested = extra.get("extra") or {}
        stop_prob = _as_float(nested.get("stop_hazard_probability") or extra.get("probability"))
        return None if stop_prob is None else _clip(1.0 - stop_prob, 0.0, 1.0)
    if key == "gap_risk_open_model":
        nested = extra.get("extra") or {}
        gap_prob = _as_float(nested.get("adverse_gap_probability") or extra.get("probability"))
        return None if gap_prob is None else _clip(1.0 - gap_prob, 0.0, 1.0)
    if key == "multi_horizon_target_classifier":
        return _as_float(extra.get("probability"))
    if key == "target_ladder_probability_model":
        return _as_float(extra.get("probability"))
    if key == "path_shape_mixture_model":
        return _as_float(extra.get("probability"))
    if key == "mfe_mae_surface_model":
        return _as_float(extra.get("probability"))
    if key == "forward_entry_timing_model":
        return _as_float((extra.get("extra") or {}).get("buy_now_probability") or extra.get("probability"))
    if key == "entry_price_surface_model":
        nested = extra.get("extra") or {}
        return _as_float(nested.get("tradable_probability") or extra.get("probability"))
    if key in {"pullback_rebound_classifier", "squeeze_breakout_classifier"}:
        return _as_float(extra.get("probability"))
    if key == "limit_up_event_path_model":
        return _as_float(extra.get("continuation_probability"))
    if key == "peer_financial_alpha_model":
        alpha = _as_float(extra.get("expected_alpha_pct"))
        return None if alpha is None else _clip(0.50 + alpha / 40.0, 0.0, 1.0)
    if key == "position_sizing_model":
        fraction = _as_float(extra.get("recommended_fraction"))
        return None if fraction is None else _clip(fraction / 0.35, 0.0, 1.0)
    if key == "historical_replay_edge":
        rows = extra.get("target_stats") or []
        best = extra.get("best_key")
        selected = next((row for row in rows if row.get("key") == best), rows[0] if rows else {})
        return _as_float(selected.get("hit_rate"))
    if key == "target_stop_replay":
        rows = extra.get("target_stats") or []
        best = extra.get("best_key")
        selected = next((row for row in rows if row.get("key") == best), rows[0] if rows else {})
        return _as_float(selected.get("target_first_rate"))
    if key == "entry_fill_classifier":
        return _as_float(extra.get("predicted_fill_probability") or extra.get("clean_fill_probability") or extra.get("fill_probability"))
    if key == "analog_kronos_nearest_neighbors":
        return _as_float(extra.get("best_hit_rate") or extra.get("right_tail_hit_rate"))
    if key == "kronos_path_cluster_transition":
        clusters = extra.get("cluster_distribution") or {}
        return _as_float(clusters.get("right_tail") or clusters.get("swing_up"))
    return None


def _orderflow_mix_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    df = snapshot.moneyflow.data
    required = {"buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"}
    if df is None or df.empty or not required.issubset(df.columns):
        return StrategySignal("orderflow_mix", "大单结构", "资金", "超大单/大单净买连续评分", "neutral", 0.0, 0.85, "missing", "大单资金结构不可用。", "smartmoney.raw_moneyflow")
    tail = df.tail(7)
    buy_elg = float(pd.to_numeric(tail["buy_elg_amount"], errors="coerce").sum())
    sell_elg = float(pd.to_numeric(tail["sell_elg_amount"], errors="coerce").sum())
    buy_lg = float(pd.to_numeric(tail["buy_lg_amount"], errors="coerce").sum())
    sell_lg = float(pd.to_numeric(tail["sell_lg_amount"], errors="coerce").sum())
    gross = buy_elg + sell_elg + buy_lg + sell_lg
    if gross <= 0:
        return StrategySignal("orderflow_mix", "大单结构", "资金", "超大单/大单净买连续评分", "neutral", 0.0, 0.75, "missing", "大单总额不可用。", "smartmoney.raw_moneyflow")
    net_elg = buy_elg - sell_elg
    net_lg = buy_lg - sell_lg
    imbalance = (1.25 * net_elg + net_lg) / gross
    score = _clip(1.8 * imbalance, -0.45, 0.45)
    direction = _direction(score, params)
    name = "大单净买" if direction == "positive" else ("大单净卖" if direction == "negative" else "大单中性")
    return StrategySignal("orderflow_mix", name, "资金", "超大单/大单净买连续评分", direction, score, 0.90, "active", f"7日加权大单失衡 {imbalance:+.3f}。", "smartmoney.raw_moneyflow")


def _flow_persistence_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_flow_persistence_profile(snapshot.moneyflow.data, params=params.get("flow_persistence") or {})
    if not profile.available:
        return StrategySignal(
            "flow_persistence_decay",
            "资金持续性",
            "资金",
            "net money-flow persistence/decay",
            "neutral",
            0.0,
            0.78,
            "degraded",
            profile.reason,
            "smartmoney.raw_moneyflow",
        )
    direction = _direction(profile.score, params)
    name = "资金持续流入" if direction == "positive" else ("资金持续流出/衰减" if direction == "negative" else "资金持续性中性")
    return StrategySignal(
        "flow_persistence_decay",
        name,
        "资金",
        "net money-flow persistence/decay",
        direction,
        profile.score,
        0.78,
        "active",
        f"{profile.sample_count} 日净流合计 {profile.net_sum_wan:.1f} 万，正流天数 {profile.positive_day_share:.0%}，同向连续 {profile.same_sign_streak_days} 日，近3日/前段 {profile.latest_3d_vs_prior_pct:+.1f}%。",
        "smartmoney.raw_moneyflow",
        extra=profile.to_dict(),
    )


def _lhb_institution_hotmoney_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    events = (snapshot.event_context.data if snapshot.event_context else None) or {}
    top_rows = events.get("top_list") or []
    inst_rows = events.get("top_inst") or []
    if not top_rows and not inst_rows:
        return StrategySignal(
            "lhb_institution_hotmoney_divergence",
            "龙虎榜中性",
            "事件",
            "龙虎榜机构/席位净买连续评分",
            "neutral",
            0.0,
            0.45,
            "active",
            "近30日未触发龙虎榜，事件资金按中性处理。",
            "smartmoney.raw_top_list/raw_top_inst",
        )
    top_net = sum(_as_float(row.get("net_amount")) or 0.0 for row in top_rows)
    inst_net = sum(_as_float(row.get("net_buy")) or 0.0 for row in inst_rows)
    gross = (
        sum(abs(_as_float(row.get("l_buy")) or 0.0) + abs(_as_float(row.get("l_sell")) or 0.0) for row in top_rows)
        + sum(abs(_as_float(row.get("buy")) or 0.0) + abs(_as_float(row.get("sell")) or 0.0) for row in inst_rows)
    )
    denominator = max(gross, abs(top_net) + abs(inst_net), 1.0)
    institution_share = inst_net / denominator
    hotmoney_share = top_net / denominator
    divergence_penalty = 0.25 if top_net > 0 and inst_net < 0 else 0.0
    raw = 1.35 * institution_share + 0.55 * hotmoney_share - divergence_penalty
    score = _clip(raw * 2.2, -0.45, 0.45)
    direction = _direction(score, params)
    name = "龙虎榜机构共振" if direction == "positive" else ("龙虎榜分歧/净卖" if direction == "negative" else "龙虎榜中性")
    return StrategySignal(
        "lhb_institution_hotmoney_divergence",
        name,
        "事件",
        "龙虎榜机构/席位净买连续评分",
        direction,
        score,
        0.62,
        "active",
        f"近30日龙虎榜 {len(top_rows)} 条、机构席位 {len(inst_rows)} 条；"
        f"榜单净额 {_fmt_signed_yuan_amount(top_net)}，机构净买 {_fmt_signed_yuan_amount(inst_net)}。",
        "smartmoney.raw_top_list/raw_top_inst",
        extra={
            "top_rows": len(top_rows),
            "inst_rows": len(inst_rows),
            "top_net_yuan": round(top_net, 2),
            "inst_net_yuan": round(inst_net, 2),
            "top_net_yi": round(top_net / 1e8, 4),
            "inst_net_yi": round(inst_net / 1e8, 4),
        },
    )


def _limit_up_microstructure_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    events = (snapshot.event_context.data if snapshot.event_context else None) or {}
    rows = [*(events.get("kpl") or []), *(events.get("limit_list") or [])]
    if not rows:
        return StrategySignal(
            "limit_up_microstructure",
            "涨停事件中性",
            "事件",
            "涨停封单/开板微结构评分",
            "neutral",
            0.0,
            0.45,
            "active",
            "近30日无涨停池记录，事件催化按中性处理。",
            "smartmoney.raw_kpl_list/raw_limit_list_d",
        )
    latest = max(rows, key=lambda row: row.get("trade_date"))
    open_times = _as_float(latest.get("open_times")) or _as_float(latest.get("open_time")) or 0.0
    pct_chg = _as_float(latest.get("pct_chg")) or _as_float(latest.get("rt_pct_chg")) or 0.0
    fc_ratio = _as_float(latest.get("fc_ratio")) or 0.0
    fl_ratio = _as_float(latest.get("fl_ratio")) or 0.0
    bid_amount = _as_float(latest.get("bid_amount")) or _as_float(latest.get("limit_amount")) or _as_float(latest.get("fd_amount")) or 0.0
    amount = _as_float(latest.get("amount")) or 0.0
    seal_ratio = bid_amount / max(abs(amount), 1.0)
    status = str(latest.get("status") or latest.get("limit_") or "")
    open_penalty = _clip(open_times / 6.0, 0.0, 1.0)
    seal_score = _clip(math.log1p(max(seal_ratio, fc_ratio / 100.0, fl_ratio / 100.0)) / math.log(3.0), 0.0, 1.0)
    hard_limit_bonus = 0.18 if any(word in status for word in ["涨停", "封", "U"]) else 0.0
    score = _clip(0.30 * _tanh_scaled(pct_chg, 8.0) + 0.32 * seal_score + hard_limit_bonus - 0.35 * open_penalty, -0.45, 0.45)
    direction = _direction(score, params)
    name = "涨停封单强" if direction == "positive" else ("涨停开板风险" if direction == "negative" else "涨停结构中性")
    return StrategySignal(
        "limit_up_microstructure",
        name,
        "事件",
        "涨停封单/开板微结构评分",
        direction,
        score,
        0.58,
        "active",
        f"近30日涨停事件 {len(rows)} 条；最近 {latest.get('trade_date')} 涨跌幅 {pct_chg:+.2f}%，开板 {open_times:.0f} 次，封单/成交额 {seal_ratio:.2f}。",
        "smartmoney.raw_kpl_list/raw_limit_list_d",
        extra={"event_rows": len(rows), "latest_date": latest.get("trade_date"), "open_times": open_times, "seal_ratio": round(seal_ratio, 4)},
    )


def _limit_up_event_path_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    cfg = params.get("limit_up_event_path_model") or {}
    if not cfg.get("enabled", True):
        return StrategySignal("limit_up_event_path_model", "涨停路径模型", "事件", "limit-up continuation/fade path model", "neutral", 0.0, 0.56, "degraded", "limit-up event path model disabled。", "smartmoney.raw_kpl_list/raw_limit_list_d")
    events = (snapshot.event_context.data if snapshot.event_context else None) or {}
    rows = [*(events.get("kpl") or []), *(events.get("limit_list") or [])]
    if not rows:
        return StrategySignal(
            "limit_up_event_path_model",
            "涨停路径中性",
            "事件",
            "limit-up continuation/fade path model",
            "neutral",
            0.0,
            0.56,
            "active",
            "近30日无涨停/炸板路径记录，按事件中性处理。",
            "smartmoney.raw_kpl_list/raw_limit_list_d",
            extra={"continuation_probability": 0.0, "fade_probability": 0.0, "event_rows": 0},
        )
    sorted_rows = sorted(rows, key=lambda row: str(row.get("trade_date") or ""))
    latest = sorted_rows[-1]
    open_times = _as_float(latest.get("open_times")) or _as_float(latest.get("open_time")) or 0.0
    pct_chg = _as_float(latest.get("pct_chg")) or _as_float(latest.get("rt_pct_chg")) or 0.0
    fc_ratio = (_as_float(latest.get("fc_ratio")) or 0.0) / 100.0
    fl_ratio = (_as_float(latest.get("fl_ratio")) or 0.0) / 100.0
    bid_amount = _as_float(latest.get("bid_amount")) or _as_float(latest.get("limit_amount")) or _as_float(latest.get("fd_amount")) or 0.0
    amount = _as_float(latest.get("amount")) or 0.0
    seal_ratio = max(bid_amount / max(abs(amount), 1.0), fc_ratio, fl_ratio)
    recent_events = sorted_rows[-5:]
    event_density = _clip(len(recent_events) / max(float(cfg.get("density_full_events", 4.0)), 1.0), 0.0, 1.5)
    open_penalty = _clip(open_times / max(float(cfg.get("open_times_bad", 5.0)), 1.0), 0.0, 1.4)
    seal_quality = _clip(math.log1p(max(seal_ratio, 0.0)) / math.log(3.0), 0.0, 1.2)
    momentum_quality = _clip(pct_chg / max(float(cfg.get("limit_pct_reference", 10.0)), 1.0), -1.0, 1.2)
    continuation_probability = _clip(0.18 + 0.30 * seal_quality + 0.22 * momentum_quality + 0.16 * event_density - 0.28 * open_penalty, 0.0, 0.92)
    fade_probability = _clip(0.14 + 0.34 * open_penalty + 0.22 * max(-momentum_quality, 0.0) - 0.18 * seal_quality, 0.0, 0.92)
    score = 0.46 * _tanh_scaled((continuation_probability - fade_probability) * 100.0, 24.0) + 0.18 * _tanh_scaled((continuation_probability - 0.34) * 100.0, 18.0)
    direction = _direction(score, params)
    return StrategySignal(
        "limit_up_event_path_model",
        "涨停路径延续" if direction == "positive" else ("涨停后衰减/炸板风险" if direction == "negative" else "涨停路径中性"),
        "事件",
        "limit-up continuation/fade path model",
        direction,
        _clip(score, -0.38, 0.40),
        0.56,
        "active",
        f"近30日涨停/炸板事件 {len(rows)} 条；延续概率 {continuation_probability:.1%}，衰减概率 {fade_probability:.1%}，开板 {open_times:.0f} 次，封单质量 {seal_quality:.2f}。",
        "smartmoney.raw_kpl_list/raw_limit_list_d",
        extra={
            "event_rows": len(rows),
            "latest_date": latest.get("trade_date"),
            "continuation_probability": round(continuation_probability, 6),
            "fade_probability": round(fade_probability, 6),
            "open_times": open_times,
            "seal_quality": round(seal_quality, 4),
        },
    )


def _event_catalyst_llm_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    events = (snapshot.event_context.data if snapshot.event_context else None) or {}
    rows = [*(events.get("company_events") or []), *(events.get("catalyst_events") or [])]
    if not rows:
        return StrategySignal(
            "event_catalyst_llm",
            "LLM 事件催化",
            "LLM",
            "cached LLM catalyst event memory",
            "neutral",
            0.0,
            0.50,
            "missing",
            "近120日没有项目 LLM 抽取的公司/催化事件记忆。",
            "research.company_event_memory / ta.catalyst_event_memory",
        )
    importance_w = {"high": 1.0, "medium": 0.58, "low": 0.28}
    polarity_w = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
    weighted = 0.0
    total_w = 0.0
    high_impact = []
    for row in rows:
        imp = str(row.get("importance") or "low").lower()
        pol = str(row.get("polarity") or "neutral").lower()
        weight = importance_w.get(imp, 0.28)
        weighted += weight * polarity_w.get(pol, 0.0)
        total_w += weight
        if imp == "high" or abs(polarity_w.get(pol, 0.0)) > 0:
            high_impact.append(str(row.get("title") or row.get("event_type") or "事件")[:40])
    balance = weighted / max(total_w, 1e-9)
    score = _clip(0.34 * balance + 0.08 * _tanh_scaled(len(rows), 6.0), -0.36, 0.38)
    direction = _direction(score, params)
    return StrategySignal(
        "event_catalyst_llm",
        "LLM 事件催化顺风" if direction == "positive" else ("LLM 事件风险" if direction == "negative" else "LLM 事件中性"),
        "LLM",
        "cached LLM catalyst event memory",
        direction,
        score,
        0.50,
        "active",
        f"近120日 LLM 事件 {len(rows)} 条，极性加权 {balance:+.2f}；关键事件：{'；'.join(high_impact[:3]) or '无显著极性事件'}。",
        "research.company_event_memory / ta.catalyst_event_memory",
        extra={"event_count": len(rows), "polarity_balance": round(balance, 4), "top_events": high_impact[:5]},
    )


def _regime_adaptive_weight_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    cfg = params.get("regime_adaptive_weight_model") or {}
    if not cfg.get("enabled", True):
        return StrategySignal("regime_adaptive_weight_model", "体制自适应权重", "ML", "regime/sector phase adaptive cluster tilts", "neutral", 0.0, 0.54, "degraded", "regime adaptive weight model disabled。", "ta.regime / smartmoney.sector_state_daily")
    market_regime = _normalized_market_regime(snapshot)
    sw_phase = _normalized_sw_l2_phase(snapshot)
    clusters = [
        "trend_breakout",
        "pullback_continuation",
        "order_flow_smart_money",
        "sw_l2_sector_leadership",
        "model_ensemble",
        "risk_warning",
        "intraday_t0_execution",
    ]
    multipliers = {cluster: _context_gate(cluster, params, snapshot) for cluster in clusters}
    if not market_regime and not sw_phase:
        return StrategySignal(
            "regime_adaptive_weight_model",
            "体制权重中性",
            "ML",
            "regime/sector phase adaptive cluster tilts",
            "neutral",
            0.0,
            0.54,
            "degraded",
            "市场体制和 SW L2 相位未识别，动态调权退化为中性。",
            "ta.regime / smartmoney.sector_state_daily",
            extra={"cluster_multipliers": multipliers},
        )
    offensive = sum(multipliers.get(k, 1.0) for k in ["trend_breakout", "order_flow_smart_money", "sw_l2_sector_leadership", "model_ensemble"]) / 4.0
    defensive = multipliers.get("risk_warning", 1.0)
    execution = multipliers.get("intraday_t0_execution", 1.0)
    score = 0.44 * _tanh_scaled((offensive - defensive) * 100.0, 28.0) + 0.18 * _tanh_scaled((execution - 1.0) * 100.0, 24.0)
    direction = _direction(score, params)
    return StrategySignal(
        "regime_adaptive_weight_model",
        "体制调权进攻" if direction == "positive" else ("体制调权防守" if direction == "negative" else "体制调权中性"),
        "ML",
        "regime/sector phase adaptive cluster tilts",
        direction,
        _clip(score, -0.32, 0.34),
        0.54,
        "active",
        f"市场体制 {market_regime or '未识别'}，SW L2 相位 {sw_phase or '未识别'}；进攻簇均值 {offensive:.2f}，风险簇 {defensive:.2f}，执行簇 {execution:.2f}。",
        "ta.regime / smartmoney.sector_state_daily",
        extra={"market_regime": market_regime, "sw_l2_phase": sw_phase, "cluster_multipliers": multipliers},
    )


def _northbound_regime_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    events = (snapshot.event_context.data if snapshot.event_context else None) or {}
    rows = sorted(events.get("northbound") or [], key=lambda row: row.get("trade_date"))
    if len(rows) < 5:
        return StrategySignal(
            "northbound_regime",
            "北向体制中性",
            "资金",
            "northbound flow regime",
            "neutral",
            0.0,
            0.50,
            "missing",
            "北向资金样本不足，按中性处理。",
            "smartmoney.raw_moneyflow_hsgt",
        )
    north = [_as_float(row.get("north_money")) or 0.0 for row in rows]
    tail5 = north[-5:]
    tail20 = north[-20:] if len(north) >= 20 else north
    avg5 = sum(tail5) / max(len(tail5), 1)
    avg20 = sum(tail20) / max(len(tail20), 1)
    positive_share = sum(1 for v in tail5 if v > 0) / max(len(tail5), 1)
    acceleration = avg5 - avg20
    score = 0.20 * _tanh_scaled(avg5, 80000.0) + 0.16 * _tanh_scaled(acceleration, 60000.0) + 0.10 * (positive_share - 0.5)
    score = _clip(score, -0.34, 0.34)
    direction = _direction(score, params)
    name = "北向顺风" if direction == "positive" else ("北向逆风" if direction == "negative" else "北向中性")
    return StrategySignal(
        "northbound_regime",
        name,
        "资金",
        "northbound flow regime",
        direction,
        score,
        0.50,
        "active",
        f"北向近5日均值 {avg5 / 10000.0:+.2f} 亿，近20日均值 {avg20 / 10000.0:+.2f} 亿，5日正流占比 {positive_share:.0%}。",
        "smartmoney.raw_moneyflow_hsgt",
        extra={"avg5_wan": round(avg5, 2), "avg20_wan": round(avg20, 2), "positive_share_5d": round(positive_share, 4)},
    )


def _market_margin_impulse_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    events = (snapshot.event_context.data if snapshot.event_context else None) or {}
    rows = sorted(events.get("market_margin") or [], key=lambda row: row.get("trade_date"))
    if len(rows) < 6:
        return StrategySignal(
            "market_margin_impulse",
            "两融脉冲中性",
            "风险",
            "market margin impulse",
            "neutral",
            0.0,
            0.48,
            "missing",
            "两融市场样本不足，按中性处理。",
            "smartmoney.raw_margin",
        )
    latest = rows[-1]
    prev5 = rows[-6]
    tail5 = rows[-5:]
    latest_balance = _as_float(latest.get("rzye")) or 0.0
    prev_balance = _as_float(prev5.get("rzye")) or 0.0
    balance_chg = latest_balance / prev_balance - 1.0 if latest_balance > 0 and prev_balance > 0 else 0.0
    buy5 = sum(_as_float(row.get("rzmre")) or 0.0 for row in tail5)
    repay5 = sum(_as_float(row.get("rzche")) or 0.0 for row in tail5)
    impulse = (buy5 - repay5) / max(latest_balance, 1.0)
    overheat = max(0.0, balance_chg - 0.035) / 0.035
    score = 0.22 * _tanh_scaled(balance_chg * 100.0, 1.2) + 0.18 * _tanh_scaled(impulse * 100.0, 0.55) - 0.16 * _clip(overheat, 0.0, 1.5)
    score = _clip(score, -0.34, 0.28)
    direction = _direction(score, params)
    name = "两融温和扩张" if direction == "positive" else ("两融收缩/过热" if direction == "negative" else "两融中性")
    return StrategySignal(
        "market_margin_impulse",
        name,
        "风险",
        "market margin impulse",
        direction,
        score,
        0.48,
        "active",
        f"市场融资余额5日变化 {balance_chg * 100:+.2f}%，融资买入-偿还/余额 {impulse * 100:+.2f}%。",
        "smartmoney.raw_margin",
        extra={"balance_change_5d_pct": round(balance_chg * 100.0, 4), "financing_impulse_pct": round(impulse * 100.0, 4)},
    )


def _block_trade_pressure_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    events = (snapshot.event_context.data if snapshot.event_context else None) or {}
    rows = events.get("block_trade") or []
    if not rows:
        return StrategySignal(
            "block_trade_pressure",
            "大宗交易中性",
            "事件",
            "block-trade premium/discount pressure",
            "neutral",
            0.0,
            0.45,
            "active",
            "近60日无大宗交易记录，事件压力按中性处理。",
            "smartmoney.raw_block_trade",
        )
    daily = snapshot.daily_bars.data
    close = None
    avg_amount = None
    if daily is not None and not daily.empty and "close" in daily.columns:
        ordered = daily.sort_values("trade_date") if "trade_date" in daily.columns else daily
        close = _as_float(ordered.iloc[-1].get("close"))
        if "amount" in ordered.columns:
            amounts = pd.to_numeric(ordered.tail(20)["amount"], errors="coerce").dropna()
            if len(amounts):
                avg_amount = float(amounts.mean())
    amount_sum = sum(_as_float(row.get("amount")) or 0.0 for row in rows)
    weighted_price = sum((_as_float(row.get("price")) or 0.0) * (_as_float(row.get("amount")) or 0.0) for row in rows) / max(amount_sum, 1.0)
    premium_pct = weighted_price / close * 100.0 - 100.0 if close and close > 0 and weighted_price > 0 else 0.0
    liquidity_ratio = amount_sum / max(avg_amount or amount_sum or 1.0, 1.0)
    size_amp = _clip(math.log1p(max(liquidity_ratio, 0.0)) / math.log(4.0), 0.2, 1.4)
    score = _clip(0.26 * _tanh_scaled(premium_pct, 4.0) * size_amp, -0.34, 0.34)
    direction = _direction(score, params)
    name = "大宗溢价承接" if direction == "positive" else ("大宗折价压力" if direction == "negative" else "大宗交易中性")
    return StrategySignal(
        "block_trade_pressure",
        name,
        "事件",
        "block-trade premium/discount pressure",
        direction,
        score,
        0.45,
        "active",
        f"近60日大宗 {len(rows)} 笔，成交额合计 {amount_sum:.1f}，均价相对现价 {premium_pct:+.2f}%，流动性占比 {liquidity_ratio:.2f}。",
        "smartmoney.raw_block_trade",
        extra={"rows": len(rows), "amount_sum": round(amount_sum, 2), "premium_pct": round(premium_pct, 4), "liquidity_ratio": round(liquidity_ratio, 4)},
    )


def _ta_family_signals(snapshot: StockEdgeSnapshot, params: dict) -> list[StrategySignal]:
    ta = snapshot.ta_context.data or {}
    candidates = ta.get("candidates") or []
    warnings = ta.get("warnings") or []
    metrics = ta.get("setup_metrics") or []
    if not candidates and not warnings and not metrics:
        return [
            StrategySignal("ta_family_none", "TA 策略族", "TA", "30 setup 注册表", "neutral", 0.0, 0.85, "missing", "本地 TA 上下文不可用。", "ta.*")
        ]

    families = {
        "T": ("T 趋势", "突破/趋势延续/加速"),
        "P": ("P 回踩", "趋势内回踩与缺口回补"),
        "R": ("R 反转", "双底/头肩底/锤子线/支撑反弹"),
        "F": ("F 形态", "旗形/三角形/矩形整理"),
        "V": ("V 量价", "放量上行/缩量蓄势"),
        "S": ("S 板块", "SW L2 板块共振与补涨"),
        "C": ("C 筹码", "筹码集中与松动"),
        "O": ("O 订单流", "机构持续买入/龙虎榜/封单强度"),
        "Z": ("Z 统计", "Z-score/超跌/区间反转"),
        "E": ("E 事件", "公告与事件催化"),
        "D": ("D 顶部预警", "双顶/头肩顶/流星线风险"),
    }
    by_family: dict[str, dict[str, list[dict]]] = {
        code: {"candidates": [], "warnings": [], "metrics": []}
        for code in families
    }
    for row in candidates:
        code = str(row.get("setup_family_code") or row.get("setup_name", "")[:1])
        if code in by_family:
            by_family[code]["candidates"].append(row)
    for row in warnings:
        code = str(row.get("setup_family_code") or row.get("setup_name", "")[:1])
        if code in by_family:
            by_family[code]["warnings"].append(row)
    for row in metrics:
        code = str(row.get("setup_family_code") or row.get("setup_name", "")[:1])
        if code in by_family:
            by_family[code]["metrics"].append(row)

    signals: list[StrategySignal] = []
    for code, (label, algo) in families.items():
        group = by_family[code]
        n_cand = len(group["candidates"])
        n_warn = len(group["warnings"])
        n_metric = len(group["metrics"])
        if n_cand == 0 and n_warn == 0 and n_metric == 0:
            continue
        metric_score, metric_text = _ta_metric_edge(group["metrics"], params)
        if code == "D":
            score = -0.22 * min(n_warn, 3) + metric_score
        else:
            score = 0.16 * min(n_cand, 3) - 0.10 * min(n_warn, 3) + metric_score
        score = max(-0.60, min(0.65, score))
        direction = _direction(score, params)
        status: SignalStatus = "active" if n_cand or n_warn else "degraded"
        setup_names = _setup_names([*group["candidates"], *group["warnings"], *group["metrics"]])
        signals.append(
            StrategySignal(
                f"ta_family_{code}",
                label,
                "TA",
                algo,
                direction,
                score,
                0.72 if code == "D" else 0.90,
                status,
                f"命中 {n_cand} 个候选、{n_warn} 个预警、{n_metric} 条滚动指标；{metric_text}；setup: {setup_names}",
                "ta.candidates_daily / ta.warnings_daily / ta.setup_metrics_daily",
            )
        )
    return signals


def _ta_metric_edge(metrics: list[dict], params: dict) -> tuple[float, str]:
    winrates = [float(m["winrate_60d"]) for m in metrics if m.get("winrate_60d") is not None]
    decays = [float(m["decay_score"]) for m in metrics if m.get("decay_score") is not None]
    score = 0.0
    parts = []
    if winrates:
        avg_wr = sum(winrates) / len(winrates)
        smooth = _smooth(params)
        center = float(smooth.get("ta_winrate_center_pct", 25.0))
        scale = float(smooth.get("ta_winrate_scale_pct", 8.0))
        score += 0.16 * _tanh_scaled(avg_wr - center, scale)
        parts.append(f"60日胜率均值 {avg_wr:.1f}%")
    if decays:
        avg_decay = sum(decays) / len(decays)
        smooth = _smooth(params)
        center = float(smooth.get("ta_decay_center_pp", -5.0))
        scale = float(smooth.get("ta_decay_scale_pp", 8.0))
        score += 0.12 * _tanh_scaled(avg_decay - center, scale)
        parts.append(f"衰减均值 {avg_decay:+.1f}pp")
    return score, "，".join(parts) if parts else "暂无滚动胜率"


def _setup_names(rows: list[dict]) -> str:
    seen = []
    for row in rows:
        name = row.get("setup_label") or row.get("setup_name")
        if name and name not in seen:
            seen.append(str(name))
    return "、".join(seen[:5]) if seen else "—"


def _apply_param_weights(signal: StrategySignal, params: dict, snapshot: StockEdgeSnapshot | None = None) -> StrategySignal:
    cluster = _cluster_for_signal(signal.key)
    signal_weight = _configured_signal_weight(signal, params)
    cluster_weight = float((params.get("cluster_weights") or {}).get(cluster, 1.0))
    context_gate = _context_gate(cluster, params, snapshot)
    return replace(
        signal,
        cluster=cluster,
        weight=round(signal.weight * signal_weight * cluster_weight * context_gate, 6),
    )


def _configured_signal_weight(signal: StrategySignal, params: dict) -> float:
    if signal.key.startswith("ta_family_"):
        code = signal.key.removeprefix("ta_family_")
        return float((params.get("ta_family_weights") or {}).get(code, 1.0))
    return float((params.get("signal_weights") or {}).get(signal.key, 1.0))


def _context_gate(cluster: str, params: dict, snapshot: StockEdgeSnapshot | None) -> float:
    if snapshot is None:
        return 1.0
    gates = params.get("context_gates") or {}
    value = 1.0
    market_regime = _normalized_market_regime(snapshot)
    if market_regime:
        value *= float((gates.get("market_regime") or {}).get(market_regime, {}).get(cluster, 1.0))
    sw_phase = _normalized_sw_l2_phase(snapshot)
    if sw_phase:
        value *= float((gates.get("sw_l2_phase") or {}).get(sw_phase, {}).get(cluster, 1.0))
    return max(0.05, min(value, 3.0))


def _normalized_market_regime(snapshot: StockEdgeSnapshot) -> str | None:
    ta = snapshot.ta_context.data or {}
    regime = ta.get("regime") or {}
    text = " ".join(str(regime.get(key) or "") for key in ["regime", "regime_label", "market_regime", "label"]).lower()
    if not text:
        model_ctx = (snapshot.model_context.data if snapshot.model_context else None) or {}
        llm = model_ctx.get("llm_regime") or {}
        text = " ".join(str(llm.get(key) or "") for key in ["regime_label", "recommended_tilt"]).lower()
    if any(word in text for word in ["risk_on", "risk-on", "bull", "uptrend", "trend", "多头", "进攻", "主升"]):
        return "risk_on"
    if any(word in text for word in ["range", "sideway", "chop", "震荡", "箱体", "横盘"]):
        return "range_bound"
    if any(word in text for word in ["risk_off", "risk-off", "bear", "downtrend", "防守", "退潮", "空头"]):
        return "risk_off"
    return None


def _normalized_sw_l2_phase(snapshot: StockEdgeSnapshot) -> str | None:
    sector = snapshot.sector_membership.data or {}
    state = sector.get("sector_state") or {}
    text = str(state.get("cycle_phase") or state.get("phase") or "").lower()
    if any(word in text for word in ["acceler", "加速", "启动", "主升"]):
        return "acceleration"
    if any(word in text for word in ["diffusion", "扩散", "分歧", "轮动"]):
        return "diffusion"
    if any(word in text for word in ["climax", "高潮", "拥挤", "过热"]):
        return "climax"
    if any(word in text for word in ["retreat", "cool", "decline", "退潮", "冷却", "衰退"]):
        return "retreat"
    return None


def _cluster_for_signal(key: str) -> str:
    if key in {"trend_following", "breakout_pressure", "range_position", "volatility_contraction", "trend_quality_r2", "volume_price_divergence", "squeeze_breakout_classifier"} or key in {"ta_family_T", "ta_family_F", "ta_family_V"}:
        return "trend_breakout"
    if key in {"support_pullback", "pullback_rebound_classifier"} or key in {"ta_family_P"}:
        return "pullback_continuation"
    if key in {"drawdown_recovery", "candle_reversal_structure"} or key in {"ta_family_R", "ta_family_Z"}:
        return "reversal_mean_reversion"
    if key in {"moneyflow_7d", "orderflow_mix", "flow_persistence_decay", "lhb_institution_hotmoney_divergence", "northbound_regime", "block_trade_pressure", "event_catalyst_llm"} or key in {"ta_family_O"}:
        return "order_flow_smart_money"
    if key in {"limit_up_microstructure", "limit_up_event_path_model"}:
        return "trend_breakout"
    if key in {"smartmoney_sw_l2", "sector_diffusion_breadth"} or key in {"ta_family_S"}:
        return "sw_l2_sector_leadership"
    if key in {"same_sector_leadership", "peer_relative_momentum", "hierarchical_sector_shrinkage"}:
        return "sw_l2_sector_leadership"
    if key in {"fundamental_lineup", "daily_basic_style", "peer_leader_fundamental_spread", "peer_financial_alpha_model", "fundamental_price_dislocation_model", "peer_research_auto_trigger", "fundamental_contradiction_llm"} or key in {"ta_family_C", "ta_family_E"}:
        return "fundamentals_quality"
    if key in {"smartmoney_sector_ml", "ningbo_active_ml", "regime_adaptive_weight_model", "position_sizing_model", "right_tail_meta_gbm", "temporal_fusion_sequence_ranker", "target_stop_survival_model", "multi_horizon_target_classifier", "target_ladder_probability_model", "path_shape_mixture_model", "mfe_mae_surface_model", "model_stack_blender", "kronos_pattern", "analog_kronos_nearest_neighbors", "kronos_path_cluster_transition", "llm_regime_cache", "llm_counterfactual_cache", "historical_replay_edge", "target_stop_replay", "quantile_return_forecaster", "isotonic_score_calibrator", "strategy_validation_decay"}:
        return "model_ensemble"
    if key in {"volatility_structure", "gap_risk", "gap_risk_open_model", "liquidity_slippage", "conformal_return_band", "stop_first_classifier", "stop_loss_hazard_model", "market_margin_impulse"}:
        return "risk_warning"
    if key in {"intraday_profile", "entry_fill_replay", "entry_fill_classifier", "forward_entry_timing_model", "entry_price_surface_model", "t0_uplift", "volume_profile_support", "vwap_reclaim_execution", "auction_imbalance_proxy"}:
        return "intraday_t0_execution"
    if key in {"ta_family_D"}:
        return "risk_warning"
    return "misc"


def _cluster_label(cluster: str) -> str:
    return {
        "trend_breakout": "趋势/突破",
        "pullback_continuation": "回踩/延续",
        "reversal_mean_reversion": "反转/均值回归",
        "order_flow_smart_money": "订单流/聪明钱",
        "sw_l2_sector_leadership": "SW L2 板块",
        "fundamentals_quality": "基本面/质量",
        "intraday_t0_execution": "日内/T+0",
        "model_ensemble": "ML/Kronos",
        "risk_warning": "风险/预警",
        "misc": "其他",
    }.get(cluster, cluster)


def _smooth(params: dict) -> dict:
    return params.get("smooth_scoring") or {}


def _sigmoid(x: float) -> float:
    if x >= 40:
        return 1.0
    if x <= -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _tanh_scaled(value: float, scale: float) -> float:
    return math.tanh(value / max(scale, 1e-6))


def _sector_score(value: float, params: dict) -> float:
    smooth = _smooth(params)
    center = float(smooth.get("sector_score_center", 0.50))
    scale = float(smooth.get("sector_score_scale", 0.18))
    return _tanh_scaled(value - center, scale)


def _clip(value: float, low: float, high: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    if not math.isfinite(v):
        v = 0.0
    return max(low, min(high, v))


def _direction(score: float, params: dict) -> SignalDirection:
    neutral_band = float(_smooth(params).get("display_neutral_band", 0.08))
    if score >= neutral_band:
        return "positive"
    if score <= -neutral_band:
        return "negative"
    return "neutral"


def _smartmoney_sw_l2_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    sector = snapshot.sector_membership.data or {}
    l2_name = sector.get("l2_name")
    flow_rows = sector.get("sector_flow_7d") or []
    state = sector.get("sector_state") or {}
    factor = sector.get("sector_factor") or {}
    if not l2_name:
        return StrategySignal("smartmoney_sw_l2", "SW L2 板块资金", "资金", "SW L2 资金流 + 相位", "neutral", 0.0, 0.95, "missing", "缺少 SW L2 行业归属。", "smartmoney.sw_member_monthly")
    net_sum = sum(float(row.get("net_amount") or 0) for row in flow_rows)
    heat = _as_float(factor.get("heat_score"))
    trend = _as_float(factor.get("trend_score"))
    score = 0.0
    if flow_rows:
        flow_scale = float(_smooth(params).get("sector_flow_scale_wan", 500000.0))
        score += 0.24 * _tanh_scaled(net_sum, flow_scale)
    if heat is not None:
        score += 0.12 * _sector_score(heat, params)
    if trend is not None:
        score += 0.12 * _sector_score(trend, params)
    role = state.get("role") or "未知角色"
    phase = state.get("cycle_phase") or "未知相位"
    direction = _direction(score, params)
    status: SignalStatus = "active" if flow_rows or state or factor else "degraded"
    return StrategySignal(
        "smartmoney_sw_l2",
        "SW L2 板块资金",
        "资金",
        "SW L2 资金流 + 相位",
        direction,
        max(-0.45, min(0.50, score)),
        1.10,
        status,
        f"{l2_name} 近7日板块净流 {net_sum:.2f} 万元；角色 {role}；相位 {phase}。",
        "smartmoney.sector_moneyflow_sw_daily / sector_state_daily / factor_daily",
    )


def _sector_diffusion_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_sector_diffusion_profile(snapshot.sector_membership.data, params=params.get("sector_diffusion") or {})
    if not profile.available:
        return StrategySignal(
            "sector_diffusion_breadth",
            "SW L2 扩散宽度",
            "资金",
            "sector flow diffusion/crowding",
            "neutral",
            0.0,
            0.75,
            "degraded",
            profile.reason,
            "smartmoney.sector_moneyflow_sw_daily / factor_daily",
        )
    direction = _direction(profile.score, params)
    name = "板块扩散顺风" if direction == "positive" else ("板块退潮/拥挤" if direction == "negative" else "板块扩散中性")
    return StrategySignal(
        "sector_diffusion_breadth",
        name,
        "资金",
        "sector flow diffusion/crowding",
        direction,
        profile.score,
        0.82,
        "active",
        f"{profile.l2_name or 'SW L2'}：正流天数 {profile.positive_flow_share if profile.positive_flow_share is not None else 0:.0%}，近3日/前段 {profile.recent_vs_prior_flow_pct if profile.recent_vs_prior_flow_pct is not None else 0:+.1f}%，拥挤 {profile.crowding_score if profile.crowding_score is not None else 0:.2f}。",
        "smartmoney.sector_moneyflow_sw_daily / factor_daily",
        extra=profile.to_dict(),
    )


def _same_sector_leadership_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    sector = snapshot.sector_membership.data or {}
    leaders = sector.get("sector_leaders") or {}
    if not leaders:
        return StrategySignal("same_sector_leadership", "同板块相对位置", "SW L2", "四类龙头排名连续评分", "neutral", 0.0, 0.80, "missing", "同板块龙头数据不可用。", "smartmoney.sw_member_monthly")
    categories = {
        "size": ("市值", 0.22),
        "momentum": ("动量", 0.26),
        "moneyflow": ("资金", 0.28),
        "ta": ("TA", 0.24),
    }
    score = 0.0
    parts = []
    for key, (label, weight) in categories.items():
        rows = leaders.get(key) or []
        rank = _target_rank(rows)
        n = max(len(rows), 1)
        if rank is None:
            score -= 0.05 * weight
            continue
        # Rank 1 -> +1, last displayed rank -> close to 0, below top list -> small penalty.
        rank_score = 1.0 - (rank - 1) / max(n - 1, 1)
        centered = rank_score * 2.0 - 1.0
        score += weight * centered
        parts.append(f"{label}第{rank}")
    score = _clip(score, -0.35, 0.45)
    direction = _direction(score, params)
    name = "板块内领先" if direction == "positive" else ("板块内落后" if direction == "negative" else "板块内中性")
    return StrategySignal(
        "same_sector_leadership",
        name,
        "SW L2",
        "四类龙头排名连续评分",
        direction,
        score,
        0.90,
        "active",
        "、".join(parts) if parts else "目标股未进入同板块龙头列表。",
        "smartmoney + ta",
    )


def _peer_relative_momentum_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    sector = snapshot.sector_membership.data or {}
    leaders = sector.get("sector_leaders") or {}
    peer_rows = []
    for rows in leaders.values():
        peer_rows.extend(rows or [])
    unique: dict[str, dict] = {}
    for row in peer_rows:
        code = row.get("ts_code")
        if code:
            unique[str(code)] = row
    if not unique:
        return StrategySignal("peer_relative_momentum", "同行相对强弱", "SW L2", "5/10/15日同行动量连续评分", "neutral", 0.0, 0.80, "missing", "同行动量样本不可用。", "smartmoney.sw_member_monthly")
    target = unique.get(snapshot.ctx.request.ts_code)
    if not target:
        return StrategySignal("peer_relative_momentum", "同行相对强弱", "SW L2", "5/10/15日同行动量连续评分", "neutral", -0.04, 0.80, "degraded", "目标股未进入同行龙头样本。", "smartmoney.sw_member_monthly")
    score = 0.0
    parts = []
    for key, weight in [("return_5d_pct", 0.42), ("return_10d_pct", 0.34), ("return_15d_pct", 0.24)]:
        vals = [_as_float(row.get(key)) for row in unique.values()]
        vals = [v for v in vals if v is not None]
        tv = _as_float(target.get(key))
        if tv is None or len(vals) < 2:
            continue
        rank = sum(1 for v in vals if v <= tv) / len(vals)
        score += weight * (rank * 2.0 - 1.0)
        parts.append(f"{key.replace('return_', '').replace('_pct', '')}分位 {rank:.0%}")
    score = _clip(score * 0.32, -0.32, 0.32)
    direction = _direction(score, params)
    name = "同行强势" if direction == "positive" else ("同行弱势" if direction == "negative" else "同行中性")
    return StrategySignal("peer_relative_momentum", name, "SW L2", "5/10/15日同行动量连续评分", direction, score, 0.82, "active", "，".join(parts) if parts else "同行收益字段不足。", "smartmoney.raw_daily / sw_member_monthly")


def _peer_fundamental_spread_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_peer_fundamental_spread_profile(
        snapshot.sector_membership.data,
        snapshot.research_lineup.data,
        ts_code=snapshot.ctx.request.ts_code,
        params=params.get("peer_fundamental_spread") or {},
    )
    if not profile.available:
        return StrategySignal(
            "peer_leader_fundamental_spread",
            "同行龙头基本面对比",
            "基本面",
            "same-sector leader spread",
            "neutral",
            0.0,
            0.70,
            "degraded",
            profile.reason,
            "smartmoney.sw_member_monthly / raw_daily_basic / research.memory",
        )
    direction = _direction(profile.score, params)
    name = "同行相对质量占优" if direction == "positive" else ("同行相对弱势" if direction == "negative" else "同行基本面对比中性")
    if profile.target_in_leader_set:
        evidence = (
            f"同板块 {profile.peer_count} 个可见同行；财报综合分位 {profile.fundamental_percentile or 0:.0%}，"
            f"盈利 {profile.quality_percentile or 0:.0%}，成长 {profile.growth_percentile or 0:.0%}，现金 {profile.cash_percentile or 0:.0%}，"
            f"估值折价分 {profile.valuation_discount_score if profile.valuation_discount_score is not None else 0:+.2f}；"
            f"市值分位 {profile.size_percentile or 0:.0%}、动量分位 {profile.momentum_percentile or 0:.0%}仅作辅助，"
            f"Research 覆盖 {profile.research_coverage_score:.0%}。"
        )
    else:
        evidence = f"目标股未进入当前可见龙头样本；Research 覆盖 {profile.research_coverage_score:.0%}。"
    return StrategySignal(
        "peer_leader_fundamental_spread",
        name,
        "基本面",
        "same-sector leader spread",
        direction,
        profile.score,
        0.70,
        "active",
        evidence,
        "smartmoney.sw_member_monthly / raw_daily_basic / research.memory",
        extra=profile.to_dict(),
    )


def _peer_financial_alpha_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    cfg = params.get("peer_financial_alpha_model") or {}
    if not cfg.get("enabled", True):
        return StrategySignal("peer_financial_alpha_model", "同行财务Alpha", "基本面", "peer financial alpha model", "neutral", 0.0, 0.68, "degraded", "peer financial alpha model disabled。", "research.memory / sw_member_monthly")
    profile = build_peer_fundamental_spread_profile(
        snapshot.sector_membership.data,
        snapshot.research_lineup.data,
        ts_code=snapshot.ctx.request.ts_code,
        params=params.get("peer_fundamental_spread") or {},
    )
    if not profile.available or not profile.target_in_leader_set:
        return StrategySignal(
            "peer_financial_alpha_model",
            "同行财务Alpha不足",
            "基本面",
            "peer financial alpha model",
            "neutral",
            0.0,
            0.68,
            "degraded",
            profile.reason if not profile.available else "目标股不在可见同行样本内，无法稳定估计财务 alpha。",
            "research.memory / sw_member_monthly",
            extra=profile.to_dict() if profile.available else None,
        )
    f_pct = float(profile.fundamental_percentile or 0.5)
    q_pct = float(profile.quality_percentile or 0.5)
    g_pct = float(profile.growth_percentile or 0.5)
    c_pct = float(profile.cash_percentile or 0.5)
    mom_pct = float(profile.momentum_percentile or 0.5)
    val = float(profile.valuation_discount_score or 0.0)
    lag_alpha = _clip((f_pct - mom_pct) * float(cfg.get("price_lag_alpha_scale", 0.55)), -0.35, 0.35)
    quality_alpha = _clip((0.36 * (q_pct - 0.5) + 0.28 * (g_pct - 0.5) + 0.24 * (c_pct - 0.5) + 0.12 * val) * 2.0, -0.60, 0.60)
    coverage_gate = _clip(0.55 + float(profile.research_coverage_score) * 0.65, 0.35, 1.2)
    alpha_score = coverage_gate * (0.58 * quality_alpha + 0.32 * lag_alpha + 0.10 * (f_pct - 0.5) * 2.0)
    score = _clip(alpha_score, -0.42, 0.44)
    direction = _direction(score, params)
    expected_alpha_pct = _clip(score * float(cfg.get("alpha_pct_scale", 18.0)), -12.0, 14.0)
    return StrategySignal(
        "peer_financial_alpha_model",
        "同行财务Alpha占优" if direction == "positive" else ("同行财务Alpha落后" if direction == "negative" else "同行财务Alpha中性"),
        "基本面",
        "peer financial alpha model",
        direction,
        score,
        0.68,
        "active",
        f"同板块财报分位 {f_pct:.0%}，盈利/成长/现金 {q_pct:.0%}/{g_pct:.0%}/{c_pct:.0%}，价格动量分位 {mom_pct:.0%}，预期同行 alpha {expected_alpha_pct:+.1f}%。",
        "research.memory / smartmoney.sw_member_monthly",
        extra={**profile.to_dict(), "lag_alpha": round(lag_alpha, 4), "quality_alpha": round(quality_alpha, 4), "expected_alpha_pct": round(expected_alpha_pct, 4)},
    )


def _hierarchical_sector_shrinkage_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    sector = snapshot.sector_membership.data or {}
    leaders = sector.get("sector_leaders") or {}
    peer_rows = []
    for rows in leaders.values():
        peer_rows.extend(rows or [])
    unique: dict[str, dict] = {}
    for row in peer_rows:
        code = row.get("ts_code")
        if code:
            unique[str(code)] = row
    target = unique.get(snapshot.ctx.request.ts_code)
    factor = sector.get("sector_factor") or {}
    heat = _as_float(factor.get("heat_score"))
    persistence = _as_float(factor.get("persistence_score"))
    sector_prior = 0.0
    prior_parts = []
    if heat is not None:
        sector_prior += 0.45 * _sector_score(heat, params)
        prior_parts.append(f"行业热度 {heat:.2f}")
    if persistence is not None:
        sector_prior += 0.35 * _sector_score(persistence, params)
        prior_parts.append(f"持续性 {persistence:.2f}")
    peer_momentum = None
    if target and unique:
        vals = [_as_float(row.get("return_5d_pct")) for row in unique.values()]
        vals = [v for v in vals if v is not None]
        tv = _as_float(target.get("return_5d_pct"))
        if tv is not None and len(vals) >= 2:
            peer_rank = sum(1 for v in vals if v <= tv) / len(vals)
            peer_momentum = peer_rank * 2.0 - 1.0
            prior_parts.append(f"同行5日分位 {peer_rank:.0%}")
    history_rows = snapshot.daily_bars.rows
    cfg = params.get("hierarchical_sector_shrinkage") or {}
    shrink_min = float(cfg.get("min_full_weight_rows", 360.0))
    shrink_weight = _clip(1.0 - history_rows / max(shrink_min, 1.0), 0.0, 0.75)
    target_component = peer_momentum if peer_momentum is not None else 0.0
    score = _clip((1.0 - shrink_weight) * target_component * 0.22 + shrink_weight * sector_prior, -0.32, 0.35)
    if not prior_parts:
        return StrategySignal("hierarchical_sector_shrinkage", "行业层级收缩", "统计学习", "sector/style prior shrinkage", "neutral", 0.0, 0.52, "missing", "行业/同行先验不足。", "smartmoney.factor_daily / sw_member_monthly")
    direction = _direction(score, params)
    name = "行业先验支持" if direction == "positive" else ("行业先验拖累" if direction == "negative" else "行业先验中性")
    return StrategySignal(
        "hierarchical_sector_shrinkage",
        name,
        "统计学习",
        "sector/style prior shrinkage",
        direction,
        score,
        0.52,
        "active",
        f"历史 {history_rows} 根，收缩权重 {shrink_weight:.0%}；" + "，".join(prior_parts) + "。",
        "smartmoney.factor_daily / sw_member_monthly",
        extra={"history_rows": history_rows, "shrink_weight": round(shrink_weight, 4), "sector_prior": round(sector_prior, 4), "peer_component": round(target_component, 4)},
    )


def _target_rank(rows: list[dict]) -> int | None:
    for idx, row in enumerate(rows, start=1):
        if row.get("is_target"):
            return idx
    return None


def _daily_basic_style_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    df = snapshot.daily_basic.data
    if df is None or not hasattr(df, "empty") or df.empty:
        return StrategySignal("daily_basic_style", "交易质量/估值风格", "风格", "换手/量比/估值连续评分", "neutral", 0.0, 0.70, "missing", "daily_basic 不可用。", "smartmoney.raw_daily_basic")
    latest = df.sort_values("trade_date").iloc[-1] if "trade_date" in df.columns else df.iloc[-1]
    turnover = _as_float(latest.get("turnover_rate_f") if "turnover_rate_f" in latest else latest.get("turnover_rate"))
    volume_ratio = _as_float(latest.get("volume_ratio"))
    pe_ttm = _as_float(latest.get("pe_ttm"))
    pb = _as_float(latest.get("pb"))
    score = 0.0
    parts = []
    if turnover is not None:
        # Prefer tradable but not frantic turnover.
        score += 0.16 * math.exp(-((turnover - 4.0) / 5.0) ** 2) - 0.10 * _sigmoid((turnover - 18.0) / 4.0)
        parts.append(f"换手 {turnover:.2f}%")
    if volume_ratio is not None:
        score += 0.12 * _tanh_scaled(math.log(max(volume_ratio, 1e-6)), 0.55)
        parts.append(f"量比 {volume_ratio:.2f}")
    if pe_ttm is not None and pe_ttm > 0:
        score -= 0.08 * _sigmoid((pe_ttm - 90.0) / 25.0)
        parts.append(f"PE(TTM) {pe_ttm:.1f}")
    if pb is not None and pb > 0:
        score -= 0.06 * _sigmoid((pb - 10.0) / 3.0)
        parts.append(f"PB {pb:.1f}")
    score = _clip(score, -0.25, 0.25)
    direction = _direction(score, params)
    name = "交易风格友好" if direction == "positive" else ("估值/交易拥挤" if direction == "negative" else "交易风格中性")
    return StrategySignal("daily_basic_style", name, "风格", "换手/量比/估值连续评分", direction, score, 0.70, "active", "，".join(parts) if parts else "可用字段不足。", "smartmoney.raw_daily_basic")


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _fmt_optional_float(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "—"


def _fmt_signed_yuan_amount(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else "-"
    amount = abs(value)
    if amount >= 1e7:
        return f"{sign}{amount / 1e8:.2f} 亿"
    if amount >= 1e4:
        return f"{sign}{amount / 1e4:.1f} 万"
    return f"{sign}{amount:.0f} 元"


def _fundamental_lineup_signal(snapshot: StockEdgeSnapshot) -> StrategySignal:
    research = snapshot.research_lineup.data or {}
    n = len(research.get("annual_factors") or []) + len(research.get("quarterly_factors") or [])
    reports = len(research.get("recent_research_reports") or [])
    if n == 0 and reports == 0:
        return StrategySignal("fundamental_lineup", "基本面阵列", "基本面", "财报深度报告复用", "neutral", 0.0, 0.75, "missing", "本地尚无财报研究阵列。", "research.memory")
    return StrategySignal(
        "fundamental_lineup",
        "基本面阵列",
        "基本面",
        "财报深度报告复用",
        "positive",
        0.20,
        0.80,
        "active",
        f"已复用 {n} 条财报因子和 {reports} 条研报摘要。",
        "research.memory",
    )


def _fundamental_price_dislocation_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_fundamental_dislocation_profile(
        research_lineup=snapshot.research_lineup.data,
        daily_bars=snapshot.daily_bars.data,
        daily_basic=snapshot.daily_basic.data,
        sector_membership=snapshot.sector_membership.data,
        params=params.get("fundamental_price_dislocation_model") or {},
    )
    if not profile.available:
        return StrategySignal(
            "fundamental_price_dislocation_model",
            "财报-价格错配模型",
            "基本面",
            "fundamental strength vs price-extension model",
            "neutral",
            0.0,
            0.66,
            "degraded",
            profile.reason,
            "research.memory / smartmoney.raw_daily_basic / raw_daily",
        )
    strength = float(profile.fundamental_strength or 0.0)
    extension = float(profile.price_extension or 0.0)
    dislocation = float(profile.dislocation_score or 0.0)
    score = _clip(profile.score, -0.42, 0.42)
    direction = _direction(score, params)
    name = "财报强但价格未充分反映" if direction == "positive" else ("财报弱且价格已透支" if direction == "negative" else "财报价格匹配中性")
    peer_rel = profile.peer_relative_return_15d
    peer_text = f"，15日相对同行 {peer_rel:+.2f}%" if peer_rel is not None else ""
    return StrategySignal(
        "fundamental_price_dislocation_model",
        name,
        "基本面",
        "fundamental strength vs price-extension model",
        direction,
        score,
        0.66,
        "active",
        f"财报强度 {strength:+.2f}，价格透支 {extension:+.2f}，错配分 {dislocation:+.2f}{peer_text}；年报 {profile.latest_annual_period or '—'}，季报 {profile.latest_quarterly_period or '—'}。",
        "research.memory / smartmoney.raw_daily_basic / raw_daily",
        extra=profile.to_dict(),
    )


def _intraday_availability_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    result = snapshot.intraday_5min
    if result is None:
        return StrategySignal("intraday_profile", "日内结构", "执行", "5分钟画像", "neutral", 0.0, 0.35, "missing", "未启用 5分钟数据。", "duckdb/parquet")
    if result.ok:
        profile = build_intraday_profile(result.data, params=params.get("intraday_profile") or {})
        if not profile.available:
            return StrategySignal("intraday_profile", "日内结构", "执行", "VWAP/成交密集区画像", "neutral", 0.0, 0.40, "degraded", profile.reason, "duckdb/parquet")
        direction = _direction(profile.score, params)
        name = "日内承接友好" if direction == "positive" else ("日内压力偏重" if direction == "negative" else "日内结构中性")
        return StrategySignal(
            "intraday_profile",
            name,
            "执行",
            "VWAP/成交密集区画像",
            direction,
            profile.score,
            0.45,
            "active",
            f"VWAP {profile.vwap:.2f}，收盘/VWAP {profile.close_vs_vwap_pct:+.2f}%，下方量 {profile.lower_volume_share:.1%}，上方量 {profile.upper_volume_share:.1%}。",
            "duckdb/parquet",
            extra=profile.to_dict(),
        )
    return StrategySignal("intraday_profile", "日内结构", "执行", "5分钟画像", "neutral", 0.0, 0.35, "degraded", result.message or "5分钟数据不可用。", "duckdb/parquet")


def _vwap_profile(snapshot: StockEdgeSnapshot, params: dict):
    return build_vwap_execution_profile(
        snapshot.intraday_5min.data if snapshot.intraday_5min and snapshot.intraday_5min.ok else None,
        params=params.get("vwap_execution") or {},
    )


def _volume_profile_support_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = _vwap_profile(snapshot, params)
    if not profile.available:
        return StrategySignal(
            "volume_profile_support",
            "成交密集支撑",
            "执行",
            "intraday volume-profile support",
            "neutral",
            0.0,
            0.38,
            "degraded",
            profile.reason,
            "duckdb.intraday_5min",
        )
    direction = _direction(profile.volume_profile_support_score, params)
    name = "成本区承接" if direction == "positive" else ("成本区压力" if direction == "negative" else "成本区中性")
    return StrategySignal(
        "volume_profile_support",
        name,
        "执行",
        "intraday volume-profile support",
        direction,
        profile.volume_profile_support_score,
        0.42,
        "active",
        f"{profile.sample_count} 个分钟交易日；最新收盘/VWAP {profile.latest_close_vs_vwap_pct:+.2f}%，VWAP收复率 {profile.reclaim_rate:.0%}。",
        "duckdb.intraday_5min",
        extra=profile.to_dict(),
    )


def _vwap_reclaim_execution_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = _vwap_profile(snapshot, params)
    if not profile.available:
        return StrategySignal(
            "vwap_reclaim_execution",
            "VWAP 收复执行",
            "执行",
            "intraday VWAP reclaim",
            "neutral",
            0.0,
            0.38,
            "degraded",
            profile.reason,
            "duckdb.intraday_5min",
        )
    direction = _direction(profile.vwap_reclaim_score, params)
    name = "VWAP 收复" if direction == "positive" else ("VWAP 压制" if direction == "negative" else "VWAP 中性")
    latest = "是" if profile.latest_reclaimed else "否"
    return StrategySignal(
        "vwap_reclaim_execution",
        name,
        "执行",
        "intraday VWAP reclaim",
        direction,
        profile.vwap_reclaim_score,
        0.42,
        "active",
        f"{profile.sample_count} 个分钟交易日；收复 {profile.reclaim_days} 天，最新是否收复 {latest}。",
        "duckdb.intraday_5min",
        extra=profile.to_dict(),
    )


def _t0_uplift_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    profile = build_t0_uplift_profile(
        snapshot.intraday_5min.data if snapshot.intraday_5min and snapshot.intraday_5min.ok else None,
        snapshot.daily_bars.require(),
        params=params.get("t0_uplift") or {},
    )
    if not profile.available:
        return StrategySignal(
            "t0_uplift",
            "T+0 增益",
            "执行",
            "base-position T+0 uplift replay",
            "neutral",
            0.0,
            0.35,
            "degraded",
            profile.reason,
            "duckdb.intraday_5min / smartmoney.raw_daily",
        )
    direction = _direction(profile.score, params)
    name = "底仓 T+0 友好" if direction == "positive" else ("T+0 性价比弱" if direction == "negative" else "T+0 中性")
    return StrategySignal(
        "t0_uplift",
        name,
        "执行",
        "base-position T+0 uplift replay",
        direction,
        profile.score,
        0.42,
        "active",
        f"{profile.sample_count} 个样本；平均振幅 {profile.avg_intraday_range_pct:.2f}%，可捕捉 {profile.avg_reversal_capture_pct:.2f}%，扣费后增益 {profile.avg_uplift_pct:.2f}%，成功率 {profile.success_rate:.1%}。",
        profile.source,
        extra=profile.to_dict(),
    )


def _smartmoney_sector_ml_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    model_ctx = (snapshot.model_context.data if snapshot.model_context else None) or {}
    sm = model_ctx.get("smartmoney_sector") or {}
    if not sm.get("available"):
        return StrategySignal(
            "smartmoney_sector_ml",
            "SmartMoney 板块模型",
            "ML",
            "RF/XGB SW L2 模型复用",
            "neutral",
            0.0,
            0.75,
            "missing",
            sm.get("message") or "SmartMoney RF/XGB 板块模型未命中。",
            "smartmoney.ml",
        )
    rf = _as_float(sm.get("rf_proba"))
    xgb = _as_float(sm.get("xgb_proba"))
    probs = [v for v in [rf, xgb] if v is not None]
    avg = sum(probs) / len(probs) if probs else 0.5
    score = 0.50 * _tanh_scaled(avg - 0.50, 0.18)
    direction = _direction(score, params)
    return StrategySignal(
        "smartmoney_sector_ml",
        "SmartMoney 板块模型",
        "ML",
        "RF/XGB SW L2 模型复用",
        direction,
        _clip(score, -0.45, 0.50),
        0.90,
        "active",
        f"{sm.get('sector_name') or sm.get('sector_code')}：RF {_fmt_optional_float(rf)}，XGB {_fmt_optional_float(xgb)}，版本 {sm.get('version')}。",
        "smartmoney.ml.persistence / smartmoney.ml.features",
    )


def _ningbo_active_ml_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    model_ctx = (snapshot.model_context.data if snapshot.model_context else None) or {}
    nb = model_ctx.get("ningbo_candidate") or {}
    if not nb.get("available"):
        return StrategySignal(
            "ningbo_active_ml",
            "宁波主动模型",
            "ML",
            "active aggressive/conservative 复用",
            "neutral",
            0.0,
            0.70,
            "missing",
            nb.get("message") or "目标股未命中宁波候选池或 active 模型。",
            "ningbo.ml",
        )
    scores = [v for v in [nb.get("aggressive"), nb.get("conservative")] if v is not None]
    if not scores and nb.get("heuristic") is not None:
        scores = [nb["heuristic"]]
    avg = sum(float(v) for v in scores) / len(scores) if scores else 0.0
    # Ningbo scores can be calibrated probabilities or rank scores depending on
    # the active artifact; tanh keeps the signal continuous and bounded.
    score = 0.42 * _tanh_scaled(avg - 0.50, 0.22)
    direction = _direction(score, params)
    return StrategySignal(
        "ningbo_active_ml",
        "宁波主动模型",
        "ML",
        "active aggressive/conservative 复用",
        direction,
        _clip(score, -0.42, 0.42),
        0.85,
        "active",
        f"命中 {nb.get('candidate_count')} 条宁波候选；策略 {','.join(nb.get('strategies') or [])}；aggr={nb.get('aggressive')} cons={nb.get('conservative')}。",
        "ningbo.candidates_daily / ningbo.ml.dual_scorer",
    )


def _kronos_pattern_signal(snapshot: StockEdgeSnapshot) -> StrategySignal:
    model_ctx = (snapshot.model_context.data if snapshot.model_context else None) or {}
    kronos = model_ctx.get("kronos") or {}
    if not kronos.get("available"):
        return StrategySignal(
            "kronos_pattern",
            "Kronos K线表征",
            "DL",
            "Kronos tokenizer embedding cache",
            "neutral",
            0.0,
            0.45,
            "missing",
            kronos.get("message") or "目标股无 Kronos embedding cache。",
            "ningbo.kronos",
        )
    return StrategySignal(
        "kronos_pattern",
        "Kronos K线表征",
        "DL",
        "Kronos tokenizer embedding cache",
        "neutral",
        0.0,
        0.45,
        "active",
        f"已复用 {kronos.get('model_id')}；{kronos.get('lookback_bars')} 日窗口，{kronos.get('embedding_dim')} 维表征。当前仅作为模型证据，不单独加方向分。",
        "ningbo.ml.kronos_features",
    )


def _kronos_analog_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    model_ctx = (snapshot.model_context.data if snapshot.model_context else None) or {}
    analog = model_ctx.get("kronos_analog") or {}
    if not analog.get("available"):
        return StrategySignal(
            "analog_kronos_nearest_neighbors",
            "Kronos 相似形态近邻",
            "DL",
            "cosine nearest-neighbor path labelling",
            "neutral",
            0.0,
            0.78,
            "missing",
            analog.get("message") or "Kronos 近邻不可用。",
            "ningbo.kronos cache / smartmoney.raw_daily",
        )
    hit50 = float(analog.get("hit_50pct_40d") or 0.0)
    hit30 = float(analog.get("hit_30pct_40d") or 0.0)
    exp40 = float(analog.get("expected_return_40d") or 0.0)
    drawdown = float(analog.get("avg_drawdown_40d") or 0.0)
    stop_first = float(analog.get("stop_12pct_first_rate") or 0.0)
    sim = float(analog.get("avg_similarity") or 0.0)
    score = (
        0.32 * _tanh_scaled((hit50 - 0.08) * 100.0, 10.0)
        + 0.28 * _tanh_scaled((hit30 - 0.18) * 100.0, 14.0)
        + 0.25 * _tanh_scaled(exp40 * 100.0, 12.0)
        + 0.10 * _tanh_scaled((sim - 0.70) * 100.0, 8.0)
        - 0.24 * _tanh_scaled(stop_first * 100.0, 18.0)
        + 0.08 * _tanh_scaled(drawdown * 100.0, 10.0)
    )
    direction = _direction(score, params)
    return StrategySignal(
        "analog_kronos_nearest_neighbors",
        "Kronos 近邻右尾顺风" if direction == "positive" else ("Kronos 近邻右尾不足" if direction == "negative" else "Kronos 近邻中性"),
        "DL",
        "cosine nearest-neighbor path labelling",
        direction,
        _clip(score, -0.45, 0.48),
        0.78,
        "active",
        (
            f"{analog.get('analog_count')} 个无前视近邻，平均相似度 {sim:.2f}；"
            f"40日预期 {exp40:+.1%}，30%/50%右尾命中 {hit30:.1%}/{hit50:.1%}，"
            f"12%止损先触发 {stop_first:.1%}。"
        ),
        "ningbo.kronos cache / smartmoney.raw_daily",
        extra=analog,
    )


def _kronos_path_cluster_transition_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    model_ctx = (snapshot.model_context.data if snapshot.model_context else None) or {}
    analog = model_ctx.get("kronos_analog") or {}
    dist = analog.get("path_cluster_distribution") or {}
    if not analog.get("available") or not dist:
        return StrategySignal(
            "kronos_path_cluster_transition",
            "Kronos 路径簇转移",
            "DL",
            "analog path-cluster transition",
            "neutral",
            0.0,
            0.68,
            "missing",
            analog.get("message") or "Kronos 路径簇不可用。",
            "ningbo.kronos cache / smartmoney.raw_daily",
        )
    edge = float(analog.get("path_cluster_edge") or 0.0)
    right_tail = float(dist.get("right_tail") or 0.0)
    swing_up = float(dist.get("swing_up") or 0.0)
    stop_first = float(dist.get("stop_first") or 0.0)
    fade = float(dist.get("pop_and_fade") or 0.0)
    score = 0.70 * _tanh_scaled(edge * 100.0, 18.0) + 0.18 * _tanh_scaled((right_tail + swing_up - stop_first - fade) * 100.0, 22.0)
    direction = _direction(score, params)
    dominant = str(analog.get("dominant_path_cluster") or "range_chop")
    labels = {
        "right_tail": "右尾爆发",
        "swing_up": "波段上行",
        "grind_up": "缓慢上行",
        "pop_and_fade": "冲高回落",
        "range_chop": "区间震荡",
        "stop_first": "止损优先",
    }
    return StrategySignal(
        "kronos_path_cluster_transition",
        f"Kronos 路径簇：{labels.get(dominant, dominant)}",
        "DL",
        "analog path-cluster transition",
        direction,
        _clip(score, -0.42, 0.44),
        0.68,
        "active",
        (
            f"主导路径簇 {labels.get(dominant, dominant)}；右尾/波段 {right_tail:.1%}/{swing_up:.1%}，"
            f"冲高回落/止损优先 {fade:.1%}/{stop_first:.1%}。"
        ),
        "ningbo.kronos cache / smartmoney.raw_daily",
        extra={"distribution": dist, "dominant": dominant, "edge": round(edge, 6), "analog_count": analog.get("analog_count")},
    )


def _peer_research_auto_trigger_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    prefetch = snapshot.research_prefetch
    data = (prefetch.data if prefetch else None) or {}
    items = data.get("items") or []
    failures = data.get("failures") or []
    lineup = snapshot.research_lineup.data or {}
    deep_rows = len(lineup.get("annual_factors") or []) + len(lineup.get("quarterly_factors") or [])
    if not prefetch and deep_rows <= 0:
        return StrategySignal(
            "peer_research_auto_trigger",
            "同行 Research 触发未运行",
            "基本面",
            "research deep dependency orchestration",
            "neutral",
            0.0,
            0.40,
            "missing",
            "本次快照没有 Research prefetch 结果，也没有本地深度因子。",
            "research.report_runs / research.memory",
        )
    success_count = sum(1 for item in items if item.get("status") in {"reused", "generated", "ok"} or item.get("record_id"))
    total = max(len(items), success_count + len(failures), 1)
    success_rate = success_count / total
    coverage = min(1.0, deep_rows / 12.0)
    score = 0.26 * _tanh_scaled((coverage - 0.45) * 100.0, 24.0) + 0.18 * _tanh_scaled((success_rate - 0.65) * 100.0, 18.0)
    if failures:
        score -= min(0.22, len(failures) / total * 0.28)
    direction = _direction(score, params)
    return StrategySignal(
        "peer_research_auto_trigger",
        "同行 Research 覆盖充分" if direction == "positive" else ("同行 Research 覆盖缺口" if direction == "negative" else "同行 Research 覆盖中性"),
        "基本面",
        "research deep dependency orchestration",
        direction,
        _clip(score, -0.30, 0.32),
        0.40,
        "active" if success_count or deep_rows else "degraded",
        f"本地深度因子 {deep_rows} 条；prefetch 成功 {success_count}/{total}，失败 {len(failures)}。",
        "research.report_runs / research.memory",
        extra={"deep_factor_rows": deep_rows, "prefetch_success_count": success_count, "prefetch_failure_count": len(failures), "success_rate": round(success_rate, 4)},
    )


def _fundamental_contradiction_llm_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    lineup = snapshot.research_lineup.data or {}
    model_ctx = (snapshot.model_context.data if snapshot.model_context else None) or {}
    cf = model_ctx.get("llm_counterfactual") or {}
    annual = len(lineup.get("annual_factors") or [])
    quarterly = len(lineup.get("quarterly_factors") or [])
    if not cf.get("available") and annual + quarterly <= 0:
        return StrategySignal(
            "fundamental_contradiction_llm",
            "基本面矛盾审计",
            "LLM",
            "cached LLM counterfactual plus Research memory audit",
            "neutral",
            0.0,
            0.52,
            "missing",
            "缺少 Research 深度因子和 SmartMoney LLM 反事实缓存。",
            "research.memory / smartmoney.llm_counterfactuals",
        )
    daily = snapshot.daily_bars.require()
    ret20 = 0.0
    if len(daily) >= 21:
        close = pd.to_numeric(daily["close"], errors="coerce").dropna()
        if len(close) >= 21 and float(close.iloc[-21]) > 0:
            ret20 = float(close.iloc[-1] / close.iloc[-21] - 1.0)
    moneyflow = snapshot.moneyflow.data
    net_flow = 0.0
    if moneyflow is not None and not moneyflow.empty and "net_mf_amount" in moneyflow.columns:
        net_flow = float(pd.to_numeric(moneyflow["net_mf_amount"], errors="coerce").fillna(0.0).tail(7).sum())
    coverage = min(1.0, (annual + quarterly) / 12.0)
    verdict_text = " ".join(str(cf.get(k) or "") for k in ["robustness_verdict", "counterfactual_narrative", "risk_factors"]).lower()
    fragile = any(word in verdict_text for word in ["fragile", "weak", "risk", "invalid", "脆弱", "失效", "风险", "恶化"])
    robust = any(word in verdict_text for word in ["robust", "resilient", "strong", "韧性", "稳健", "强化"])
    market_conflict = coverage * (0.18 if ret20 < -0.08 and net_flow < 0 else 0.0)
    score = 0.16 * coverage + 0.16 * float(robust) - 0.24 * float(fragile) - market_conflict
    direction = _direction(score, params)
    return StrategySignal(
        "fundamental_contradiction_llm",
        "基本面与交易行为一致" if direction == "positive" else ("基本面/交易行为矛盾" if direction == "negative" else "基本面矛盾中性"),
        "LLM",
        "cached LLM counterfactual plus Research memory audit",
        direction,
        _clip(score, -0.34, 0.32),
        0.52,
        "active",
        f"Research 深度因子 {annual + quarterly} 条，20日涨跌 {ret20:+.1%}，7日主力净流 {net_flow:+.0f} 万；LLM 反事实 verdict={cf.get('robustness_verdict') or '—'}。",
        "research.memory / smartmoney.llm_counterfactuals",
        extra={"deep_factor_rows": annual + quarterly, "return_20d": round(ret20, 4), "net_flow_7d_wan": round(net_flow, 2), "counterfactual_available": bool(cf.get("available")), "fragile": fragile, "robust": robust},
    )


def _strategy_validation_decay_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    ta = snapshot.ta_context.data or {}
    profile = build_validation_decay_profile(ta.get("setup_metrics") or [], params=params.get("strategy_validation_decay") or {})
    if not profile.available:
        return StrategySignal(
            "strategy_validation_decay",
            "策略验证衰减",
            "统计学习",
            "rolling setup validation meta-score",
            "neutral",
            0.0,
            0.62,
            "degraded",
            profile.reason,
            "ta.setup_metrics_daily",
        )
    direction = _direction(profile.score, params)
    name = "滚动验证顺风" if direction == "positive" else ("滚动验证衰减" if direction == "negative" else "滚动验证中性")
    return StrategySignal(
        "strategy_validation_decay",
        name,
        "统计学习",
        "rolling setup validation meta-score",
        direction,
        profile.score,
        0.68,
        "active",
        f"{profile.sample_count} 条 setup 指标；60日胜率 {profile.avg_winrate_60d if profile.avg_winrate_60d is not None else 0:.1f}%，综合分 {profile.avg_combined_score_60d if profile.avg_combined_score_60d is not None else 0:+.2f}，衰减 {profile.avg_decay_score if profile.avg_decay_score is not None else 0:+.1f}pp。",
        "ta.setup_metrics_daily",
        extra=profile.to_dict(),
    )


def _llm_regime_cache_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    model_ctx = (snapshot.model_context.data if snapshot.model_context else None) or {}
    regime = model_ctx.get("llm_regime") or {}
    if not regime.get("available"):
        return StrategySignal(
            "llm_regime_cache",
            "LLM市场体制解释",
            "LLM",
            "SmartMoney LLM regime cache",
            "neutral",
            0.0,
            0.45,
            "missing",
            regime.get("message") or "无本地 LLM regime cache。",
            "smartmoney.llm_regime_states",
        )
    label = str(regime.get("regime_label") or "")
    tilt = str(regime.get("recommended_tilt") or "")
    text = f"{label} {tilt}".lower()
    score = 0.0
    if any(k in text for k in ["bull", "risk_on", "进攻", "多头", "主升", "risk-on"]):
        score += 0.18
    if any(k in text for k in ["bear", "risk_off", "防守", "退潮", "空头", "risk-off"]):
        score -= 0.22
    confidence = _as_float(regime.get("confidence")) or 0.5
    score *= max(0.2, min(confidence, 1.0))
    direction = _direction(score, params)
    name = "LLM体制顺风" if direction == "positive" else ("LLM体制逆风" if direction == "negative" else "LLM体制中性")
    return StrategySignal(
        "llm_regime_cache",
        name,
        "LLM",
        "SmartMoney LLM regime cache",
        direction,
        _clip(score, -0.24, 0.20),
        0.45,
        "active",
        f"体制 {regime.get('regime_label') or '—'}；置信度 {confidence:.2f}；模型 {regime.get('model_used') or '—'}。",
        "smartmoney.llm_regime_states",
    )


def _llm_counterfactual_cache_signal(snapshot: StockEdgeSnapshot, params: dict) -> StrategySignal:
    model_ctx = (snapshot.model_context.data if snapshot.model_context else None) or {}
    cf = model_ctx.get("llm_counterfactual") or {}
    if not cf.get("available"):
        return StrategySignal(
            "llm_counterfactual_cache",
            "LLM反事实韧性",
            "LLM",
            "SmartMoney LLM counterfactual cache",
            "neutral",
            0.0,
            0.45,
            "missing",
            cf.get("message") or "目标股无本地 LLM counterfactual cache。",
            "smartmoney.llm_counterfactuals",
        )
    verdict = str(cf.get("robustness_verdict") or "").lower()
    if any(k in verdict for k in ["strong", "robust", "high", "韧性强"]):
        score = 0.18
    elif any(k in verdict for k in ["weak", "fragile", "low", "脆弱"]):
        score = -0.22
    else:
        score = 0.0
    direction = _direction(score, params)
    name = "反事实韧性强" if direction == "positive" else ("反事实脆弱" if direction == "negative" else "反事实中性")
    return StrategySignal(
        "llm_counterfactual_cache",
        name,
        "LLM",
        "SmartMoney LLM counterfactual cache",
        direction,
        _clip(score, -0.24, 0.20),
        0.45,
        "active",
        f"反事实 verdict={cf.get('robustness_verdict') or '—'}；角色 {cf.get('role') or '—'}；模型 {cf.get('model_used') or '—'}。",
        "smartmoney.llm_counterfactuals",
    )
