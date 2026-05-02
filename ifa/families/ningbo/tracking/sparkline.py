"""Sparkline SVG generation for the recap section.

Renders cumulative return time series as inline SVG.

Color convention (中国习惯):
    - Positive cum_return → 红 (#991b1b)
    - Negative cum_return → 绿 (#166534)
    - Zero baseline       → 灰虚线

Series rendering:
    - Each value as a small filled bar from baseline (0%) to cum_return value
    - Bar color depends on the SIGN of that bar's value
    - Final value gets a small marker dot
    - Optional terminal-state marker:
        ⚠️ stop_loss:    red triangle ▼ at terminal day
        ✅ take_profit:  red triangle ▲ at terminal day
        ⏰ expired:      gray dot at day 15

Inline SVG so it embeds cleanly in HTML and PDF.
"""
from __future__ import annotations

UP_COLOR = "#991b1b"      # 涨 = 红 (中国惯例)
DOWN_COLOR = "#166534"    # 跌 = 绿 (中国惯例)
ZERO_COLOR = "#94a3b8"
TERMINAL_COLOR_STOP = "#0d172a"   # dark for stop marker (visible on red bg)
TERMINAL_COLOR_TP = "#7c2d12"     # darker red for take_profit


def _bar(x: float, y_top: float, y_bottom: float, w: float, color: str) -> str:
    """Generate one filled rect SVG element."""
    height = abs(y_bottom - y_top)
    y = min(y_top, y_bottom)
    return (
        f'<rect x="{x:.2f}" y="{y:.2f}" '
        f'width="{w:.2f}" height="{height:.2f}" '
        f'fill="{color}" />'
    )


def render_sparkline(
    cum_returns: list[float | None],
    *,
    width: int = 130,
    height: int = 24,
    expected_days: int = 15,
    terminal_status: str | None = None,
    terminal_track_day: int | None = None,
) -> str:
    """Render cumulative-return sparkline as inline SVG.

    Args:
        cum_returns: time-ordered list of cum_return values (T+1, T+2, ...).
                     None entries become gaps. Length should equal track_day_count.
        width, height: in px (default 130 × 24)
        expected_days: total slots to reserve on x-axis (default 15)
        terminal_status: 'stop_loss' | 'take_profit' | 'expired' | None
        terminal_track_day: T+N where terminal happened (1..15)

    Returns:
        '<svg>...</svg>' string. Empty <svg> if no data.
    """
    if not cum_returns:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"></svg>'

    # Y-scaling: find absolute max so we can center 0 at vertical midpoint
    valid = [v for v in cum_returns if v is not None]
    if not valid:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"></svg>'

    abs_max = max(abs(v) for v in valid)
    # Use minimum of 0.05 (5%) so very small returns don't look exaggerated
    abs_max = max(abs_max, 0.05)

    # Layout: thin bars side by side
    pad_x = 2
    pad_y = 2
    plot_w = width - 2 * pad_x
    plot_h = height - 2 * pad_y
    bar_slot = plot_w / expected_days
    bar_w = max(2, bar_slot * 0.8)
    bar_gap = bar_slot - bar_w
    y_zero = pad_y + plot_h / 2  # 0% baseline at vertical midpoint

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
    ]

    # Zero baseline (dashed)
    parts.append(
        f'<line x1="{pad_x}" y1="{y_zero:.2f}" x2="{width-pad_x}" y2="{y_zero:.2f}" '
        f'stroke="{ZERO_COLOR}" stroke-width="0.5" stroke-dasharray="2,2"/>'
    )

    # Bars
    for i, val in enumerate(cum_returns):
        if val is None:
            continue
        x = pad_x + i * bar_slot + bar_gap / 2
        # bar height proportional to abs(val) / abs_max
        bar_h = (abs(val) / abs_max) * (plot_h / 2)
        if val >= 0:
            y_top = y_zero - bar_h
            y_bot = y_zero
            color = UP_COLOR
        else:
            y_top = y_zero
            y_bot = y_zero + bar_h
            color = DOWN_COLOR
        parts.append(_bar(x, y_top, y_bot, bar_w, color))

    # Terminal marker (small triangle at top of bar)
    if terminal_status and terminal_track_day:
        idx = terminal_track_day - 1
        if 0 <= idx < expected_days:
            x = pad_x + idx * bar_slot + bar_slot / 2
            if terminal_status == "stop_loss":
                # Down triangle (warning) at top
                parts.append(
                    f'<polygon points="{x-3},{pad_y} {x+3},{pad_y} {x},{pad_y+4}" '
                    f'fill="{TERMINAL_COLOR_STOP}"/>'
                )
            elif terminal_status == "take_profit":
                # Up triangle (success) at top
                parts.append(
                    f'<polygon points="{x-3},{pad_y+4} {x+3},{pad_y+4} {x},{pad_y}" '
                    f'fill="{TERMINAL_COLOR_TP}"/>'
                )
            elif terminal_status == "expired":
                # Small circle at top
                parts.append(
                    f'<circle cx="{x:.2f}" cy="{pad_y+2}" r="2" fill="{ZERO_COLOR}"/>'
                )

    parts.append('</svg>')
    return "".join(parts)


def cum_returns_from_tracking(
    tracking_rows: list[dict],
    expected_days: int = 15,
) -> list[float | None]:
    """Convert tracking rows to a fixed-length cum_returns list.

    Args:
        tracking_rows: list of dicts with 'track_day' and 'cum_return'
        expected_days: list length to return (default 15)

    Returns:
        list[float|None] of length expected_days. Index = T+(i+1).
        Missing days are None.
    """
    out = [None] * expected_days
    for row in tracking_rows:
        td = int(row["track_day"])
        if 1 <= td <= expected_days:
            cr = row["cum_return"]
            out[td - 1] = float(cr) if cr is not None else None
    return out
