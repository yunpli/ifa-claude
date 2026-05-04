"""Inline SVG sparkline generator.

A sparkline is a tiny line chart (typically 100×24 px) embedded inline with
text. Useful for the trend grid: a single glance shows shape (rising / falling
/ choppy) without needing a separate chart panel.

Design choices:
  · No external chart libs. Just string-formatted SVG paths — fits anywhere
    HTML goes (browser, email, PDF).
  · Auto-scaled to data range (0% / 100% bands hidden — keeps the line visible).
  · None values cause the path to break (gap), so missing periods are visible.
  · Color-coded by overall direction: green if last > first, red if last <
    first, gray if flat (within ±2%).
  · Last point highlighted with a small dot to anchor the eye.

API:
  · render_sparkline(values, *, width=100, height=24) → str (SVG markup)
"""
from __future__ import annotations

from typing import Sequence

# Visual constants — tuned for inline use at 14px line-height
_DEFAULT_WIDTH = 100
_DEFAULT_HEIGHT = 24
_PADDING_Y = 2     # vertical breathing room

_COLOR_UP = "#2e7d32"
_COLOR_DOWN = "#c62828"
_COLOR_FLAT = "#888"
_COLOR_BG_GUIDE = "#e0e0e0"  # mid-line guide


def render_sparkline(
    values: Sequence[float | None],
    *,
    width: int = _DEFAULT_WIDTH,
    height: int = _DEFAULT_HEIGHT,
) -> str:
    """Render an SVG sparkline. Returns markup ready to inline in HTML.

    Returns an empty string for inputs with <2 valid points (no shape to draw).
    """
    finite = [v for v in values if v is not None]
    if len(finite) < 2:
        return ""

    n = len(values)
    vmin = min(finite)
    vmax = max(finite)
    span = (vmax - vmin) or 1.0   # avoid div-zero on flat series

    inner_h = height - 2 * _PADDING_Y
    x_step = width / (n - 1) if n > 1 else 0

    def _coords(i: int, v: float) -> tuple[float, float]:
        x = i * x_step
        # SVG y-axis points down, so invert.
        y = _PADDING_Y + inner_h * (1 - (v - vmin) / span)
        return x, y

    # Build path with M/L commands; break into a new "M" on None gaps so the
    # line does not falsely connect across missing periods.
    path_parts: list[str] = []
    pen_down = False
    for i, v in enumerate(values):
        if v is None:
            pen_down = False
            continue
        x, y = _coords(i, v)
        cmd = "L" if pen_down else "M"
        path_parts.append(f"{cmd}{x:.1f},{y:.1f}")
        pen_down = True
    if not path_parts:
        return ""
    path_d = " ".join(path_parts)

    # Direction color (compare first valid → last valid)
    first_valid = next(v for v in values if v is not None)
    last_valid = next(v for v in reversed(values) if v is not None)
    if first_valid == 0:
        change_pct = 0.0
    else:
        change_pct = (last_valid - first_valid) / abs(first_valid) * 100
    if change_pct > 2:
        stroke = _COLOR_UP
    elif change_pct < -2:
        stroke = _COLOR_DOWN
    else:
        stroke = _COLOR_FLAT

    # Last-point dot
    last_idx = max(i for i, v in enumerate(values) if v is not None)
    lx, ly = _coords(last_idx, last_valid)

    # Mid guide (helps with reading direction at a glance)
    mid_y = _PADDING_Y + inner_h / 2

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'style="display:inline-block;vertical-align:middle">'
        f'<line x1="0" y1="{mid_y:.1f}" x2="{width}" y2="{mid_y:.1f}" '
        f'stroke="{_COLOR_BG_GUIDE}" stroke-width="1" stroke-dasharray="2,2"/>'
        f'<path d="{path_d}" fill="none" stroke="{stroke}" '
        f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2" fill="{stroke}"/>'
        f'</svg>'
    )
