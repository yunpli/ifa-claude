"""Inline SVG sparkline / bar chart helpers — no JS, no external assets.

Produces compact, print-friendly charts that survive email and offline viewing.
"""
from __future__ import annotations

from collections.abc import Sequence


def sparkline_svg(
    values: Sequence[float | None],
    *,
    width: int = 120,
    height: int = 28,
    color: str = "#0f1626",
    fill: str = "rgba(15,22,38,0.06)",
    show_endpoint: bool = True,
    show_baseline: bool = False,
) -> str:
    """Render a smooth sparkline polyline + soft fill area."""
    pts = [v for v in values if v is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    n = len(values)
    pad = 2

    def _xy(i: int, v: float) -> tuple[float, float]:
        x = pad + (width - 2 * pad) * (i / (n - 1)) if n > 1 else width / 2
        y = pad + (height - 2 * pad) * (1 - (v - lo) / span)
        return x, y

    coords: list[tuple[float, float]] = []
    for i, v in enumerate(values):
        if v is None:
            continue
        coords.append(_xy(i, v))

    line_d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in coords)
    fill_d = (
        f"M {coords[0][0]:.1f} {height - pad:.1f} "
        + " L ".join(f"{x:.1f} {y:.1f}" for x, y in coords)
        + f" L {coords[-1][0]:.1f} {height - pad:.1f} Z"
    )
    last_x, last_y = coords[-1]
    endpoint = (
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.2" fill="{color}"/>'
        if show_endpoint else ""
    )
    baseline = (
        f'<line x1="{pad}" x2="{width - pad}" y1="{height/2:.1f}" y2="{height/2:.1f}" '
        f'stroke="rgba(15,22,38,0.12)" stroke-width="0.5" stroke-dasharray="2 2"/>'
        if show_baseline else ""
    )

    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" class="ifa-spark">'
        f'{baseline}'
        f'<path d="{fill_d}" fill="{fill}" stroke="none"/>'
        f'<path d="{line_d}" fill="none" stroke="{color}" stroke-width="1.4" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'{endpoint}'
        f'</svg>'
    )


def bar_svg(
    values: Sequence[float | None],
    *,
    width: int = 120,
    height: int = 28,
    pos_color: str = "#1a7f47",
    neg_color: str = "#b8242b",
    zero_line: bool = True,
) -> str:
    """Render a small +/- bar chart, useful for YoY/MoM deltas."""
    nums = [0.0 if v is None else float(v) for v in values]
    if not nums:
        return ""
    abs_max = max(abs(min(nums)), abs(max(nums))) or 1.0
    n = len(nums)
    pad = 2
    bar_w = max(1.0, (width - 2 * pad) / n - 1)
    mid = height / 2

    bars = []
    for i, v in enumerate(nums):
        x = pad + i * (bar_w + 1)
        h = (height / 2 - pad) * (abs(v) / abs_max)
        if v >= 0:
            y, c = mid - h, pos_color
        else:
            y, c = mid, neg_color
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(h,0.5):.1f}" fill="{c}" rx="0.5"/>')
    line = (
        f'<line x1="{pad}" x2="{width - pad}" y1="{mid:.1f}" y2="{mid:.1f}" '
        f'stroke="rgba(15,22,38,0.25)" stroke-width="0.5"/>'
        if zero_line else ""
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" class="ifa-bars">{line}{"".join(bars)}</svg>'
    )
