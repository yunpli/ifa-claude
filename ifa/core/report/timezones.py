"""UTC ↔ Beijing-time helpers.

The system stores everything in UTC (timestamptz), and renders everything in
Beijing time (UTC+8). This module is the only place that knows the offset.
"""
from __future__ import annotations

import datetime as dt

BJT = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def bjt_now() -> dt.datetime:
    return dt.datetime.now(BJT)


def to_bjt(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(BJT)


def fmt_bjt(d: dt.datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    bjt = to_bjt(d)
    return bjt.strftime(fmt) if bjt else "—"


def parse_bjt_cutoff(date_str: str, time_str: str) -> dt.datetime:
    """Parse a Beijing-time cutoff like ('2026-04-30', '08:45') → tz-aware UTC dt."""
    naive = dt.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return naive.replace(tzinfo=BJT).astimezone(dt.timezone.utc)
