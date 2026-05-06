"""Inline SVG charts for Stock Edge reports.

Charts are report artifacts, not production PNG files. The first functional
version renders deterministic SVG from local daily bars so the report carries
visual evidence without adding browser or image-output side effects.
"""
from __future__ import annotations

import math
from html import escape
from typing import Any, Iterable

import pandas as pd

UP = "#c23a2b"  # China market convention: up is red.
DOWN = "#16824a"  # China market convention: down is green.
INK = "#263241"
MUTED = "#7a8594"
GRID = "#d8dde6"
MA5 = "#5b6f95"
MA20 = "#c47b2f"
MA60 = "#6f5aa7"


def build_chart_context(
    daily_bars: pd.DataFrame,
    *,
    max_rows: int = 60,
    price_levels: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Build the default Stock Edge chart set from daily OHLCV bars."""
    df = _prepare_daily(daily_bars, max_rows=max_rows)
    if df.empty:
        return {"daily_kline_svg": "", "macd_svg": "", "momentum_svg": ""}
    return {
        "daily_kline_svg": _daily_kline_svg(df, price_levels=price_levels or []),
        "macd_svg": _macd_svg(df),
        "momentum_svg": _momentum_svg(df),
    }


def build_peer_context_charts(peers: list[dict[str, Any]]) -> dict[str, str]:
    """Build compact SW L2 peer comparison charts.

    The peer data comes from local SmartMoney/TA tables. These SVGs are meant to
    replace a flat leader table with a faster visual read of where the target
    sits versus same-sector leaders.
    """
    rows = _dedupe_peers(peers)
    if not rows:
        return {"peer_size_return_svg": "", "peer_return_ladder_svg": ""}
    return {
        "peer_size_return_svg": _peer_size_return_svg(rows),
        "peer_return_ladder_svg": _peer_return_ladder_svg(rows),
    }


def build_peer_fundamental_chart(rows: list[dict[str, Any]]) -> str:
    """Build the primary same-sector financial-quality comparison chart."""
    rows = [row for row in rows if row.get("fundamental_score") is not None]
    if not rows:
        return ""
    return _peer_fundamental_score_svg(rows)


def _prepare_daily(daily_bars: pd.DataFrame, *, max_rows: int) -> pd.DataFrame:
    required = {"trade_date", "open", "high", "low", "close"}
    if daily_bars.empty or not required.issubset(daily_bars.columns):
        return pd.DataFrame()
    df = daily_bars.sort_values("trade_date").copy().reset_index(drop=True)
    for column in ["open", "high", "low", "close"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["hist"] = (df["macd"] - df["signal"]) * 2
    df["momentum_5d"] = df["close"].pct_change(5) * 100
    return df.tail(max_rows).reset_index(drop=True)


def _daily_kline_svg(df: pd.DataFrame, *, price_levels: list[dict[str, Any]]) -> str:
    width, height = 920, 372
    left, top, right, bottom = 46, 24, 18, 102
    plot_w = width - left - right
    plot_h = height - top - bottom
    level_values = [level.get("price") for level in price_levels[:6]]
    price_values = _finite_values(
        [
            *df["high"].tolist(),
            *df["low"].tolist(),
            *df["ma5"].tolist(),
            *df["ma20"].tolist(),
            *df["ma60"].tolist(),
            *level_values,
        ]
    )
    lo, hi = _padded_domain(price_values)
    x_at = _x_scale(len(df), left, plot_w)
    y_at = _y_scale(lo, hi, top, plot_h)
    candle_w = max(3.0, min(10.0, plot_w / max(len(df), 1) * 0.56))
    parts = [_svg_open(width, height, "日线K线、均线与近期结构")]
    parts.append(_grid(width, left, top, plot_w, plot_h, lo, hi, y_at))
    for i, row in df.iterrows():
        x = x_at(i)
        color = UP if float(row["close"]) >= float(row["open"]) else DOWN
        high_y = y_at(float(row["high"]))
        low_y = y_at(float(row["low"]))
        open_y = y_at(float(row["open"]))
        close_y = y_at(float(row["close"]))
        body_top = min(open_y, close_y)
        body_h = max(1.6, abs(open_y - close_y))
        parts.append(f'<line x1="{x:.2f}" y1="{high_y:.2f}" x2="{x:.2f}" y2="{low_y:.2f}" stroke="{color}" stroke-width="1.2"/>')
        parts.append(
            f'<rect x="{x - candle_w / 2:.2f}" y="{body_top:.2f}" width="{candle_w:.2f}" '
            f'height="{body_h:.2f}" rx="1" fill="{color}" opacity="0.88"/>'
        )
    parts.append(_polyline(df["ma5"], x_at, y_at, MA5, "2.0"))
    parts.append(_polyline(df["ma20"], x_at, y_at, MA20, "2.0"))
    parts.append(_polyline(df["ma60"], x_at, y_at, MA60, "2.0"))
    parts.append(_price_level_overlay(price_levels[:6], width, left, right, y_at, top=top, bottom=bottom, height=height))
    parts.append(_legend([("K线", UP), ("MA5", MA5), ("MA20", MA20), ("MA60", MA60), ("支撑", DOWN), ("压力", UP)], x=left, y=16))
    parts.append(_axis_dates(df, x_at, height - 74))
    parts.append("</svg>")
    return "".join(parts)


def _price_level_overlay(levels: list[dict[str, Any]], width: int, left: int, right: int, y_at, *, top: int, bottom: int, height: int) -> str:
    if not levels:
        return ""
    parts: list[str] = []
    labels: list[dict[str, Any]] = []
    colors = {
        "support": ["#2e8c63", "#4ca27a", "#7bb89a"],
        "resistance": ["#b6493d", "#c86657", "#d98a7e"],
    }
    support_seen = 0
    resistance_seen = 0
    for level in levels:
        price = level.get("price")
        if price is None:
            continue
        kind = str(level.get("kind") or "")
        if kind == "support":
            idx = support_seen
            support_seen += 1
        elif kind == "resistance":
            idx = resistance_seen
            resistance_seen += 1
        else:
            idx = 0
        palette = colors.get(kind, ["#3b6f78"])
        color = palette[min(idx, len(palette) - 1)]
        y = y_at(float(price))
        strength = float(level.get("strength") or 0.5)
        stroke = 1.2 + min(max(strength, 0.0), 1.0) * 1.8
        dash = "none" if strength >= 0.65 else "6 4"
        label = f'{level.get("source_label") or level.get("kind_label")}: {float(price):.2f}'
        parts.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" '
            f'stroke="{color}" stroke-width="{stroke:.2f}" stroke-dasharray="{dash}" opacity="0.78"/>'
        )
        labels.append({"color": color, "label": label, "order": len(labels)})
    legend_y = height - bottom + 32
    for i, row in enumerate(labels[:6]):
        col = i % 2
        row_idx = i // 2
        x = left + col * 410
        y = legend_y + row_idx * 21
        parts.append(f'<line x1="{x}" y1="{y}" x2="{x + 26}" y2="{y}" stroke="{row["color"]}" stroke-width="3.2"/>')
        parts.append(
            f'<text x="{x + 34}" y="{y + 5}" font-size="12.5" fill="{row["color"]}" '
            f'font-weight="700">{escape(str(row["label"]))}</text>'
        )
    return "".join(parts)


def _spread_price_labels(labels: list[dict[str, Any]], *, min_y: float, max_y: float, min_gap: float) -> list[dict[str, Any]]:
    """Keep dense support/resistance labels readable without moving price lines."""
    if not labels:
        return []
    ordered = sorted(labels, key=lambda row: float(row["y"]))
    placed: list[dict[str, Any]] = []
    cursor = min_y
    for row in ordered:
        label_y = max(float(row["y"]) - 4.0, cursor)
        label_y = min(label_y, max_y)
        placed.append({**row, "label_y": label_y})
        cursor = label_y + min_gap
    overflow = placed[-1]["label_y"] - max_y
    if overflow > 0:
        for row in reversed(placed):
            row["label_y"] = max(min_y, float(row["label_y"]) - overflow)
            overflow = max(0.0, min_y - float(row["label_y"]))
            if overflow <= 0:
                break
    return sorted(placed, key=lambda row: int(row["order"]))


def _dedupe_peers(peers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    target: dict[str, Any] | None = None
    for row in peers:
        if not _is_active_peer_for_chart(row):
            continue
        code = str(row.get("ts_code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        if row.get("is_target"):
            target = row
        rows.append(row)
    return _keep_target(rows, target=target, limit=12)


def _is_active_peer_for_chart(row: dict[str, Any]) -> bool:
    if row.get("is_target"):
        return True
    list_status = str(row.get("list_status") or "").strip().upper()
    if list_status and list_status != "L":
        return False
    name = str(row.get("name") or "").replace(" ", "")
    if "退市" in name or "退(" in name or name.endswith("退"):
        return False
    return True


def _keep_target(rows: list[dict[str, Any]], *, target: dict[str, Any] | None = None, limit: int) -> list[dict[str, Any]]:
    if len(rows) <= limit:
        return rows
    target = target or next((row for row in rows if row.get("is_target")), None)
    trimmed = rows[:limit]
    if target is None or any(row.get("ts_code") == target.get("ts_code") for row in trimmed):
        return trimmed
    return [*trimmed[: max(limit - 1, 0)], target]


def _peer_size_return_svg(rows: list[dict[str, Any]]) -> str:
    width, height = 920, 320
    left, top, right, bottom = 86, 34, 34, 58
    plot_w = width - left - right
    plot_h = height - top - bottom
    returns = _finite_values([row.get("return_5d_pct") for row in rows] + [0])
    mvs = _finite_values([row.get("total_mv") for row in rows])
    x_lo, x_hi = _symmetric_domain(returns)
    y_lo, y_hi = _padded_domain([math.log10(max(v, 1.0)) for v in mvs] or [0, 1])
    x_at = lambda v: left + (float(v) - x_lo) / (x_hi - x_lo or 1.0) * plot_w
    y_at = lambda v: top + (y_hi - math.log10(max(float(v), 1.0))) / (y_hi - y_lo or 1.0) * plot_h
    parts = [_svg_open(width, height, "同板块市值与5日涨跌幅对照")]
    parts.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fff" stroke="{GRID}"/>')
    for t in [-1.0, -0.5, 0.0, 0.5, 1.0]:
        value = x_lo + (x_hi - x_lo) * (t + 1.0) / 2.0
        x = x_at(value)
        parts.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + plot_h}" stroke="{GRID}" stroke-width="1" opacity="0.62"/>')
        parts.append(f'<text x="{x:.2f}" y="{top + plot_h + 18}" text-anchor="middle" font-size="10" fill="{MUTED}">{_fmt_signed_axis_pct(value)}</text>')
    for frac in [0.0, 0.25, 0.50, 0.75, 1.0]:
        log_value = y_hi - (y_hi - y_lo) * frac
        mv = 10 ** log_value
        y = top + plot_h * frac
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="{GRID}" stroke-width="1" opacity="0.50"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.2f}" text-anchor="end" font-size="10" fill="{MUTED}">{_fmt_market_cap_axis(mv)}</text>')
    zero_x = x_at(0.0)
    parts.append(f'<line x1="{zero_x:.2f}" y1="{top}" x2="{zero_x:.2f}" y2="{top + plot_h}" stroke="{INK}" stroke-width="1.3" opacity="0.38"/>')
    for row in rows:
        ret = row.get("return_5d_pct")
        mv = row.get("total_mv")
        if ret is None or mv is None:
            continue
        color = UP if float(ret) >= 0 else DOWN
        r = 8.0 if row.get("is_target") else 5.5
        x = x_at(float(ret))
        y = y_at(float(mv))
        label = escape(str(row.get("name") or row.get("ts_code")))
        if row.get("is_target"):
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r + 5:.2f}" fill="none" stroke="{INK}" stroke-width="2.2" opacity="0.95"/>')
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r + 1.6:.2f}" fill="{color}" opacity="0.92"/>')
        else:
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r}" fill="{color}" opacity="0.82"/>')
        tx = min(width - right - 58, max(left + 4, x + 8))
        parts.append(f'<text x="{tx:.2f}" y="{y - 6:.2f}" font-size="10" fill="{INK}" font-weight="{700 if row.get("is_target") else 500}">{label}</text>')
    parts.append(f'<text x="{left + plot_w / 2:.2f}" y="{height - 26}" text-anchor="middle" font-size="11" fill="{MUTED}">5日涨跌幅（%）</text>')
    parts.append(f'<text x="18" y="{top + plot_h / 2:.2f}" transform="rotate(-90 18 {top + plot_h / 2:.2f})" text-anchor="middle" font-size="11" fill="{MUTED}">总市值（亿元，近似）</text>')
    parts.append(f'<text x="{left}" y="{height - 8}" font-size="10.5" fill="{MUTED}">样本：同板块市值/动量/资金/TA代表股；目标股黑色外圈。红=5日上涨，绿=5日下跌。</text>')
    parts.append("</svg>")
    return "".join(parts)


def _peer_return_ladder_svg(rows: list[dict[str, Any]]) -> str:
    width, height = 920, 442
    left, top, right, bottom = 132, 54, 34, 42
    sorted_rows = sorted(rows, key=lambda row: float(row.get("return_15d_pct") if row.get("return_15d_pct") is not None else row.get("return_5d_pct") or -999), reverse=True)
    rows = _keep_target(sorted_rows, limit=8)
    series_by_code = {str(row.get("ts_code") or i): _daily_return_series(row) for i, row in enumerate(rows)}
    daily_values = [value for series in series_by_code.values() for value in series]
    max_abs_daily = max([abs(value) for value in daily_values] + [2.0])
    # Use a capped visual scale so one extreme limit-up day does not flatten
    # normal daily bars. Direction and ordering remain faithful; height is
    # square-root compressed for readability.
    visual_scale = min(max_abs_daily * 1.02, 10.0)
    bar_area_w = 510
    metric_x = left + bar_area_w + 42
    metric_cols = [metric_x, metric_x + 54, metric_x + 108]
    row_h = (height - top - bottom) / max(len(rows), 1)
    parts = [_svg_open(width, height, "同板块15日每日涨跌走势")]
    parts.append(f'<text x="{left}" y="{top - 22}" font-size="11.5" fill="{MUTED}" font-weight="700">最近15个交易日每日涨跌</text>')
    for label, x in zip(["5日", "10日", "15日"], metric_cols, strict=True):
        parts.append(f'<text x="{x}" y="{top - 22}" text-anchor="middle" font-size="11.5" fill="{MUTED}" font-weight="700">{label}</text>')
    for i, row in enumerate(rows):
        y = top + i * row_h + row_h * 0.50
        label = escape(str(row.get("name") or row.get("ts_code")))
        if row.get("is_target"):
            parts.append(
                f'<rect x="{left - 112}" y="{y - row_h * 0.46:.2f}" width="{width - left - right + 112}" '
                f'height="{row_h * 0.92:.2f}" rx="6" fill="#fff3cd" stroke="#d8b45c" opacity="0.95"/>'
            )
        parts.append(f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" font-size="11" fill="{INK}" font-weight="{700 if row.get("is_target") else 500}">{label}{ " 目标股" if row.get("is_target") else ""}</text>')
        series = series_by_code.get(str(row.get("ts_code") or i), [])[-15:]
        bar_gap = 4.0
        bar_w = max(7.0, (bar_area_w - 14 * bar_gap) / 15)
        start_x = left
        zero_y = y
        max_h = row_h * 0.46
        parts.append(f'<line x1="{start_x - 4:.2f}" y1="{zero_y:.2f}" x2="{start_x + 15 * (bar_w + bar_gap):.2f}" y2="{zero_y:.2f}" stroke="{GRID}" stroke-width="1.1"/>')
        start_day = max(1, 16 - len(series))
        for day, value in enumerate(series, start=start_day):
            color = UP if value >= 0 else DOWN
            visual_ratio = math.sqrt(min(abs(value), visual_scale) / visual_scale) if visual_scale > 0 else 0.0
            h = max(2.2, visual_ratio * max_h)
            x = start_x + (day - 1) * (bar_w + bar_gap)
            bar_y = zero_y - h if value >= 0 else zero_y
            parts.append(f'<rect x="{x:.2f}" y="{bar_y:.2f}" width="{bar_w:.2f}" height="{h:.2f}" rx="1.2" fill="{color}" opacity="0.88"/>')
        for value, x in zip([row.get("return_5d_pct"), row.get("return_10d_pct"), row.get("return_15d_pct")], metric_cols, strict=True):
            color = _pct_color(value)
            parts.append(f'<text x="{x}" y="{y + 4:.2f}" text-anchor="middle" font-size="11.5" fill="{color}" font-weight="800">{_fmt_optional_pct(value)}</text>')
    parts.append(_legend([("红=当日上涨", UP), ("绿=当日下跌", DOWN), ("黄底=目标股", "#d8b45c")], x=left, y=18))
    parts.append(f'<text x="{left}" y="{height - 12}" font-size="11" fill="{MUTED}">柱高做视觉增强缩放，当前满刻度约 ±{visual_scale:.1f}%；右侧为 5/10/15 日累计涨跌幅。</text>')
    parts.append("</svg>")
    return "".join(parts)


def _peer_fundamental_score_svg(rows: list[dict[str, Any]]) -> str:
    width, height = 920, 330
    left, top, right, bottom = 124, 28, 34, 34
    rows = _keep_target(sorted(rows, key=lambda row: float(row.get("fundamental_score") or -999), reverse=True), limit=8)
    plot_w = width - left - right
    row_h = (height - top - bottom) / max(len(rows), 1)
    parts = [_svg_open(width, height, "同板块财务质量对比")]
    parts.append(f'<line x1="{left}" y1="{top - 8}" x2="{left}" y2="{height - bottom + 4}" stroke="{GRID}" stroke-width="1.2"/>')
    for i, row in enumerate(rows):
        y = top + i * row_h + row_h * 0.50
        score = _clip(float(row.get("fundamental_score") or 0.0), 0.0, 1.0)
        x2 = left + score * plot_w
        label = escape(str(row.get("name") or row.get("ts_code")))
        if row.get("is_target"):
            parts.append(
                f'<rect x="8" y="{y - row_h * 0.46:.2f}" width="{width - 16}" '
                f'height="{row_h * 0.92:.2f}" rx="6" fill="#fff3cd" stroke="#d8b45c" opacity="0.95"/>'
            )
        color = "#2f6f91" if not row.get("is_target") else "#263241"
        parts.append(f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" font-size="11" fill="{INK}" font-weight="{700 if row.get("is_target") else 500}">{label}{ " 目标股" if row.get("is_target") else ""}</text>')
        parts.append(f'<rect x="{left}" y="{y - 7:.2f}" width="{max(2.0, x2 - left):.2f}" height="14" rx="3" fill="{color}" opacity="{0.88 if row.get("is_target") else 0.70}"/>')
        parts.append(f'<text x="{min(width - right - 34, x2 + 8):.2f}" y="{y + 4:.2f}" font-size="11" fill="{INK}" font-weight="700">{score:.0%}</text>')
        roe = row.get("annual_roe")
        growth = row.get("annual_growth")
        cfo = row.get("annual_cfo_ni")
        debt = row.get("annual_debt")
        detail = f'ROE {_fmt_optional_pct(roe)} · 营收 {_fmt_optional_pct(growth)} · CFO/NI {_fmt_optional_num(cfo)} · 负债 {_fmt_optional_pct(debt)}'
        parts.append(f'<text x="{left + 260}" y="{y + 18:.2f}" font-size="9.5" fill="{MUTED}">{escape(detail)}</text>')
    parts.append(f'<text x="{left}" y="{height - 8}" font-size="11" fill="{MUTED}">主图：财报质量综合分，基于 ROE、营收增速、CFO/NI、资产负债率及估值分位；市值和短线涨跌幅仅作辅助。</text>')
    parts.append("</svg>")
    return "".join(parts)


def _fmt_optional_pct(value: Any) -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_optional_num(value: Any) -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_signed_axis_pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.0f}%"


def _fmt_market_cap_axis(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    # TuShare daily_basic.total_mv is in 10k CNY. Convert to 100M CNY.
    yi = number / 10000.0
    if yi >= 10000:
        return f"{yi / 10000:.1f}万亿"
    if yi >= 1000:
        return f"{yi:.0f}亿"
    if yi >= 100:
        return f"{yi:.0f}亿"
    return f"{yi:.1f}亿"


def _pct_color(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return MUTED
    if number > 0:
        return UP
    if number < 0:
        return DOWN
    return MUTED


def _daily_return_series(row: dict[str, Any]) -> list[float]:
    raw = row.get("daily_returns_15d") or []
    values: list[float] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = item.get("pct_chg")
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    if values:
        return values[-15:]
    # Fallback for old cached reports that only have aggregate returns.
    return _fallback_daily_from_aggregate(row)


def _fallback_daily_from_aggregate(row: dict[str, Any]) -> list[float]:
    r5 = _optional_float(row.get("return_5d_pct")) or 0.0
    r10 = _optional_float(row.get("return_10d_pct"))
    r15 = _optional_float(row.get("return_15d_pct"))
    first = (r15 - r10) / 5.0 if r15 is not None and r10 is not None else 0.0
    second = (r10 - r5) / 5.0 if r10 is not None else 0.0
    third = r5 / 5.0
    return [first] * 5 + [second] * 5 + [third] * 5


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _macd_svg(df: pd.DataFrame) -> str:
    width, height = 920, 210
    left, top, right, bottom = 46, 24, 18, 28
    plot_w = width - left - right
    plot_h = height - top - bottom
    values = _finite_values([*df["macd"].tolist(), *df["signal"].tolist(), *df["hist"].tolist(), 0.0])
    lo, hi = _symmetric_domain(values)
    x_at = _x_scale(len(df), left, plot_w)
    y_at = _y_scale(lo, hi, top, plot_h)
    zero_y = y_at(0.0)
    bar_w = max(2.0, min(8.0, plot_w / max(len(df), 1) * 0.52))
    parts = [_svg_open(width, height, "MACD 趋势确认")]
    parts.append(_grid(width, left, top, plot_w, plot_h, lo, hi, y_at))
    parts.append(f'<line x1="{left}" y1="{zero_y:.2f}" x2="{width - right}" y2="{zero_y:.2f}" stroke="{INK}" stroke-width="1" opacity="0.35"/>')
    for i, value in enumerate(df["hist"]):
        if pd.isna(value):
            continue
        v = float(value)
        x = x_at(i)
        y = y_at(v)
        color = UP if v >= 0 else DOWN
        parts.append(
            f'<rect x="{x - bar_w / 2:.2f}" y="{min(y, zero_y):.2f}" width="{bar_w:.2f}" '
            f'height="{max(1.0, abs(y - zero_y)):.2f}" fill="{color}" opacity="0.62"/>'
        )
    parts.append(_polyline(df["macd"], x_at, y_at, MA5, "2.0"))
    parts.append(_polyline(df["signal"], x_at, y_at, MA20, "2.0"))
    parts.append(_legend([("柱体", UP), ("DIF", MA5), ("DEA", MA20)], x=left, y=16))
    parts.append("</svg>")
    return "".join(parts)


def _momentum_svg(df: pd.DataFrame) -> str:
    width, height = 920, 190
    left, top, right, bottom = 46, 24, 18, 28
    plot_w = width - left - right
    plot_h = height - top - bottom
    values = _finite_values([*df["momentum_5d"].tolist(), 0.0])
    lo, hi = _symmetric_domain(values)
    x_at = _x_scale(len(df), left, plot_w)
    y_at = _y_scale(lo, hi, top, plot_h)
    zero_y = y_at(0.0)
    bar_w = max(2.0, min(8.0, plot_w / max(len(df), 1) * 0.52))
    parts = [_svg_open(width, height, "5日动量")]
    parts.append(_grid(width, left, top, plot_w, plot_h, lo, hi, y_at))
    parts.append(f'<line x1="{left}" y1="{zero_y:.2f}" x2="{width - right}" y2="{zero_y:.2f}" stroke="{INK}" stroke-width="1" opacity="0.35"/>')
    for i, value in enumerate(df["momentum_5d"]):
        if pd.isna(value):
            continue
        v = float(value)
        x = x_at(i)
        y = y_at(v)
        color = UP if v >= 0 else DOWN
        parts.append(
            f'<rect x="{x - bar_w / 2:.2f}" y="{min(y, zero_y):.2f}" width="{bar_w:.2f}" '
            f'height="{max(1.0, abs(y - zero_y)):.2f}" fill="{color}" opacity="0.70"/>'
        )
    parts.append(_legend([("5日动量为正", UP), ("5日动量为负", DOWN)], x=left, y=16))
    parts.append("</svg>")
    return "".join(parts)


def _svg_open(width: int, height: int, title: str) -> str:
    return (
        f'<svg class="stock-chart__svg" role="img" aria-label="{escape(title)}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
        f"<title>{escape(title)}</title>"
    )


def _grid(width: int, left: int, top: int, plot_w: int, plot_h: int, lo: float, hi: float, y_at) -> str:
    parts: list[str] = []
    for t in [0.0, 0.25, 0.50, 0.75, 1.0]:
        value = hi - (hi - lo) * t
        y = y_at(value)
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="{GRID}" stroke-width="1" opacity="0.75"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.2f}" text-anchor="end" font-size="10" fill="{MUTED}">{value:.2f}</text>')
    parts.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="none" stroke="{GRID}" stroke-width="1"/>')
    return "".join(parts)


def _legend(items: list[tuple[str, str]], *, x: int, y: int) -> str:
    parts: list[str] = []
    cursor = x
    for label, color in items:
        text_w = max(26, len(label) * 11)
        parts.append(f'<line x1="{cursor}" y1="{y}" x2="{cursor + 18}" y2="{y}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{cursor + 24}" y="{y + 4}" font-size="11" fill="{MUTED}">{escape(label)}</text>')
        cursor += text_w + 34
    return "".join(parts)


def _axis_dates(df: pd.DataFrame, x_at, y: int) -> str:
    if df.empty:
        return ""
    indices = sorted({0, len(df) // 2, len(df) - 1})
    parts = []
    for idx in indices:
        raw = df["trade_date"].iloc[idx]
        label = pd.to_datetime(raw).strftime("%m-%d")
        parts.append(f'<text x="{x_at(idx):.2f}" y="{y}" text-anchor="middle" font-size="10" fill="{MUTED}">{label}</text>')
    return "".join(parts)


def _polyline(series: pd.Series, x_at, y_at, color: str, stroke_width: str) -> str:
    points = []
    for i, value in enumerate(series):
        if pd.isna(value):
            continue
        points.append(f"{x_at(i):.2f},{y_at(float(value)):.2f}")
    if len(points) < 2:
        return ""
    return f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="{stroke_width}" stroke-linejoin="round" stroke-linecap="round"/>'


def _x_scale(n: int, left: int, plot_w: int):
    if n <= 1:
        return lambda _i: left + plot_w / 2
    return lambda i: left + plot_w * i / (n - 1)


def _y_scale(lo: float, hi: float, top: int, plot_h: int):
    span = hi - lo if hi != lo else 1.0
    return lambda value: top + (hi - value) / span * plot_h


def _finite_values(values: Iterable[float | int | None]) -> list[float]:
    finite: list[float] = []
    for value in values:
        if value is None or pd.isna(value):
            continue
        finite.append(float(value))
    return finite


def _padded_domain(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.08, abs(hi) * 0.01, 0.01)
    return lo - pad, hi + pad


def _symmetric_domain(values: list[float]) -> tuple[float, float]:
    if not values:
        return -1.0, 1.0
    max_abs = max(abs(min(values)), abs(max(values)), 0.01)
    max_abs *= 1.12
    return -max_abs, max_abs
