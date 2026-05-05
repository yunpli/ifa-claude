"""SW L2 sector diffusion signal for Stock Edge."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SectorDiffusionProfile:
    available: bool
    reason: str
    l2_name: str | None
    sample_count: int
    positive_flow_share: float | None
    recent_vs_prior_flow_pct: float | None
    persistence_score: float | None
    crowding_score: float | None
    leader_overlap: int
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_sector_diffusion_profile(sector_data: dict[str, Any] | None, *, params: dict[str, Any]) -> SectorDiffusionProfile:
    """Estimate whether the SW L2 sector is broadening or exhausting."""
    if not params.get("enabled", True):
        return _missing("sector diffusion disabled")
    data = sector_data or {}
    l2_name = data.get("l2_name")
    rows = list(data.get("sector_flow_7d") or [])
    factor = data.get("sector_factor") or {}
    leaders = data.get("sector_leaders") or {}
    if not l2_name and not rows and not factor:
        return _missing("缺少 SW L2 板块资金/状态数据。")
    flows = [_as_float(row.get("net_amount")) for row in rows]
    flows = [v for v in flows if v is not None]
    positive_share = sum(1 for v in flows if v > 0) / len(flows) if flows else None
    recent_vs_prior = _recent_vs_prior(flows)
    persistence = _as_float(factor.get("persistence_score"))
    crowding = _as_float(factor.get("crowding_score"))
    score = 0.0
    if positive_share is not None:
        score += 0.24 * (positive_share * 2.0 - 1.0)
    if recent_vs_prior is not None:
        scale = max(float(params.get("flow_acceleration_scale_pct", 80.0)), 1e-6)
        score += 0.18 * _clip(recent_vs_prior / scale, -1.0, 1.0)
    if persistence is not None:
        score += 0.18 * _clip((persistence - 0.5) / 0.35, -1.0, 1.0)
    if crowding is not None:
        score -= 0.22 * _clip((crowding - float(params.get("crowding_center", 0.72))) / 0.25, 0.0, 1.0)
    overlap = _leader_overlap(leaders)
    if overlap:
        score += min(overlap, 3) * 0.035
    return SectorDiffusionProfile(
        available=True,
        reason="已完成 SW L2 资金扩散/拥挤度连续评分。",
        l2_name=str(l2_name) if l2_name else None,
        sample_count=len(flows),
        positive_flow_share=round(positive_share, 4) if positive_share is not None else None,
        recent_vs_prior_flow_pct=round(recent_vs_prior, 4) if recent_vs_prior is not None else None,
        persistence_score=round(persistence, 4) if persistence is not None else None,
        crowding_score=round(crowding, 4) if crowding is not None else None,
        leader_overlap=overlap,
        score=round(_clip(score, -0.38, 0.42), 4),
    )


def _recent_vs_prior(flows: list[float]) -> float | None:
    if len(flows) < 5:
        return None
    ordered = list(reversed(flows))
    prior = ordered[:-3]
    recent = ordered[-3:]
    prior_avg = sum(prior) / max(len(prior), 1)
    recent_avg = sum(recent) / len(recent)
    denom = max(abs(prior_avg), 1.0)
    return (recent_avg - prior_avg) / denom * 100.0


def _leader_overlap(leaders: dict[str, Any]) -> int:
    memberships: dict[str, set[str]] = {}
    for category, rows in leaders.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            code = row.get("ts_code")
            if code:
                memberships.setdefault(str(code), set()).add(str(category))
    return sum(1 for cats in memberships.values() if len(cats) >= 2)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _clip(value: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0
    return max(low, min(high, value))


def _missing(reason: str) -> SectorDiffusionProfile:
    return SectorDiffusionProfile(
        available=False,
        reason=reason,
        l2_name=None,
        sample_count=0,
        positive_flow_share=None,
        recent_vs_prior_flow_pct=None,
        persistence_score=None,
        crowding_score=None,
        leader_overlap=0,
        score=0.0,
    )
