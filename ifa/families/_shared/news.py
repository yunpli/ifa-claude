"""News post-processing helpers shared across report families.

Goal: never display 'NaT', '待更新', or empty timestamps in the rendered HTML.
LLM tends to fabricate placeholders when it isn't sure — we always overwrite
`time_display` from the source publish_time, formatted in Beijing time.
Events that have no parseable publish_time are dropped entirely.
"""
from __future__ import annotations

import datetime as dt
import re

from ifa.core.report.timezones import BJT, to_bjt

_BAD_TIME_DISPLAYS = ("NaT", "nat", "待更新", "未知", "unknown", "Unknown", "")


def _try_parse(s: str) -> dt.datetime | None:
    if not s:
        return None
    s = str(s).strip()
    if "NaT" in s or s in _BAD_TIME_DISPLAYS:
        return None
    # Common forms: ISO with tz (`2026-04-30T13:58:05+08:00`), naive ISO,
    # space-separated.
    s_norm = s.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        return dt.datetime.fromisoformat(s_norm)
    except ValueError:
        pass
    # Fallback: regex grab YYYY-MM-DD HH:MM
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})[T\s](\d{1,2}):(\d{2})", s_norm)
    if m:
        try:
            y, mo, d, h, mi = (int(x) for x in m.groups())
            return dt.datetime(y, mo, d, h, mi, tzinfo=BJT)
        except ValueError:
            return None
    return None


def format_bjt_short(s: str | None) -> str | None:
    """Return 'MM-DD HH:MM' BJT, or None if input can't be parsed."""
    if s is None:
        return None
    parsed = _try_parse(str(s))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BJT)
    return to_bjt(parsed).strftime("%m-%d %H:%M")


def post_process_news_events(
    events: list[dict],
    originals: list[dict],
    *,
    drop_invalid_time: bool = True,
) -> list[dict]:
    """Walk LLM-returned `events`, look up the original candidate by title,
    overwrite time_display from the source publish_time. Drop events whose
    time we can't parse if `drop_invalid_time=True`.
    """
    by_title: dict[str, dict] = {}
    for o in originals:
        t = o.get("title")
        if t:
            by_title[str(t).strip()] = o
    out: list[dict] = []
    for e in events or []:
        if not e:
            continue
        title = str(e.get("title", "")).strip()
        original = by_title.get(title)
        # Prefer original.publish_time → fmt_bjt_short; fallback to LLM time_display
        td: str | None = None
        if original:
            td = format_bjt_short(original.get("publish_time"))
        if not td:
            llm_td = e.get("time_display")
            if llm_td and not any(b in str(llm_td) for b in _BAD_TIME_DISPLAYS):
                # LLM may have produced a sane string; keep but normalise spaces
                td = str(llm_td).strip()
        if td is None and drop_invalid_time:
            continue
        if td is not None:
            e["time_display"] = td
        out.append(e)
    return out
