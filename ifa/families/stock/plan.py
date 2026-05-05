"""Structured Stock Edge trade plan objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Action = Literal["buy", "watch", "avoid", "exit", "update"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class PriceZone:
    low: float
    high: float
    reason: str


@dataclass(frozen=True)
class PriceLevel:
    price: float
    reason: str


@dataclass(frozen=True)
class PriceTarget:
    label: str
    price: float
    reason: str


@dataclass(frozen=True)
class ProbabilityBlock:
    prob_hit_50_40d: float
    expected_return_40d: float
    expected_drawdown_40d: float
    model_version: str
    calibrated: bool = False
    prob_hit_20_40d: float | None = None
    prob_hit_30_40d: float | None = None
    prob_stop_first: float | None = None
    entry_fill_probability: float | None = None
    return_p10_40d: float | None = None
    return_p50_40d: float | None = None
    return_p90_40d: float | None = None
    opportunities: list[dict[str, Any]] | None = None
    best_opportunity: dict[str, Any] | None = None


@dataclass(frozen=True)
class PositionSize:
    label: str
    budget_fraction: float
    reason: str


@dataclass(frozen=True)
class T0Plan:
    eligible: bool
    max_size_pct_of_base: float = 0.0
    sell_zone: PriceZone | None = None
    buyback_zone: PriceZone | None = None
    do_not_t0_if: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceItem:
    key: str
    value: Any
    source: str
    note: str | None = None


@dataclass(frozen=True)
class TradePlan:
    action: Action
    confidence: Confidence
    setup_type: str
    entry_zone: PriceZone | None
    add_zone: PriceZone | None
    stop: PriceLevel | None
    targets: list[PriceTarget]
    holding_window_days: tuple[int, int]
    probability: ProbabilityBlock
    position_size: PositionSize
    t0_plan: T0Plan | None
    vetoes: list[str]
    evidence: list[EvidenceItem]

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)
