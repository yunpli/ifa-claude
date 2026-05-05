"""Three-horizon Stock Edge v2.2 decision layer.

The decision layer is deliberately separate from the legacy ``TradePlan`` and
40d prediction surface. It converts the existing strategy matrix into three
user-facing, auditable decisions for 5 / 10 / 20 trading days.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from ifa.core.report.timezones import bjt_now
from ifa.families.stock.data.snapshot import StockEdgeSnapshot
from ifa.families.stock.features import build_support_resistance, compute_technical_summary
from ifa.families.stock.features.support_resistance import nearest_resistance, nearest_support
from ifa.families.stock.plan import TradePlan

DecisionAction = Literal["buy", "wait", "watch", "hold", "reduce", "sell", "avoid", "no_action"]
Level = Literal["high", "medium", "low"]
RiskLevel = Literal["low", "medium", "high", "extreme"]


@dataclass(frozen=True)
class DecisionProbability:
    value: float | None
    calibrated: bool
    label: str
    source: str


@dataclass(frozen=True)
class DecisionPriceZone:
    low: float | None
    high: float | None
    basis: str


@dataclass(frozen=True)
class DecisionPriceLevel:
    price: float | None
    basis: str


@dataclass(frozen=True)
class DecisionSignal:
    key: str
    label: str
    score: float
    direction: str
    status: str
    evidence: str
    source: str
    role: str = ""


@dataclass(frozen=True)
class HorizonDecision:
    horizon: str
    horizon_label: str
    decision: DecisionAction
    user_facing_label: str
    decision_summary: str
    confidence_level: Level
    risk_level: RiskLevel
    score: float
    score_type: str
    score_explanation: str
    probability_estimates: dict[str, DecisionProbability]
    probability_display_warning: str
    buy_zone: DecisionPriceZone
    chase_warning_price: float | None
    stop_loss: DecisionPriceLevel
    first_take_profit: DecisionPriceZone
    target_zone: DecisionPriceZone
    invalidation_condition: list[str]
    suggested_action: str
    if_already_holding: str
    if_not_holding: str
    key_supporting_signals: list[DecisionSignal]
    key_risk_signals: list[DecisionSignal]
    model_contributors: list[DecisionSignal]
    opposing_models: list[DecisionSignal]
    conflict_notes: str
    data_quality: dict[str, Any]
    missing_data_notes: list[str]
    as_of_trade_date: str
    data_cutoff: str
    generated_at: str
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


HORIZON_META = {
    "5d": ("一周内短线", "execution_score", "短线执行分衡量入场、追高、跳空、分时承接、流动性与止损空间，不是上涨概率。"),
    "10d": ("两周短波段", "swing_score", "短波段综合分衡量资金持续、板块扩散、同行强弱和目标/止损路径，不是确定性概率。"),
    "20d": ("一个月波段", "position_score", "20日波段评分衡量趋势质量、板块顺风、目标/止损、MFE/MAE 与仓位适配，不是长期投资评级。"),
}

DEFAULT_KEYS = {
    "5d": {
        "positive": [
            "entry_fill_replay",
            "entry_fill_classifier",
            "forward_entry_timing_model",
            "entry_price_surface_model",
            "support_pullback",
            "momentum_5d",
            "moneyflow_7d",
            "orderflow_mix",
            "limit_up_microstructure",
            "limit_up_event_path_model",
            "lhb_institution_hotmoney_divergence",
            "intraday_profile",
            "volume_profile_support",
            "vwap_reclaim_execution",
            "ta_family_P",
            "ta_family_V",
            "ta_family_O",
            "ta_family_E",
        ],
        "risk": ["gap_risk", "gap_risk_open_model", "liquidity_slippage", "stop_first_classifier", "ta_family_D", "block_trade_pressure"],
    },
    "10d": {
        "positive": [
            "trend_following",
            "support_pullback",
            "breakout_pressure",
            "volume_confirmation",
            "trend_quality_r2",
            "volume_price_divergence",
            "moneyflow_7d",
            "orderflow_mix",
            "flow_persistence_decay",
            "smartmoney_sw_l2",
            "sector_diffusion_breadth",
            "same_sector_leadership",
            "peer_relative_momentum",
            "target_stop_replay",
            "target_stop_survival_model",
            "path_shape_mixture_model",
            "mfe_mae_surface_model",
            "pullback_rebound_classifier",
            "squeeze_breakout_classifier",
            "smartmoney_sector_ml",
            "kronos_path_cluster_transition",
            "strategy_validation_decay",
            "regime_adaptive_weight_model",
            "ta_family_T",
            "ta_family_P",
            "ta_family_F",
            "ta_family_V",
            "ta_family_S",
            "ta_family_C",
            "ta_family_O",
            "ta_family_Z",
        ],
        "risk": ["stop_first_classifier", "liquidity_slippage", "gap_risk_open_model", "daily_basic_style", "ta_family_D", "strategy_validation_decay"],
    },
    "20d": {
        "positive": [
            "trend_following",
            "trend_quality_r2",
            "range_position",
            "drawdown_recovery",
            "smartmoney_sw_l2",
            "sector_diffusion_breadth",
            "same_sector_leadership",
            "peer_relative_momentum",
            "target_stop_replay",
            "quantile_return_forecaster",
            "conformal_return_band",
            "right_tail_meta_gbm",
            "target_stop_survival_model",
            "multi_horizon_target_classifier",
            "target_ladder_probability_model",
            "mfe_mae_surface_model",
            "position_sizing_model",
            "model_stack_blender",
            "smartmoney_sector_ml",
            "analog_kronos_nearest_neighbors",
            "kronos_path_cluster_transition",
            "temporal_fusion_sequence_ranker",
            "strategy_validation_decay",
            "regime_adaptive_weight_model",
            "fundamental_price_dislocation_model",
            "fundamental_contradiction_llm",
        ],
        "risk": ["stop_first_classifier", "stop_loss_hazard_model", "mfe_mae_surface_model", "liquidity_slippage", "strategy_validation_decay", "fundamental_contradiction_llm", "ta_family_D"],
    },
}


def build_decision_layer(
    snapshot: StockEdgeSnapshot,
    plan: TradePlan,
    *,
    strategy_matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build structured 5d/10d/20d decisions from current local evidence."""
    from ifa.families.stock.strategies import compute_strategy_matrix

    matrix = strategy_matrix or compute_strategy_matrix(snapshot)
    daily = snapshot.daily_bars.require()
    tech = compute_technical_summary(daily)
    levels = build_support_resistance(daily)
    support = nearest_support(levels, tech.close)
    resistance = nearest_resistance(levels, tech.close)
    params = snapshot.ctx.params.get("decision_layer", {})
    signal_map = {str(s.get("key")): s for s in matrix.get("signals") or []}
    generated_at = bjt_now().isoformat()
    decisions = {}
    for horizon in ("5d", "10d", "20d"):
        decisions[f"decision_{horizon}"] = _build_horizon_decision(
            horizon,
            snapshot=snapshot,
            plan=plan,
            matrix=matrix,
            signal_map=signal_map,
            tech=tech,
            support=support,
            resistance=resistance,
            params=params,
            generated_at=generated_at,
        ).to_dict()
    _attach_cross_horizon_conflicts(decisions)
    return {
        **decisions,
        "model_conflicts": _model_conflicts(decisions),
        "legacy_40d_audit": _legacy_audit(plan),
        "version": "stock_edge_v2.2_decision_layer_v1",
    }


def _build_horizon_decision(
    horizon: str,
    *,
    snapshot: StockEdgeSnapshot,
    plan: TradePlan,
    matrix: dict[str, Any],
    signal_map: dict[str, dict[str, Any]],
    tech,
    support,
    resistance,
    params: dict[str, Any],
    generated_at: str,
) -> HorizonDecision:
    cfg = _horizon_cfg(params, horizon)
    label, score_type, explanation = HORIZON_META[horizon]
    positive_keys = _configured_keys(cfg, "positive", DEFAULT_KEYS[horizon]["positive"])
    risk_keys = _configured_keys(cfg, "risk", DEFAULT_KEYS[horizon]["risk"])
    score, debug = _horizon_score(signal_map, positive_keys=positive_keys, risk_keys=risk_keys, cfg=cfg)
    data_quality = _data_quality(snapshot, horizon)
    risk_level = _risk_level(score, signal_map, risk_keys, plan, cfg, data_quality)
    confidence = _confidence(score, signal_map, positive_keys, risk_keys, risk_level, data_quality, cfg)
    action = _decision_action(score, risk_level, confidence, snapshot.ctx.request.has_base_position, cfg)
    prices = _price_rules(horizon, tech=tech, support=support, resistance=resistance, plan=plan, cfg=cfg)
    supporting = _signal_rows(signal_map, positive_keys, positive=True)[: int(cfg.get("max_display_signals", 5))]
    risks = _signal_rows(signal_map, risk_keys, positive=False)[: int(cfg.get("max_display_signals", 5))]
    contributors = _contributors(signal_map, positive_keys + risk_keys)[: int(cfg.get("max_audit_signals", 12))]
    opposing = [row for row in contributors if row.score < 0][: int(cfg.get("max_display_signals", 5))]
    probabilities = _probability_estimates(horizon, plan)
    warning = _probability_warning(plan)
    user_label = _user_label(action, horizon)
    summary = _summary(horizon, action, score, risk_level, confidence)
    missing_notes = _missing_data_notes(snapshot, horizon)
    conflict = _conflict_note(supporting, risks, confidence)
    return HorizonDecision(
        horizon=horizon,
        horizon_label=label,
        decision=action,
        user_facing_label=user_label,
        decision_summary=summary,
        confidence_level=confidence,
        risk_level=risk_level,
        score=round(score, 4),
        score_type=str(cfg.get("score_type", score_type)),
        score_explanation=str(cfg.get("score_explanation", explanation)),
        probability_estimates=probabilities,
        probability_display_warning=warning,
        buy_zone=prices["buy_zone"],
        chase_warning_price=prices["chase_warning_price"],
        stop_loss=prices["stop_loss"],
        first_take_profit=prices["first_take_profit"],
        target_zone=prices["target_zone"],
        invalidation_condition=prices["invalidation_condition"],
        suggested_action=_suggested_action(action, horizon, risk_level),
        if_already_holding=_holding_action(action, risk_level, prices["stop_loss"].price),
        if_not_holding=_not_holding_action(action, prices["buy_zone"], prices["chase_warning_price"]),
        key_supporting_signals=supporting,
        key_risk_signals=risks,
        model_contributors=contributors,
        opposing_models=opposing,
        conflict_notes=conflict,
        data_quality=data_quality,
        missing_data_notes=missing_notes,
        as_of_trade_date=str(snapshot.ctx.as_of.as_of_trade_date),
        data_cutoff=snapshot.ctx.as_of.data_cutoff_at_bjt.isoformat(),
        generated_at=generated_at,
        debug=debug,
    )


def _horizon_cfg(params: dict[str, Any], horizon: str) -> dict[str, Any]:
    common = params.get("common") or {}
    horizon_cfg = ((params.get("horizons") or {}).get(horizon) or {}).copy()
    for key in ["score_to_label", "risk_level_mapping", "confidence_mapping", "conflict_thresholds", "probability_display"]:
        horizon_cfg.setdefault(key, common.get(key, {}))
    return horizon_cfg


def _configured_keys(cfg: dict[str, Any], group: str, default: list[str]) -> list[str]:
    keys = (cfg.get("signal_groups") or {}).get(group)
    return list(keys) if isinstance(keys, list) and keys else list(default)


def _horizon_score(
    signal_map: dict[str, dict[str, Any]],
    *,
    positive_keys: list[str],
    risk_keys: list[str],
    cfg: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    weights = cfg.get("weights") or {}
    raw = 0.0
    denom = 0.0
    active = 0
    for key in positive_keys:
        signal = signal_map.get(key)
        if not signal or signal.get("status") == "missing":
            continue
        weight = float(weights.get(key, 1.0))
        raw += float(signal.get("score") or 0.0) * weight
        denom += abs(weight)
        active += 1
    for key in risk_keys:
        signal = signal_map.get(key)
        if not signal or signal.get("status") == "missing":
            continue
        weight = float(weights.get(key, weights.get("risk_penalty_weight", 1.0)))
        raw += float(signal.get("score") or 0.0) * weight
        denom += abs(weight)
        active += 1
    edge = raw / denom if denom else 0.0
    base = float(cfg.get("base_score", 0.50))
    scale = float(cfg.get("raw_edge_scale", 0.50))
    score = _clip(base + edge * scale, 0.0, 1.0)
    return score, {"raw_edge": round(edge, 4), "active_signal_count": active, "weight_denom": round(denom, 4)}


def _risk_level(
    score: float,
    signal_map: dict[str, dict[str, Any]],
    risk_keys: list[str],
    plan: TradePlan,
    cfg: dict[str, Any],
    data_quality: dict[str, Any],
) -> RiskLevel:
    risk_cfg = cfg.get("risk") or {}
    negative_pressure = 0.0
    for key in risk_keys:
        signal = signal_map.get(key)
        if not signal or signal.get("status") == "missing":
            continue
        negative_pressure = max(negative_pressure, max(0.0, -float(signal.get("score") or 0.0)))
    if plan.vetoes:
        negative_pressure = max(negative_pressure, float(risk_cfg.get("veto_pressure", 0.75)))
    if data_quality.get("status") in {"degraded", "missing"}:
        negative_pressure = max(negative_pressure, float(risk_cfg.get("degraded_pressure", 0.45)))
    if negative_pressure >= float(risk_cfg.get("extreme_pressure", 0.70)) or score <= float(risk_cfg.get("extreme_score", 0.25)):
        return "extreme"
    if negative_pressure >= float(risk_cfg.get("high_pressure", 0.38)) or score <= float(risk_cfg.get("high_risk_score", 0.38)):
        return "high"
    if negative_pressure >= float(risk_cfg.get("medium_pressure", 0.18)) or score <= float(risk_cfg.get("medium_risk_score", 0.48)):
        return "medium"
    return "low"


def _confidence(
    score: float,
    signal_map: dict[str, dict[str, Any]],
    positive_keys: list[str],
    risk_keys: list[str],
    risk_level: RiskLevel,
    data_quality: dict[str, Any],
    cfg: dict[str, Any],
) -> Level:
    active_positive = sum(1 for key in positive_keys if (signal_map.get(key) or {}).get("status") == "active")
    active_risk = sum(1 for key in risk_keys if (signal_map.get(key) or {}).get("status") == "active")
    pos_pressure = sum(max(0.0, float((signal_map.get(key) or {}).get("score") or 0.0)) for key in positive_keys)
    neg_pressure = sum(max(0.0, -float((signal_map.get(key) or {}).get("score") or 0.0)) for key in risk_keys + positive_keys)
    conflict = min(pos_pressure, neg_pressure)
    thresholds = cfg.get("confidence_mapping") or {}
    if data_quality.get("status") in {"degraded", "missing"} or risk_level == "extreme":
        return "low"
    if conflict >= float(thresholds.get("conflict_low", 0.70)):
        return "low"
    if active_positive >= int(thresholds.get("high_min_active", 5)) and active_risk <= int(thresholds.get("high_max_risk", 2)) and score >= float(thresholds.get("high_score", 0.68)) and risk_level in {"low", "medium"}:
        return "high"
    return "medium"


def _decision_action(score: float, risk_level: RiskLevel, confidence: Level, has_base: bool, cfg: dict[str, Any]) -> DecisionAction:
    th = cfg.get("thresholds") or {}
    buy = float(th.get("buy", 0.70))
    watch = float(th.get("watch", 0.55))
    wait = float(th.get("wait", 0.45))
    reduce = float(th.get("reduce", 0.42))
    sell = float(th.get("sell", 0.30))
    if has_base:
        if risk_level == "extreme" or score <= sell:
            return "sell"
        if risk_level == "high" or score <= reduce:
            return "reduce"
        if score >= watch:
            return "hold"
        return "watch"
    if risk_level == "extreme" or score < float(th.get("avoid", 0.38)):
        return "avoid"
    if risk_level == "high":
        return "wait" if score >= wait else "avoid"
    if score >= buy and confidence != "low":
        return "buy"
    if score >= watch:
        return "watch"
    if score >= wait:
        return "wait"
    return "avoid"


def _price_rules(horizon: str, *, tech, support, resistance, plan: TradePlan, cfg: dict[str, Any]) -> dict[str, Any]:
    rules = cfg.get("price_rules") or {}
    atr = float(tech.atr14 or tech.close * float(rules.get("atr_fallback_pct", 0.03)))
    close = float(tech.close)
    support_price = float(support.price) if support is not None else close * (1.0 - float(rules.get("fallback_support_pct", 0.05)))
    resistance_price = float(resistance.price) if resistance is not None else close * (1.0 + float(rules.get("fallback_resistance_pct", 0.10)))
    entry_low_mult = float(rules.get("entry_low_atr", {"5d": -0.20, "10d": -0.10, "20d": 0.00}.get(horizon, -0.10)))
    entry_high_mult = float(rules.get("entry_high_atr", {"5d": 0.35, "10d": 0.55, "20d": 0.75}.get(horizon, 0.35)))
    buy_low = max(0.01, support_price + entry_low_mult * atr)
    buy_high = max(buy_low * 1.002, min(close * float(rules.get("max_entry_vs_close", 1.025)), support_price + entry_high_mult * atr))
    stop = max(0.01, support_price - float(rules.get("stop_atr", {"5d": 0.80, "10d": 1.10, "20d": 1.35}.get(horizon, 1.0))) * atr)
    first_pct = float(rules.get("first_target_pct", {"5d": 6.0, "10d": 10.0, "20d": 14.0}.get(horizon, 8.0)))
    target_low_pct = float(rules.get("target_low_pct", {"5d": 5.0, "10d": 8.0, "20d": 12.0}.get(horizon, 8.0)))
    target_high_pct = float(rules.get("target_high_pct", {"5d": 10.0, "10d": 16.0, "20d": 24.0}.get(horizon, 16.0)))
    first_low = min(max(resistance_price, buy_high * (1.0 + first_pct / 100.0)), buy_high * (1.0 + target_high_pct / 100.0))
    first_high = max(first_low, first_low + 0.35 * atr)
    target_low = buy_high * (1.0 + target_low_pct / 100.0)
    target_high = buy_high * (1.0 + target_high_pct / 100.0)
    chase = min(resistance_price + float(rules.get("chase_resistance_atr", 0.20)) * atr, close + float(rules.get("chase_atr", {"5d": 0.90, "10d": 1.30, "20d": 1.80}.get(horizon, 1.0))) * atr)
    invalidation = [
        f"有效跌破 {round(stop, 4)} 且无法收回。",
        "策略矩阵转为负向且核心支持信号消失。",
    ]
    if horizon == "10d":
        invalidation.append("资金连续性或 SW L2 板块扩散明显转弱。")
    if horizon == "20d":
        invalidation.append("20日趋势支撑破坏，板块顺风消失。")
    return {
        "buy_zone": DecisionPriceZone(round(buy_low, 4), round(buy_high, 4), "支撑位/ATR 参数化买入区间。"),
        "chase_warning_price": round(chase, 4),
        "stop_loss": DecisionPriceLevel(round(stop, 4), "支撑位下方 ATR 参数化失效线。"),
        "first_take_profit": DecisionPriceZone(round(first_low, 4), round(first_high, 4), "第一止盈参考最近压力位与目标收益参数。"),
        "target_zone": DecisionPriceZone(round(target_low, 4), round(target_high, 4), f"{horizon} 目标区间，非收益承诺。"),
        "invalidation_condition": invalidation,
    }


def _probability_estimates(horizon: str, plan: TradePlan) -> dict[str, DecisionProbability]:
    prob = plan.probability
    return {
        "entry_fill_probability": DecisionProbability(prob.entry_fill_probability, bool(prob.calibrated), "入场成交估计", "legacy_prediction_surface"),
        "stop_first_probability": DecisionProbability(prob.prob_stop_first, bool(prob.calibrated), "止损先到估计", "legacy_prediction_surface"),
        "target_first_probability": DecisionProbability(_best_target_first(prob.opportunities, horizon), bool(prob.calibrated), "目标先到估计", "target_stop_replay_or_legacy_surface"),
    }


def _best_target_first(opportunities: list[dict[str, Any]] | None, horizon: str) -> float | None:
    if not opportunities:
        return None
    target_days = {"5d": 5, "10d": 10, "20d": 20}[horizon]
    rows = sorted(opportunities, key=lambda r: abs(int(r.get("horizon_days") or target_days) - target_days))
    row = rows[0]
    value = row.get("target_first_probability", row.get("probability"))
    return float(value) if value is not None else None


def _probability_warning(plan: TradePlan) -> str:
    if plan.probability.calibrated:
        return "概率已标记为校准版本，仍需结合风险等级和价格执行。"
    return "当前概率估计未经过 5/10/20 三周期正式校准，不能当作确定性上涨概率；主决策以 score、风险和价格执行为准。"


def _signal_rows(signal_map: dict[str, dict[str, Any]], keys: list[str], *, positive: bool) -> list[DecisionSignal]:
    rows = []
    for key in keys:
        signal = signal_map.get(key)
        if not signal or signal.get("status") == "missing":
            continue
        score = float(signal.get("score") or 0.0)
        if positive and score <= 0:
            continue
        if not positive and score >= 0:
            continue
        rows.append(_decision_signal(signal))
    rows.sort(key=lambda r: abs(r.score), reverse=True)
    return rows


def _contributors(signal_map: dict[str, dict[str, Any]], keys: list[str]) -> list[DecisionSignal]:
    rows = [_decision_signal(signal_map[key]) for key in keys if key in signal_map and signal_map[key].get("status") != "missing"]
    rows.sort(key=lambda r: abs(r.score), reverse=True)
    return rows


def _decision_signal(signal: dict[str, Any]) -> DecisionSignal:
    return DecisionSignal(
        key=str(signal.get("key")),
        label=str(signal.get("name") or signal.get("key")),
        score=round(float(signal.get("score") or 0.0), 4),
        direction=str(signal.get("direction") or "neutral"),
        status=str(signal.get("status") or ""),
        evidence=str(signal.get("evidence") or ""),
        source=str(signal.get("data_source") or ""),
        role=str(signal.get("cluster_label") or signal.get("cluster") or ""),
    )


def _data_quality(snapshot: StockEdgeSnapshot, horizon: str) -> dict[str, Any]:
    mandatory_ok = snapshot.daily_bars.ok and snapshot.daily_basic.ok
    optional_bad = []
    if snapshot.moneyflow.degraded:
        optional_bad.append("moneyflow")
    if horizon == "5d" and (snapshot.intraday_5min is None or snapshot.intraday_5min.degraded):
        optional_bad.append("intraday_5min")
    if horizon == "20d" and snapshot.research_lineup.degraded:
        optional_bad.append("research_lineup")
    status = "ok" if mandatory_ok and not optional_bad else ("partial" if mandatory_ok else "missing")
    return {"status": status, "required_sources_ok": mandatory_ok, "optional_missing": optional_bad, "record_status_blocking": not mandatory_ok}


def _missing_data_notes(snapshot: StockEdgeSnapshot, horizon: str) -> list[str]:
    notes = []
    if snapshot.moneyflow.degraded and snapshot.moneyflow.message:
        notes.append(snapshot.moneyflow.message)
    if horizon == "5d" and snapshot.intraday_5min is None:
        notes.append("5分钟数据未接入本次快照，短线执行分按降级口径输出。")
    if horizon == "5d" and snapshot.intraday_5min is not None and snapshot.intraday_5min.degraded:
        notes.append(snapshot.intraday_5min.message or "5分钟数据不足，短线执行分降级。")
    if horizon == "20d" and snapshot.research_lineup.degraded:
        notes.append(snapshot.research_lineup.message or "Research/Fundamental 辅助背景不足，不阻塞 20d 决策。")
    return notes


def _user_label(action: DecisionAction, horizon: str) -> str:
    labels = {
        "buy": {"5d": "短线可执行", "10d": "短波段值得关注", "20d": "一个月波段可关注"},
        "watch": {"5d": "短线观察", "10d": "短波段观察", "20d": "波段观察"},
        "wait": {"5d": "等待回踩", "10d": "等待确认", "20d": "等待趋势确认"},
        "avoid": {"5d": "不建议追高", "10d": "暂不参与", "20d": "暂不做波段"},
        "hold": {"5d": "已有仓位可观察", "10d": "已有仓位可持有", "20d": "已有仓位可继续观察"},
        "reduce": {"5d": "短线减仓防守", "10d": "短波段降低仓位", "20d": "波段降风险"},
        "sell": {"5d": "触发退出", "10d": "退出短波段", "20d": "退出波段"},
        "no_action": {"5d": "无动作", "10d": "无动作", "20d": "无动作"},
    }
    return labels[action][horizon]


def _summary(horizon: str, action: DecisionAction, score: float, risk: RiskLevel, confidence: Level) -> str:
    return f"{HORIZON_META[horizon][0]}：{_user_label(action, horizon)}；score={score:.2f}，风险={risk}，置信度={confidence}。"


def _suggested_action(action: DecisionAction, horizon: str, risk_level: RiskLevel) -> str:
    if action == "buy":
        return "只在买入区间内执行，越过追高警戒价不追。"
    if action == "watch":
        return "保留观察，等待价格进入买入区间或核心信号增强。"
    if action == "wait":
        return "等待回踩、VWAP/支撑确认或板块资金继续扩散。"
    if action == "avoid":
        return "当前不新开仓，先观察风险信号是否消退。"
    if action == "hold":
        return "已有仓位按止损和第一止盈执行，不加仓追高。"
    if action == "reduce":
        return "降低风险暴露，反弹到压力区优先减仓。"
    if action == "sell":
        return "严格执行退出或止损纪律。"
    return "暂不采取交易动作。"


def _holding_action(action: DecisionAction, risk_level: RiskLevel, stop_price: float | None) -> str:
    stop = f"{stop_price:.4f}" if stop_price else "失效价"
    if action in {"buy", "hold", "watch"} and risk_level in {"low", "medium"}:
        return f"已有仓位可继续观察；跌破 {stop} 需要复核或止损。"
    if action in {"wait", "reduce"} or risk_level == "high":
        return f"已有仓位建议降低风险，跌破 {stop} 不再摊薄。"
    if action in {"sell", "avoid"} or risk_level == "extreme":
        return f"已有仓位优先退出或严格按 {stop} 止损。"
    return "已有仓位暂不加仓。"


def _not_holding_action(action: DecisionAction, buy_zone: DecisionPriceZone, chase: float | None) -> str:
    zone = f"{buy_zone.low:.4f}-{buy_zone.high:.4f}" if buy_zone.low and buy_zone.high else "买入区间"
    chase_text = f"{chase:.4f}" if chase else "追高警戒价"
    if action == "buy":
        return f"未持仓只在 {zone} 内分批试错，超过 {chase_text} 不追。"
    if action in {"watch", "wait"}:
        return f"未持仓等待 {zone} 或突破回踩确认，超过 {chase_text} 不追。"
    return "未持仓不主动买入。"


def _conflict_note(supporting: list[DecisionSignal], risks: list[DecisionSignal], confidence: Level) -> str:
    if supporting and risks:
        return f"正向信号与风险信号同时存在，已降低置信度；正向代表 {supporting[0].label}，风险主要来自 {risks[0].label}。"
    if confidence == "low":
        return "数据不足或模型分歧较大，不能给高置信度结论。"
    return "核心模型方向相对一致，暂无显著冲突。"


def _attach_cross_horizon_conflicts(decisions: dict[str, dict[str, Any]]) -> None:
    d5 = decisions["decision_5d"]
    d20 = decisions["decision_20d"]
    if float(d5["score"]) >= 0.70 and float(d20["score"]) < 0.50:
        note = "5d 偏强但 20d 不强，只适合短线执行，不应自动转成波段持有。"
        d5["conflict_notes"] = f"{d5['conflict_notes']} {note}"
        d20["conflict_notes"] = f"{d20['conflict_notes']} {note}"
    if float(d20["score"]) >= 0.65 and float(d5["score"]) < 0.55:
        note = "20d 结构尚可但 5d 买点不足，中期可观察，短线等待回踩或执行确认。"
        d5["conflict_notes"] = f"{d5['conflict_notes']} {note}"
        d20["conflict_notes"] = f"{d20['conflict_notes']} {note}"


def _model_conflicts(decisions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for key in ("decision_5d", "decision_10d", "decision_20d"):
        d = decisions[key]
        rows.append(
            {
                "horizon": d["horizon"],
                "score": d["score"],
                "risk_level": d["risk_level"],
                "confidence_level": d["confidence_level"],
                "conflict_notes": d["conflict_notes"],
                "supporting": [s["key"] for s in d["key_supporting_signals"]],
                "opposing": [s["key"] for s in d["opposing_models"]],
            }
        )
    return {"rows": rows, "summary": "三周期冲突解释来自结构化策略矩阵，不使用 LLM 改写数值。"}


def _legacy_audit(plan: TradePlan) -> dict[str, Any]:
    return {
        "note": "旧 40d/20-40d 概率面仅保留为兼容审计，不进入三周期用户主决策。",
        "probability": plan.probability.to_dict() if hasattr(plan.probability, "to_dict") else asdict(plan.probability),
        "holding_window_days": list(plan.holding_window_days),
    }


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
