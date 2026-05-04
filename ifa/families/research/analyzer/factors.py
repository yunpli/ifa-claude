"""Factor framework — base types shared by all 5 factor families.

Design principles:
1. **Refuse "calculate everything"** — every factor must answer a specific
   investment question (see the doc family rationales).
2. **Rules-only** — no LLM in factor computation; LLM consumes results.
3. **Three-state classification** — green / yellow / red. Two thresholds:
   `warning` triggers yellow, `critical` triggers red. Symmetry is decided
   per factor (some red-on-low, some red-on-high).
4. **Industry-relative is a separate concern** — peer.py adds the percentile
   rank later. Factor module returns absolute values; status bands are absolute.
5. **None propagation** — missing inputs produce None value with status='unknown'.
   Never silently treat None as 0 (data-accuracy-guidelines Rule 5).

Each factor family module exports `compute_<family>(snapshot, params) -> list[FactorResult]`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class FactorStatus(str, Enum):
    GREEN = "green"      # healthy
    YELLOW = "yellow"    # warning
    RED = "red"          # critical
    UNKNOWN = "unknown"  # missing inputs


@dataclass(frozen=True)
class FactorSpec:
    """Static metadata about a factor — registered once, consulted at compute time."""
    name: str                      # canonical name, e.g. "GPM"
    display_name_zh: str           # 中文展示名
    family: str                    # 'profitability' | 'growth' | 'cash_quality' | 'balance' | 'governance'
    formula: str                   # human-readable formula description
    unit: str                      # '%', 'x', '天', '元', '次', etc.
    source_apis: tuple[str, ...]   # which Tushare endpoints this factor depends on
    industry_sensitive: bool       # True if peer percentile is meaningful
    direction: str                 # 'higher_better' | 'lower_better' | 'in_band'
    interpretation_template: str   # f-string-style template for natural language
                                   # (rendered by section layer, not here)


@dataclass
class FactorResult:
    """Concrete computed value for a factor at a point in time."""
    spec: FactorSpec
    value: Decimal | float | None                # absolute value (in spec.unit)
    status: FactorStatus
    period: str | None = None                    # '20241231' / 'TTM' / etc.
    history: list[float | None] = field(default_factory=list)   # for sparkline
    history_periods: list[str] = field(default_factory=list)
    peer_rank: tuple[int, int] | None = None     # (rank, total) inside SW L2
    peer_percentile: float | None = None         # 0-100, 30 means worse than 70%
    notes: list[str] = field(default_factory=list)  # red-flag hints, data-quality flags
    raw_inputs: dict[str, Any] = field(default_factory=dict)    # keep for audit / explanation

    def is_red(self) -> bool:
        return self.status == FactorStatus.RED

    def is_concern(self) -> bool:
        return self.status in (FactorStatus.RED, FactorStatus.YELLOW)


# ─── Threshold helpers ────────────────────────────────────────────────────────

def classify_higher_better(
    value: Decimal | float | None,
    *,
    healthy_min: float,
    warning_below: float,
    critical_below: float,
) -> FactorStatus:
    """Higher-is-better metric (e.g. ROE, GPM): red if value < critical, yellow if < warning."""
    if value is None:
        return FactorStatus.UNKNOWN
    v = float(value)
    if v < critical_below:
        return FactorStatus.RED
    if v < warning_below:
        return FactorStatus.YELLOW
    return FactorStatus.GREEN


def classify_lower_better(
    value: Decimal | float | None,
    *,
    warning_above: float,
    critical_above: float,
) -> FactorStatus:
    """Lower-is-better metric (e.g. debt ratio, AR/Revenue ratio)."""
    if value is None:
        return FactorStatus.UNKNOWN
    v = float(value)
    if v > critical_above:
        return FactorStatus.RED
    if v > warning_above:
        return FactorStatus.YELLOW
    return FactorStatus.GREEN


def classify_in_band(
    value: Decimal | float | None,
    *,
    healthy_low: float,
    healthy_high: float,
    warning_band: float = 0.2,
) -> FactorStatus:
    """In-band metric (e.g. CFO/NI ideally 0.8-1.2). Red if outside warning_band%."""
    if value is None:
        return FactorStatus.UNKNOWN
    v = float(value)
    if healthy_low <= v <= healthy_high:
        return FactorStatus.GREEN
    span = healthy_high - healthy_low
    yellow_low = healthy_low - span * warning_band
    yellow_high = healthy_high + span * warning_band
    if yellow_low <= v <= yellow_high:
        return FactorStatus.YELLOW
    return FactorStatus.RED


# ─── Params loader ────────────────────────────────────────────────────────────

import importlib.resources
from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=1)
def load_params() -> dict:
    """Load params/research_v2.2.yaml as a nested dict."""
    here = Path(__file__).resolve().parent.parent / "params" / "research_v2.2.yaml"
    if not here.exists():
        raise FileNotFoundError(f"Research params not found at {here}")
    with here.open() as f:
        return yaml.safe_load(f)


def get_threshold(params: dict, family: str, factor: str, key: str, default: float | None = None) -> float:
    """Read params['<family>']['<factor>']['<key>'] with a default fallback."""
    fam = params.get(family, {})
    fac = fam.get(factor, {})
    if key in fac:
        return float(fac[key])
    if default is None:
        raise KeyError(f"Missing threshold {family}.{factor}.{key} and no default given")
    return float(default)
