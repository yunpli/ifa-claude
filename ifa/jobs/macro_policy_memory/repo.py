"""Persistence for macro_policy_event_memory."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .prompts import PROMPT_VERSION

ALLOWED_DIMENSIONS = {
    "稳增长", "新质生产力/科技自立", "消费与内需", "地产与信用",
    "资本市场", "金融监管/行业监管", "外部冲击", "货币/财政", "other",
}
ALLOWED_SIGNALS = {"升温", "平稳", "降温", "延续既有框架", "无新增信号"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_IMPORTANCE = {"high", "medium", "low"}


def _event_id(*, source_url: str, publish_time: str, title: str) -> str:
    """Stable event identifier across re-runs of the same source row."""
    h = hashlib.sha256()
    h.update((source_url or "").encode("utf-8"))
    h.update(b"|")
    h.update((publish_time or "").encode("utf-8"))
    h.update(b"|")
    h.update((title or "").encode("utf-8"))
    return f"evt_{h.hexdigest()[:24]}"


def upsert_policy_event(
    engine: Engine,
    *,
    candidate_meta: dict[str, Any],
    parsed_entry: dict[str, Any],
    extraction_model: str,
    extraction_endpoint: str,
) -> int:
    """Insert one policy event row. Returns 1 if inserted, 0 if skipped/dup."""
    if not parsed_entry.get("should_keep"):
        return 0

    dim = (parsed_entry.get("policy_dimension") or "other").strip()
    if dim not in ALLOWED_DIMENSIONS:
        dim = "other"
    signal = (parsed_entry.get("policy_signal") or "无新增信号").strip()
    if signal not in ALLOWED_SIGNALS:
        signal = "无新增信号"
    confidence = (parsed_entry.get("confidence") or "low").strip()
    if confidence not in ALLOWED_CONFIDENCE:
        confidence = "low"
    importance = (parsed_entry.get("importance") or "low").strip()
    if importance not in ALLOWED_IMPORTANCE:
        importance = "low"

    # Drop low-importance events from active memory (status='active' criterion)
    status = "active" if importance in {"high", "medium"} else "expired"

    pub_dt = _parse_dt(candidate_meta.get("publish_time"))
    event_date = pub_dt.date() if pub_dt else None
    carry_days = parsed_entry.get("carry_forward_days")
    try:
        carry_days = int(carry_days) if carry_days is not None else 0
    except (TypeError, ValueError):
        carry_days = 0
    carry_until = (event_date + dt.timedelta(days=carry_days)) if event_date and carry_days else None

    affected = parsed_entry.get("affected_areas") or []
    if not isinstance(affected, list):
        affected = [str(affected)]

    eid = _event_id(
        source_url=candidate_meta.get("url") or "",
        publish_time=candidate_meta.get("publish_time") or "",
        title=candidate_meta.get("title") or "",
    )

    sql = text("""
        INSERT INTO macro_policy_event_memory (
            event_id, event_date, event_window_start, event_window_end,
            policy_dimension, event_title, source_name, source_table, source_url,
            publish_time, summary, policy_signal, affected_areas, market_implication,
            carry_forward_until, confidence, status
        ) VALUES (
            :event_id, :event_date, :event_window_start, :event_window_end,
            :policy_dimension, :event_title, :source_name, :source_table, :source_url,
            :publish_time, :summary, :policy_signal, CAST(:affected_areas AS JSONB), :market_implication,
            :carry_forward_until, :confidence, :status
        )
        ON CONFLICT (event_id) DO NOTHING
    """)
    with engine.begin() as conn:
        res = conn.execute(sql, {
            "event_id": eid,
            "event_date": event_date,
            "event_window_start": pub_dt,
            "event_window_end": pub_dt,
            "policy_dimension": dim,
            "event_title": (candidate_meta.get("title") or "")[:300],
            "source_name": candidate_meta.get("source"),
            "source_table": _api_from_label(candidate_meta.get("source_label")),
            "source_url": candidate_meta.get("url"),
            "publish_time": pub_dt,
            "summary": (parsed_entry.get("summary") or "")[:500],
            "policy_signal": signal,
            "affected_areas": json.dumps(affected, ensure_ascii=False),
            "market_implication": (parsed_entry.get("market_implication") or "")[:500],
            "carry_forward_until": carry_until,
            "confidence": confidence,
            "status": status,
        })
    # extraction model / prompt version aren't columns here per current schema
    # (PRD §2.2 didn't list them); we keep status/dimension/signal as the stable provenance.
    _ = extraction_model, extraction_endpoint, PROMPT_VERSION
    return 1 if res.rowcount else 0


def _api_from_label(label: str | None) -> str | None:
    if not label:
        return None
    return label.split(".", 1)[0]


def _parse_dt(s: str | None) -> dt.datetime | None:
    """Parse ISO datetime. Naive strings are assumed Beijing local time
    (TuShare news APIs return Beijing wall-clock as naive datetimes)."""
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai"))
    return d
