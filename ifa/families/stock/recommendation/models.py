"""Typed model for Stock Edge recommendation briefs.

The brief is a cross-sectional, last-closed-trading-day product.  It does not
run tuning, mutate YAML, or infer missing data.  Each source is either cited in
candidate evidence or recorded as unavailable in `source_status`.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

BriefGroup = Literal["strong", "watchlist", "avoid"]

LOGIC_VERSION = "stock_recommendation_brief_mvp1"


@dataclass(frozen=True)
class RecommendationBriefRequest:
    as_of: dt.date | None = None
    requested_at: dt.datetime | None = None
    run_mode: str = "manual"
    limit_per_group: int = 12


@dataclass(frozen=True)
class RecommendationEvidence:
    label: str
    value: Any
    source: str
    note: str | None = None


@dataclass(frozen=True)
class RecommendationCandidate:
    ts_code: str
    name: str | None
    group: BriefGroup
    l1_name: str | None
    l2_name: str | None
    rank_in_sector: int | None
    sector_rank_count: int | None
    leader_score: float | None
    sector_score: float | None
    stock_score: float | None
    quality_flag: str | None
    horizon_suitability: dict[str, str]
    trigger: str
    invalidation: str
    evidence: list[RecommendationEvidence] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    source_flags: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecommendationBriefReport:
    title: str
    as_of_trade_date: dt.date
    generated_at_bjt: str
    data_cutoff_bjt: str
    run_mode: str
    as_of_rule: str
    logic_version: str
    groups: dict[BriefGroup, list[RecommendationCandidate]]
    source_status: dict[str, dict[str, Any]]
    audit: dict[str, Any] = field(default_factory=dict)
    disclaimer: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["as_of_trade_date"] = self.as_of_trade_date.isoformat()
        return data
