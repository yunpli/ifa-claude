"""M2.3c · Timeline — chronological event list from all disclosure sources.

Merges: announcements / forecasts / expresses / research_reports / irm_qa
Each event: event_type / publish_time / title / source_url / summary
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.report.timezones import BJT, to_bjt  # noqa: F401

from ifa.families.research.analyzer.data import CompanyFinancialSnapshot

EVENT_TYPE_PRIORITY: dict[str, int] = {
    "audit": 0,
    "forecast": 1,
    "express": 2,
    "announcement": 3,
    "research": 4,
    "irm_qa": 5,
}


@dataclass
class TimelineEvent:
    event_type: str        # 'announcement' | 'forecast' | 'express' | 'research' | 'irm_qa' | 'audit'
                           # Or any structured event_type from company_event_memory:
                           # 'earnings_beat' | 'guidance' | 'shareholding_change' | ...
    publish_time: str      # ISO date string (YYYYMMDD or YYYY-MM-DD)
    title: str
    source_url: str = ""
    summary: str = ""
    raw: dict = field(default_factory=dict)
    # ── LLM-extracted metadata (None when source is raw snapshot) ──────
    polarity: str | None = None       # 'positive' | 'negative' | 'neutral'
    importance: str | None = None     # 'high' | 'medium' | 'low'
    is_extracted: bool = False        # True if from company_event_memory

    def sort_key(self) -> tuple[str, int]:
        # Sort descending by date, then by event type priority. Extracted
        # events get priority -1 so they bubble above raw items on the same
        # date (their structured metadata is more valuable).
        priority = -1 if self.is_extracted else EVENT_TYPE_PRIORITY.get(self.event_type, 99)
        return (self.publish_time, priority)


def build_timeline(
    snap: CompanyFinancialSnapshot,
    engine: Engine | None = None,
) -> list[TimelineEvent]:
    """Merge all disclosure sources into a chronological event list (newest first).

    When `engine` is provided, the timeline is **enriched** by replacing raw
    items with their LLM-extracted counterparts from research.company_event_memory
    (matched on source_url + publish_time). Items without an extracted match
    fall through as raw events.

    Pass engine=None for tests or when the event memory table is empty.
    """
    events: list[TimelineEvent] = []

    events.extend(_from_announcements(snap.announcements))
    events.extend(_from_forecasts(snap.forecasts))
    events.extend(_from_expresses(snap.expresses))
    events.extend(_from_research_reports(snap.research_reports))
    events.extend(_from_irm_qa(snap.irm_qa))
    events.extend(_from_audit(snap.audit_records))

    if engine is not None:
        events = _enrich_with_memory(events, snap.company.ts_code, engine)

    # Sort newest first; ties broken by event type priority
    events.sort(key=lambda e: e.sort_key(), reverse=True)
    return events


# ─── Enrichment from company_event_memory ─────────────────────────────────────

def _enrich_with_memory(
    raw_events: list[TimelineEvent],
    ts_code: str,
    engine: Engine,
) -> list[TimelineEvent]:
    """Replace raw events with extracted versions where matched.

    Match rule: same source_url AND same date. URL-less items (irm_qa, audit)
    fall back to (raw_title prefix, date) match. Unmatched raw events stay as-is.
    """
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT event_type, title, summary, polarity, importance,
                           source_type, source_url, publish_time
                    FROM research.company_event_memory
                    WHERE ts_code = :tc
                """),
                {"tc": ts_code},
            ).fetchall()
    except Exception:
        return raw_events

    if not rows:
        return raw_events

    # Build lookup: (source_url, publish_date) → extracted event
    by_url: dict[tuple[str, str], TimelineEvent] = {}
    by_title_date: dict[tuple[str, str], TimelineEvent] = {}
    for r in rows:
        et, title, summary, polarity, importance, src_type, url, pt = r
        # All Chinese disclosure ann_dates are Beijing-local (e.g. ann_date
        # "20260429" = Apr 29 BJT). Normalize the timestamp to BJT before
        # formatting so the comparison key matches the raw event's BJT date.
        if pt is not None:
            pt_bjt = to_bjt(pt) if pt.tzinfo else pt.replace(tzinfo=BJT)
            date_key = pt_bjt.strftime("%Y%m%d")
        else:
            date_key = ""
        ev = TimelineEvent(
            event_type=str(et),
            publish_time=date_key,
            title=str(title or ""),
            summary=str(summary or ""),
            source_url=str(url or ""),
            polarity=str(polarity) if polarity else None,
            importance=str(importance) if importance else None,
            is_extracted=True,
        )
        if url:
            by_url[(url, date_key)] = ev
        if title:
            by_title_date[(str(title)[:30], date_key)] = ev

    enriched: list[TimelineEvent] = []
    for raw in raw_events:
        # Try URL match first
        match = by_url.get((raw.source_url, raw.publish_time)) if raw.source_url else None
        # Fall back to title-date match (for items without URL like irm_qa)
        if match is None and raw.title:
            match = by_title_date.get((raw.title[:30], raw.publish_time))
        if match:
            enriched.append(match)
        else:
            enriched.append(raw)
    return enriched


# ─── Source extractors ────────────────────────────────────────────────────────

def _from_announcements(rows: list[dict]) -> list[TimelineEvent]:
    out = []
    for r in rows:
        dt = str(r.get("ann_date") or r.get("pub_date") or "")
        if not dt:
            continue
        title = str(r.get("title") or r.get("ann_title") or "公告")
        url = str(r.get("url") or r.get("ann_url") or "")
        out.append(TimelineEvent(
            event_type="announcement",
            publish_time=dt,
            title=title,
            source_url=url,
            raw=r,
        ))
    return out


def _from_forecasts(rows: list[dict]) -> list[TimelineEvent]:
    out = []
    for r in rows:
        dt = str(r.get("ann_date") or r.get("pub_date") or "")
        if not dt:
            continue
        period = str(r.get("end_date") or "")
        fc_type = str(r.get("type") or r.get("forecast_type") or "")
        content = str(r.get("summary") or r.get("change_reason") or "")
        title = f"业绩预告 {period} {fc_type}".strip()
        summary = content[:200] if content else ""
        out.append(TimelineEvent(
            event_type="forecast",
            publish_time=dt,
            title=title,
            summary=summary,
            raw=r,
        ))
    return out


def _from_expresses(rows: list[dict]) -> list[TimelineEvent]:
    out = []
    for r in rows:
        dt = str(r.get("ann_date") or r.get("pub_date") or "")
        if not dt:
            continue
        period = str(r.get("end_date") or "")
        title = f"业绩快报 {period}".strip()
        ni = r.get("n_income")
        summary = f"净利润: {ni}" if ni is not None else ""
        out.append(TimelineEvent(
            event_type="express",
            publish_time=dt,
            title=title,
            summary=summary,
            raw=r,
        ))
    return out


def _from_research_reports(rows: list[dict]) -> list[TimelineEvent]:
    out = []
    for r in rows:
        dt = str(r.get("report_date") or r.get("pub_date") or r.get("ann_date") or "")
        if not dt:
            continue
        title = str(r.get("title") or r.get("report_title") or "研究报告")
        org = str(r.get("org_name") or r.get("institution") or "")
        url = str(r.get("url") or "")
        summary = f"机构: {org}" if org else ""
        out.append(TimelineEvent(
            event_type="research",
            publish_time=dt,
            title=title,
            source_url=url,
            summary=summary,
            raw=r,
        ))
    return out


def _from_irm_qa(rows: list[dict]) -> list[TimelineEvent]:
    out = []
    for r in rows:
        # Tushare uses 'pub_time' / 'trade_date' for IRM dates; fall back to
        # other variants for compatibility.
        dt = str(r.get("pub_time") or r.get("trade_date")
                 or r.get("pub_date") or r.get("ask_date")
                 or r.get("ann_date") or "")
        if not dt:
            continue
        # Strip time component if present ('2026-04-29 10:30:00' → '20260429')
        dt = dt[:10].replace("-", "") if "-" in dt else dt[:8]
        question = str(r.get("q") or r.get("question") or r.get("ask_content") or "互动问答")
        reply = str(r.get("a") or r.get("reply") or r.get("answer")
                    or r.get("reply_content") or "")
        title = question[:80] + ("…" if len(question) > 80 else "")
        summary = reply[:200] if reply else "（未回复）"
        out.append(TimelineEvent(
            event_type="irm_qa",
            publish_time=dt,
            title=title,
            summary=summary,
            raw=r,
        ))
    return out


def _from_audit(rows: list[dict]) -> list[TimelineEvent]:
    out = []
    for r in rows:
        dt = str(r.get("ann_date") or r.get("pub_date") or "")
        if not dt:
            continue
        end_date = str(r.get("end_date") or "")
        result = str(r.get("audit_result") or "")
        agency = str(r.get("audit_agency") or r.get("audit_org") or "")
        title = f"审计意见 {end_date} {agency}".strip()
        summary = result[:200] if result else ""
        out.append(TimelineEvent(
            event_type="audit",
            publish_time=dt,
            title=title,
            summary=summary,
            raw=r,
        ))
    return out
