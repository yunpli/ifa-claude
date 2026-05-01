"""SmartMoney parameter management.

Usage:
    from ifa.families.smartmoney.params import load_default_params, get_active_params

    # Use frozen DB params (production) or fall back to default.yaml
    params = get_active_params(engine)
"""
from __future__ import annotations

from .store import get_active_params, load_default_params

__all__ = ["get_active_params", "load_default_params"]
