"""Company event extraction job — populates research.company_event_memory.

Pulls recent disclosure/research/QA items from a CompanyFinancialSnapshot,
sends each through an LLM to extract structured event metadata, and upserts
into `research.company_event_memory` with a stable event_id so re-runs are
idempotent.

Why a separate job:
  · Event extraction is an LLM cost we want to amortize across many reports
    (each company gets reports run multiple times; events should only be
    extracted once per disclosure).
  · The output table is consumed by Research §07/§09/§12 (when those
    sections are added) and by future cross-stock event analytics.

Design choices:
  · event_id = sha256(ts_code + source_type + source_url + publish_time)[:32]
    — same announcement always maps to same event_id; ON CONFLICT DO NOTHING.
  · Strict JSON Schema; failures are logged but don't break the batch.
  · Items already in the table within max_age_days are skipped to keep cost
    bounded on re-runs.
  · Per-item LLM call rather than batch. Slower but isolates failures and
    keeps prompts narrow (better extraction quality).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.llm.client import LLMClient
from ifa.core.report.timezones import bjt_now
from ifa.families.research.analyzer.data import CompanyFinancialSnapshot

log = logging.getLogger(__name__)

_EXTRACTION_PROMPT_VERSION = "v1"
_DEFAULT_MAX_PER_SOURCE = 20

_SYSTEM_PROMPT = (
    "你是一个公司事件结构化抽取器。给定一条公开披露/研报/互动易问答的标题和片段，"
    "提取一个结构化事件。严格规则：\n"
    "1. 输出严格的 JSON 对象：{\"event_type\": str, \"title\": str, \"summary\": str, "
    "\"polarity\": \"positive|negative|neutral\", \"importance\": \"high|medium|low\"}.\n"
    "2. event_type 限定值（最多一个）：\n"
    "   - earnings_beat / earnings_miss / earnings_inline (业绩超预期/不及/符合)\n"
    "   - guidance (业绩指引/预告)\n"
    "   - management_change (管理层变动)\n"
    "   - shareholding_change (股东增持/减持/质押)\n"
    "   - merger_or_acquisition (并购/重组)\n"
    "   - investment_or_capex (投资/扩产/募资)\n"
    "   - regulatory (监管/合规/受罚)\n"
    "   - product_or_contract (产品/合同/客户)\n"
    "   - dividend (分红/回购)\n"
    "   - misc (其他)\n"
    "3. title ≤30字简短中文标题。summary 50-150 字描述事件本质，不复述源标题。\n"
    "4. polarity：对公司基本面的可能影响方向（中性事件用 neutral）。\n"
    "5. importance：股价/基本面影响的相对重要性。\n"
    "6. 严禁输出 JSON 之外的内容（无代码块、无解释文字）。\n"
    "7. 不下买卖建议，不引用未给出的财务数字。"
)


@dataclass
class _Candidate:
    source_type: str       # 'announcement' | 'research_report' | 'irm_qa'
    source_url: str
    publish_time: datetime
    raw_title: str
    raw_text: str          # short context fed to the LLM


@dataclass
class ExtractionReport:
    ts_code: str
    candidates_total: int = 0
    skipped_existing: int = 0
    extracted: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.ts_code}: candidates={self.candidates_total} "
            f"skipped={self.skipped_existing} extracted={self.extracted} "
            f"failed={self.failed}"
        )


def extract_events_for_company(
    engine: Engine,
    snap: CompanyFinancialSnapshot,
    *,
    client: LLMClient | None = None,
    max_per_source: int = _DEFAULT_MAX_PER_SOURCE,
    max_age_days: int = 365,
) -> ExtractionReport:
    """Extract structured events from snapshot's disclosure/research/QA lists.

    Args:
        engine: SQLAlchemy engine for research.company_event_memory.
        snap: CompanyFinancialSnapshot already loaded with disclosure data.
        client: LLM client; created if None.
        max_per_source: cap items per source per run.
        max_age_days: ignore items older than this (unbounded would explode cost).
    """
    client = client or LLMClient()
    ts_code = snap.company.ts_code
    report = ExtractionReport(ts_code=ts_code)

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
    candidates: list[_Candidate] = []

    # Announcements
    candidates.extend(_announcements_to_candidates(snap.announcements, cutoff)[:max_per_source])
    # Research reports
    candidates.extend(_research_reports_to_candidates(snap.research_reports, cutoff)[:max_per_source])
    # IRM Q&A
    candidates.extend(_irm_to_candidates(snap.irm_qa, cutoff)[:max_per_source])

    report.candidates_total = len(candidates)
    if not candidates:
        return report

    # Filter out already-extracted (idempotent re-runs)
    existing_ids = _existing_event_ids(engine, ts_code)
    pending: list[tuple[str, _Candidate]] = []
    for cand in candidates:
        eid = _event_id(ts_code, cand.source_type, cand.source_url, cand.publish_time)
        if eid in existing_ids:
            report.skipped_existing += 1
            continue
        pending.append((eid, cand))

    if not pending:
        return report

    log.info("extracting %d new events for %s (skipped %d existing)",
             len(pending), ts_code, report.skipped_existing)

    rows: list[dict] = []
    for eid, cand in pending:
        try:
            extracted = _extract_one(client, cand)
            if extracted is None:
                report.failed += 1
                report.failures.append((eid[:12], "extraction returned None"))
                continue
            rows.append(_row_for_persist(eid, ts_code, cand, extracted, client))
            report.extracted += 1
        except Exception as e:
            log.warning("extraction failed for %s/%s: %s", ts_code, eid[:12], e)
            report.failed += 1
            report.failures.append((eid[:12], str(e)[:200]))

    if rows:
        _persist(engine, rows)

    return report


# ─── Source → Candidate adapters ──────────────────────────────────────────────

def _announcements_to_candidates(
    rows: list[dict], cutoff: datetime,
) -> list[_Candidate]:
    out = []
    for r in rows:
        pt = _parse_anndate(r.get("ann_date"))
        if pt is None or pt < cutoff:
            continue
        title = str(r.get("title") or r.get("ann_title") or "").strip()
        if not title:
            continue
        url = str(r.get("url") or r.get("ann_url") or "")
        out.append(_Candidate(
            source_type="announcement",
            source_url=url,
            publish_time=pt,
            raw_title=title,
            raw_text=title,  # announcements only give us the title
        ))
    return out


def _research_reports_to_candidates(
    rows: list[dict], cutoff: datetime,
) -> list[_Candidate]:
    out = []
    for r in rows:
        pt = _parse_anndate(r.get("report_date") or r.get("pub_date") or r.get("ann_date"))
        if pt is None or pt < cutoff:
            continue
        title = str(r.get("title") or r.get("report_title") or "").strip()
        if not title:
            continue
        org = str(r.get("org_name") or r.get("institution") or "")
        rating = str(r.get("rating") or "")
        text_blob = f"{title}\n机构: {org}; 评级: {rating}".strip()
        out.append(_Candidate(
            source_type="research_report",
            source_url=str(r.get("url") or ""),
            publish_time=pt,
            raw_title=title,
            raw_text=text_blob[:600],
        ))
    return out


def _irm_to_candidates(
    rows: list[dict], cutoff: datetime,
) -> list[_Candidate]:
    out = []
    for r in rows:
        pt = _parse_anndate(r.get("pub_date") or r.get("ask_date") or r.get("ann_date"))
        if pt is None or pt < cutoff:
            continue
        question = str(r.get("question") or r.get("ask_content") or "").strip()
        reply = str(r.get("reply") or r.get("answer") or r.get("reply_content") or "").strip()
        if not question:
            continue
        text_blob = f"问: {question}\n答: {reply or '（未回复）'}"
        out.append(_Candidate(
            source_type="irm_qa",
            source_url="",
            publish_time=pt,
            raw_title=question[:60],
            raw_text=text_blob[:800],
        ))
    return out


# ─── LLM extraction ──────────────────────────────────────────────────────────

def _extract_one(client: LLMClient, cand: _Candidate) -> dict[str, Any] | None:
    user_msg = (
        f"source_type: {cand.source_type}\n"
        f"publish_time: {cand.publish_time.isoformat()}\n"
        f"---\n"
        f"{cand.raw_text}"
    )
    resp = client.chat(
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=400,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    parsed = resp.parse_json()
    if not isinstance(parsed, dict):
        return None
    required = {"event_type", "title", "summary", "polarity", "importance"}
    if not required <= parsed.keys():
        return None
    if parsed.get("polarity") not in ("positive", "negative", "neutral"):
        return None
    if parsed.get("importance") not in ("high", "medium", "low"):
        return None
    parsed["_model"] = resp.model
    return parsed


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _event_id(ts_code: str, source_type: str, source_url: str,
              publish_time: datetime) -> str:
    """Stable hash; same disclosure → same event_id."""
    h = hashlib.sha256()
    h.update(ts_code.encode())
    h.update(b"|")
    h.update(source_type.encode())
    h.update(b"|")
    h.update((source_url or "").encode())
    h.update(b"|")
    h.update(publish_time.isoformat().encode())
    return h.hexdigest()[:32]


def _parse_anndate(raw: Any) -> datetime | None:
    """Parse Tushare ann_date / pub_date. Source dates are Beijing local
    (Chinese exchange announcements), so we tag with BJT not UTC. Storage
    column is timestamptz so the tz-info round-trips correctly."""
    from ifa.core.report.timezones import BJT

    if raw is None:
        return None
    s = str(raw).strip()
    if len(s) == 8 and s.isdigit():
        try:
            return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), tzinfo=BJT)
        except ValueError:
            return None
    try:
        if "T" in s:
            # Caller-provided ISO format may already carry tz; if naive, assume BJT.
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=BJT)
            return parsed
        parts = s.split("-")
        if len(parts) >= 3:
            return datetime(int(parts[0]), int(parts[1]), int(parts[2][:2]),
                            tzinfo=BJT)
    except (ValueError, IndexError):
        pass
    return None


def _existing_event_ids(engine: Engine, ts_code: str) -> set[str]:
    sql = text("""
        SELECT event_id FROM research.company_event_memory
        WHERE ts_code = :tc
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"tc": ts_code}).fetchall()
    return {r[0] for r in rows}


def _row_for_persist(
    event_id: str, ts_code: str, cand: _Candidate,
    extracted: dict, client: LLMClient,
) -> dict:
    today = bjt_now().date()  # capture_date / valid_until in Beijing time
    return {
        "event_id": event_id,
        "ts_code": ts_code,
        "capture_date": today,
        "event_type": str(extracted.get("event_type") or "misc")[:64],
        "title": str(extracted.get("title") or "")[:120],
        "summary": str(extracted.get("summary") or "")[:1000],
        "polarity": str(extracted.get("polarity") or "neutral"),
        "importance": str(extracted.get("importance") or "low"),
        "source_type": cand.source_type,
        "source_url": cand.source_url[:500] if cand.source_url else "",
        "publish_time": cand.publish_time,
        "extraction_model": str(extracted.get("_model") or ""),
        "extraction_prompt_version": _EXTRACTION_PROMPT_VERSION,
        "valid_until": today + timedelta(days=365),
    }


def _persist(engine: Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO research.company_event_memory (
                    event_id, ts_code, capture_date, event_type, title,
                    summary, polarity, importance, source_type, source_url,
                    publish_time, extraction_model, extraction_prompt_version,
                    valid_until
                ) VALUES (
                    :event_id, :ts_code, :capture_date, :event_type, :title,
                    :summary, :polarity, :importance, :source_type, :source_url,
                    :publish_time, :extraction_model, :extraction_prompt_version,
                    :valid_until
                )
                ON CONFLICT (event_id) DO NOTHING
            """),
            rows,
        )
