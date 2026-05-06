"""Banner-level data-staleness detection for report builders.

If any data point in the report carries a `trade_date` (or equivalent) that
does not match the intended `report_date`, the renderer must surface a banner
warning rather than silently presenting stale data as today's print.

Usage from a family builder:

    from ifa.core.render.staleness import compute_staleness_warning

    warning = compute_staleness_warning(
        report_date=run.report_date,
        dated_objects=[ctx.breadth, *ctx.indices, *ctx.sw_sectors],
    )
    if warning:
        report["staleness_warning"] = warning  # consumed by report.html

The helper accepts any object with a `.trade_date: date | None` attribute, or
a dict with key `"trade_date"`. None values are ignored (means data missing /
intentionally suppressed by upstream staleness gate — that's a separate concern
already handled in the data layer; this helper only flags wrong-date data).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable


def _extract_date(obj: Any) -> dt.date | None:
    if obj is None:
        return None
    val = None
    if hasattr(obj, "trade_date"):
        val = getattr(obj, "trade_date", None)
    elif isinstance(obj, dict):
        val = obj.get("trade_date")
    if val is None:
        return None
    if isinstance(val, dt.datetime):
        return val.date()
    if isinstance(val, dt.date):
        return val
    if isinstance(val, str):
        # accept "YYYY-MM-DD" or "YYYYMMDD"
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return dt.datetime.strptime(val, fmt).date()
            except ValueError:
                continue
    return None


def compute_staleness_warning(
    *,
    report_date: dt.date,
    dated_objects: Iterable[Any],
) -> str | None:
    """Return a Chinese warning string if any object's trade_date < report_date,
    else None. Future dates are ignored (treated as bad data, not staleness)."""
    stale_dates: list[dt.date] = []
    for obj in dated_objects:
        d = _extract_date(obj)
        if d is None:
            continue
        if d < report_date:
            stale_dates.append(d)
    if not stale_dates:
        return None
    most_recent = max(stale_dates)
    return (
        f"⚠ 部分数据未更新至 {report_date.strftime('%Y-%m-%d')}，"
        f"最新可用日期为 {most_recent.strftime('%Y-%m-%d')}。"
        "报告中显示为「—」的字段为防止伪造而留空。"
    )
