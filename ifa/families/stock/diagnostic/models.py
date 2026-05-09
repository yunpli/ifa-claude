"""Typed evidence schema for Stock Edge diagnostic reports.

This layer is intentionally independent from the existing Stock Edge HTML report.
It is a read-only composition surface: every perspective either cites concrete
local evidence or marks itself unavailable.  That keeps the single-stock product
auditable while sector-cycle-leader, TA, Ningbo, Research and risk modules evolve
at different speeds.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PerspectiveStatus = Literal["available", "partial", "unavailable", "error"]
PerspectiveView = Literal["positive", "neutral", "negative", "risk", "unknown"]
FreshnessStatus = Literal["fresh", "stale", "unavailable"]
AdvisorConclusion = Literal[
    "short-term tradable",
    "watch only",
    "avoid",
    "overheated",
    "wait for pullback",
]


@dataclass(frozen=True)
class DiagnosticRequest:
    ts_code: str
    requested_at: dt.datetime | None = None
    run_mode: str = "manual"
    include_full_stock_edge: bool = False


@dataclass(frozen=True)
class EvidencePoint:
    label: str
    value: Any = None
    source: str = ""
    as_of: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class PerspectiveEvidence:
    key: str
    title: str
    status: PerspectiveStatus
    view: PerspectiveView
    summary: str
    points: list[EvidencePoint] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    freshness: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def freshness_status(self) -> FreshnessStatus:
        status = self.freshness.get("status")
        if status in {"fresh", "stale", "unavailable"}:
            return status  # type: ignore[return-value]
        if self.status in {"unavailable", "error"}:
            return "unavailable"
        return "fresh" if self.points else "unavailable"


@dataclass(frozen=True)
class DiagnosticSynthesis:
    conclusion: AdvisorConclusion
    confidence: str
    horizon_suitability: dict[str, str]
    trigger: str
    invalidation: str
    time_window: str
    position_risk: str
    conflicts: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiagnosticReport:
    ts_code: str
    name: str | None
    as_of_trade_date: dt.date
    generated_at_bjt: str
    data_cutoff_bjt: str
    perspectives: list[PerspectiveEvidence]
    synthesis: DiagnosticSynthesis
    audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["as_of_trade_date"] = self.as_of_trade_date.isoformat()
        for idx, perspective in enumerate(data.get("perspectives", [])):
            perspective["stance"] = perspective.get("view")
            perspective["evidence"] = perspective.get("points", [])
            perspective["missing_evidence"] = perspective.get("missing", [])
            perspective["freshness_status"] = self.perspectives[idx].freshness_status
        return data
