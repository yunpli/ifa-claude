"""Parameter overlay helpers for Stock Edge."""
from __future__ import annotations

import copy
from typing import Any, Mapping


def apply_param_overlay(base_params: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Return params with a dotted-key continuous overlay applied.

    Overlay keys are intentionally stable and human-readable, e.g.
    `aggregate.buy_threshold`, `cluster_weights.trend_breakout`, or
    `risk.right_tail_target_pct`. Strategy-matrix keys are rooted under
    `strategy_matrix`; top-level families such as `risk` and `t0` keep their
    explicit prefix.
    """
    params = copy.deepcopy(dict(base_params))
    for dotted_key, value in overlay.items():
        path = _resolve_path(str(dotted_key))
        _set_path(params, path, value)
    return params


def attach_tuning_runtime(
    params: Mapping[str, Any],
    *,
    status: str,
    reason: str,
    artifact_path: str | None = None,
    objective_score: float | None = None,
    candidate_count: int | None = None,
) -> dict[str, Any]:
    """Attach runtime tuning metadata to params for hashing/audit."""
    out = copy.deepcopy(dict(params))
    out["_runtime_tuning"] = {
        "status": status,
        "reason": reason,
        "artifact_path": artifact_path,
        "objective_score": objective_score,
        "candidate_count": candidate_count,
    }
    return out


def _resolve_path(dotted_key: str) -> list[str]:
    parts = dotted_key.split(".")
    if parts[0] in {"risk", "t0", "model", "runtime", "data", "intraday", "cache", "report", "tuning"}:
        return parts
    return ["strategy_matrix", *parts]


def _set_path(params: dict[str, Any], path: list[str], value: Any) -> None:
    current: dict[str, Any] = params
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = value
