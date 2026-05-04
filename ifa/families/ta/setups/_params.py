"""Per-setup parameter helper — bridges YAML thresholds to setup callsites.

Usage in a setup file:
    from ifa.families.ta.setups._params import setup_param

    def T2_PULLBACK_RESUME(ctx):
        if recent_low > setup_param("T2_PULLBACK_RESUME", "ma20_touch_max_x", 1.02) * ctx.ma_qfq_20:
            return None

The third arg is the hardcoded fallback default — if the YAML key is missing
or malformed, the setup keeps working with the original literal. Tune script
overrides the YAML to grid-search.

Cached process-wide via load_params; call ifa.families.ta.params.reload_params()
to bust after editing yaml.
"""
from __future__ import annotations

from typing import Any

from ifa.families.ta.params import load_params


def setup_param(setup_name: str, key: str, default: Any) -> Any:
    """Return params['setups'][setup_name][key] or default."""
    p = load_params().get("setups", {}) or {}
    block = p.get(setup_name, {}) or {}
    val = block.get(key, default)
    return val if val is not None else default
