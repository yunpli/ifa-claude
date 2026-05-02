"""Sparkline SVG generation for the recap section.

Generates inline SVG for cumulative return time series.

Color convention (中国习惯):
    - Positive cum_return → 红 (#991b1b, var(--up))
    - Negative cum_return → 绿 (#166534, var(--down))
    - Zero baseline       → 灰虚线

Output is inline <svg> string suitable for direct HTML/PDF embedding.

Phase 1.11 — to be implemented (Sonnet).
"""
from __future__ import annotations

UP_COLOR = "#991b1b"      # 涨 = 红
DOWN_COLOR = "#166534"    # 跌 = 绿
ZERO_COLOR = "#94a3b8"


def render_sparkline(cum_returns: list[float | None], width: int = 120, height: int = 24) -> str:
    """Render cumulative-return sparkline as inline SVG.

    Args:
        cum_returns: time-ordered list, None for未追踪天 (truncated/stop-loss/take-profit)
        width, height: in px

    Returns:
        '<svg>...</svg>' string
    """
    raise NotImplementedError("Phase 1.11")
