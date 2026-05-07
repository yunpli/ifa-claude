"""SME parameter loading.

Continuous thresholds and weights are first-class tuning parameters. Discrete
settings are kept for structural choices only, such as bucket construction
mode. This avoids pretending that a few arbitrary grid buckets are market
truth while still allowing audit-friendly named profiles.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


DEFAULT_MARKET_STRUCTURE_YAML = Path(__file__).with_name("market_structure_v1.yaml")


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_market_structure_params(
    *,
    profile: str | None = None,
    path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    param_path = Path(path) if path else DEFAULT_MARKET_STRUCTURE_YAML
    raw = yaml.safe_load(param_path.read_text(encoding="utf-8"))
    selected = profile or raw.get("active_profile") or "baseline"
    profiles = raw.get("profiles") or {}
    if selected not in profiles:
        raise KeyError(f"market-structure profile not found: {selected}")
    params = _deep_merge({}, profiles[selected])
    if overrides:
        params = _deep_merge(params, overrides)
    params["_meta"] = {
        "profile": selected,
        "params_version": raw.get("version"),
        "path": str(param_path),
        "search_space": raw.get("search_space") or {},
        "hash": stable_params_hash(params),
    }
    return params


def stable_params_hash(params: dict[str, Any]) -> str:
    payload = copy.deepcopy(params)
    payload.pop("_meta", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
