"""Compatibility helpers for the report-facing focus-stock contract.

Ticker selection moved to ``ifa.families._shared.focus_selection``.  There is
intentionally no default/user watchlist here: Market and Tech producers now
derive their attention lists from each report cutoff's market state.
"""
from __future__ import annotations

from ifa.families._shared.focus_selection import FocusStock


def tech_only(stocks: list[FocusStock], limit: int) -> list[FocusStock]:
    """Filter to Tech-relevant stocks (any AI layer != non_tech) up to `limit`."""
    return [s for s in stocks if s.layer != "non_tech"][:limit]


__all__ = ["FocusStock", "tech_only"]
