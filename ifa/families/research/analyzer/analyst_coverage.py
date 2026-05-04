"""§10 — Analyst coverage analytics.

Tushare research_report doesn't expose rating / target price as structured
fields (those live in the PDF body). So §10 is honest about what we can
compute from titles + metadata alone:

  · Coverage volume — reports per month over the last 6 months
  · Active institutions — top N inst_csname by report count
  · Coverage gap — if newest report > 90 days ago, signal "attention fading"

LLM-driven theme clustering (what are sell-side reports about?) is a
separate optional layer in llm_aug.analyst_themes(), invoked from the
report builder when --llm is on.

This module is pure rule-based (no LLM dependency).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ifa.core.report.timezones import bjt_now


@dataclass
class AnalystCoverage:
    total_reports: int = 0
    reports_by_month: list[dict] = field(default_factory=list)  # [{month: '202604', count: 3}, ...]
    top_institutions: list[dict] = field(default_factory=list)  # [{name, count, latest_date}, ...]
    recent_reports: list[dict] = field(default_factory=list)  # newest metadata rows for analyst reading
    latest_report_date: str | None = None
    days_since_latest: int | None = None
    coverage_gap_warning: bool = False


def compute_coverage(
    reports: list[dict],
    *,
    on_date: date | None = None,
    months_back: int = 6,
    top_n: int = 5,
    gap_threshold_days: int = 90,
) -> AnalystCoverage:
    """Aggregate research_report rows into coverage stats.

    Args:
        reports: list of dicts as returned by Tushare research_report
                 (keys: title / author / inst_csname / trade_date / url).
        on_date: BJT 'today' for windowing (default: bjt_now().date()).
        months_back: how many months of history to bucket.
        top_n: return the N most active institutions.
        gap_threshold_days: warn if newest report older than this.
    """
    on_date = on_date or bjt_now().date()
    cov = AnalystCoverage()
    if not reports:
        return cov

    # Parse dates and discard malformed rows
    parsed: list[tuple[date, dict]] = []
    for r in reports:
        d = _parse_yyyymmdd(r.get("trade_date") or r.get("report_date") or "")
        if d is not None:
            parsed.append((d, r))
    if not parsed:
        return cov

    parsed.sort(key=lambda x: x[0], reverse=True)  # newest first by date only
    cov.total_reports = len(parsed)
    latest_d = parsed[0][0]
    cov.latest_report_date = latest_d.strftime("%Y-%m-%d")
    cov.days_since_latest = (on_date - latest_d).days
    cov.coverage_gap_warning = cov.days_since_latest > gap_threshold_days
    cov.recent_reports = [
        {
            "date": d.strftime("%Y-%m-%d"),
            "title": str(r.get("title") or "").strip(),
            "institution": str(r.get("inst_csname") or r.get("institution") or "").strip(),
            "author": str(r.get("author") or "").strip(),
            "url": str(r.get("url") or "").strip(),
        }
        for d, r in parsed[:top_n]
    ]

    # Bucket by month for the trailing window
    cutoff = on_date.replace(day=1)
    for _ in range(months_back - 1):
        prev = cutoff - timedelta(days=1)
        cutoff = prev.replace(day=1)
    counts: dict[str, int] = defaultdict(int)
    for d, _ in parsed:
        if d >= cutoff:
            counts[d.strftime("%Y%m")] += 1
    # Fill in zero months so the sparkline is dense
    months: list[str] = []
    cur = cutoff
    while cur <= on_date:
        months.append(cur.strftime("%Y%m"))
        # Move to next month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    cov.reports_by_month = [
        {"month": m, "count": counts.get(m, 0)} for m in months
    ]

    # Top institutions in the windowed range
    inst_counter: Counter[str] = Counter()
    inst_latest: dict[str, date] = {}
    for d, r in parsed:
        if d < cutoff:
            continue
        name = (r.get("inst_csname") or r.get("institution") or "").strip()
        if not name:
            continue
        inst_counter[name] += 1
        if name not in inst_latest or d > inst_latest[name]:
            inst_latest[name] = d
    cov.top_institutions = [
        {
            "name": name,
            "count": count,
            "latest_date": inst_latest[name].strftime("%Y-%m-%d"),
        }
        for name, count in inst_counter.most_common(top_n)
    ]
    return cov


def _parse_yyyymmdd(s: str) -> date | None:
    s = str(s or "").strip()
    if len(s) >= 8 and s[:8].isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None
