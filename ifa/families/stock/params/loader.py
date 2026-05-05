"""Stock Edge parameter loader.

The first Stock Edge implementation intentionally starts with a small,
deterministic parameter file. Long-window tuning can rewrite or freeze a later
version, but the functional path should remain stable and hashable.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PARAMS_PATH = Path(__file__).parent / "stock_edge_v2.2.yaml"


def _load_raw() -> dict[str, Any]:
    data = yaml.safe_load(_PARAMS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Stock Edge params must be a mapping: {_PARAMS_PATH}")
    return data


@lru_cache(maxsize=1)
def load_params() -> dict[str, Any]:
    """Return parsed Stock Edge params. Treat the returned object as read-only."""
    return _load_raw()


def reload_params() -> dict[str, Any]:
    """Clear the process cache and return freshly loaded params."""
    load_params.cache_clear()
    return load_params()


def params_hash(params: dict[str, Any] | None = None) -> str:
    """Return a stable short hash for params used in caches and audit logs."""
    payload = yaml.safe_dump(
        params or load_params(),
        allow_unicode=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
