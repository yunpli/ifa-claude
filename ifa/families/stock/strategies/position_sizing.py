"""Dynamic position sizing for Stock Edge trade plans."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ifa.families.stock.plan import PositionSize, PriceLevel, PriceZone, ProbabilityBlock


@dataclass(frozen=True)
class SizingInputs:
    action: Literal["buy", "watch", "avoid", "exit", "update"]
    confidence: Literal["high", "medium", "low"]
    entry_zone: PriceZone | None
    stop: PriceLevel | None
    probability: ProbabilityBlock
    vetoes: list[str]


def build_position_size(inputs: SizingInputs, *, params: dict[str, Any]) -> PositionSize:
    """Build a continuous position size from probability and risk geometry."""
    cfg = params.get("position_sizing", {})
    if not cfg.get("enabled", True):
        return _fallback(inputs)
    if inputs.vetoes or inputs.action not in {"buy", "watch"}:
        return PositionSize("禁止开仓", 0.0, "触发风控否决或不满足开仓动作。")
    if inputs.action == "watch":
        return PositionSize("观察", float(cfg.get("max_watch_fraction", 0.0)), "观察动作不主动开仓。")
    if inputs.entry_zone is None or inputs.stop is None:
        return PositionSize("禁止开仓", 0.0, "缺少入场区间或止损线。")

    entry = max(float(inputs.entry_zone.high), 0.01)
    stop_distance = max(0.01, min(0.35, 1.0 - float(inputs.stop.price) / entry))
    max_loss_budget = float(cfg.get("max_loss_budget_fraction", 0.025))
    risk_cap = max_loss_budget / stop_distance
    best = inputs.probability.best_opportunity or {}
    expected_value = float(best.get("expected_value") if best else inputs.probability.expected_return_40d)
    stop_probability = float(inputs.probability.prob_stop_first or 0.35)
    drawdown = float(inputs.probability.expected_drawdown_40d)
    ev_scale = max(float(cfg.get("expected_value_scale", 0.18)), 1e-6)
    quality = max(0.0, min(1.0, expected_value / ev_scale))
    quality *= max(0.0, 1.0 - float(cfg.get("stop_probability_penalty", 0.60)) * stop_probability)
    quality *= max(0.0, 1.0 - float(cfg.get("drawdown_penalty", 0.40)) * drawdown)
    quality *= _confidence_multiplier(inputs.confidence, cfg)
    min_fraction = float(cfg.get("min_buy_fraction", 0.10))
    max_fraction = float(cfg.get("max_buy_fraction", 0.35))
    raw_fraction = min_fraction + (max_fraction - min_fraction) * quality
    fraction = max(0.0, min(max_fraction, min(risk_cap, raw_fraction)))
    if fraction < min_fraction * 0.55:
        return PositionSize("小试探仓", round(fraction, 4), "风险预算压制仓位，仅允许极小试探。")
    label = "试探仓" if fraction < 0.20 else ("标准仓" if fraction < 0.30 else "积极仓")
    return PositionSize(
        label,
        round(fraction, 4),
        f"基于期望值 {expected_value:.1%}、先止损概率 {stop_probability:.1%}、止损距离 {stop_distance:.1%} 的连续仓位。",
    )


def _confidence_multiplier(confidence: str, cfg: dict[str, Any]) -> float:
    return {
        "low": float(cfg.get("low_confidence_multiplier", 0.65)),
        "medium": float(cfg.get("medium_confidence_multiplier", 0.85)),
        "high": float(cfg.get("high_confidence_multiplier", 1.0)),
    }.get(confidence, 0.75)


def _fallback(inputs: SizingInputs) -> PositionSize:
    if inputs.vetoes:
        return PositionSize("禁止开仓", 0.0, "触发硬性风控否决。")
    if inputs.action == "buy":
        return PositionSize("试探仓", 0.25, "默认试探仓。")
    if inputs.action == "watch":
        return PositionSize("观察", 0.0, "观察动作不主动开仓。")
    return PositionSize("禁止开仓", 0.0, "不满足开仓动作。")
