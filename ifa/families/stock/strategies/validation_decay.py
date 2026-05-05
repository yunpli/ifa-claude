"""Rolling strategy-validation meta signal for Stock Edge."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ValidationDecayProfile:
    available: bool
    reason: str
    sample_count: int
    avg_winrate_60d: float | None
    avg_combined_score_60d: float | None
    avg_decay_score: float | None
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_validation_decay_profile(metrics: list[dict[str, Any]] | None, *, params: dict[str, Any]) -> ValidationDecayProfile:
    """Convert rolling TA validation metrics into one bounded meta signal."""
    if not params.get("enabled", True):
        return _missing("validation decay disabled")
    rows = list(metrics or [])
    if not rows:
        return _missing("缺少 TA 滚动验证指标。")
    winrates = [_as_float(row.get("winrate_60d")) for row in rows]
    winrates = [v for v in winrates if v is not None]
    combined = [_as_float(row.get("combined_score_60d")) for row in rows]
    combined = [v for v in combined if v is not None]
    decays = [_as_float(row.get("decay_score")) for row in rows]
    decays = [v for v in decays if v is not None]
    if not winrates and not combined and not decays:
        return _missing("TA 滚动验证指标字段不可用。")
    avg_wr = sum(winrates) / len(winrates) if winrates else None
    avg_combined = sum(combined) / len(combined) if combined else None
    avg_decay = sum(decays) / len(decays) if decays else None
    score = 0.0
    if avg_wr is not None:
        score += 0.26 * _clip((avg_wr - float(params.get("winrate_center_pct", 25.0))) / max(float(params.get("winrate_scale_pct", 10.0)), 1e-6), -1.0, 1.0)
    if avg_combined is not None:
        score += 0.24 * _clip(avg_combined / max(float(params.get("combined_score_scale", 0.45)), 1e-6), -1.0, 1.0)
    if avg_decay is not None:
        score += 0.22 * _clip((avg_decay - float(params.get("decay_center_pp", -5.0))) / max(float(params.get("decay_scale_pp", 10.0)), 1e-6), -1.0, 1.0)
    return ValidationDecayProfile(
        available=True,
        reason="已将 TA 滚动胜率、综合分和衰减转为策略验证元信号。",
        sample_count=len(rows),
        avg_winrate_60d=round(avg_wr, 4) if avg_wr is not None else None,
        avg_combined_score_60d=round(avg_combined, 4) if avg_combined is not None else None,
        avg_decay_score=round(avg_decay, 4) if avg_decay is not None else None,
        score=round(_clip(score, -0.36, 0.36), 4),
    )


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


def _missing(reason: str) -> ValidationDecayProfile:
    return ValidationDecayProfile(
        available=False,
        reason=reason,
        sample_count=0,
        avg_winrate_60d=None,
        avg_combined_score_60d=None,
        avg_decay_score=None,
        score=0.0,
    )
