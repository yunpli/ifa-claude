"""Shared utilities for Stock Edge diagnostic perspective adapters."""
from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.stock.diagnostic.models import EvidencePoint, PerspectiveEvidence


def query_dicts(engine: Engine, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(text(sql), params).mappings().all()]
    except Exception:
        return []


def timed(key: str, fn: Callable[[], PerspectiveEvidence]) -> PerspectiveEvidence:
    started = time.perf_counter()
    try:
        evidence = fn()
        return with_contract_fields(evidence, latency_ms=(time.perf_counter() - started) * 1000.0)
    except Exception as exc:  # noqa: BLE001 - diagnostics degrade per perspective
        return PerspectiveEvidence(
            key,
            key,
            "error",
            "unknown",
            f"{key} collector failed: {type(exc).__name__}: {exc}",
            latency_ms=(time.perf_counter() - started) * 1000.0,
            missing_required=[key],
        )


def freshness_from_points(points: list[EvidencePoint]) -> dict[str, Any]:
    dated = [str(point.as_of) for point in points if point.as_of]
    return {
        "latest_as_of": max(dated) if dated else None,
        "source_count": len({point.source for point in points if point.source}),
        "evidence_count": len(points),
    }


def with_contract_fields(p: PerspectiveEvidence, *, latency_ms: float | None) -> PerspectiveEvidence:
    sources = p.source_tables or sorted({point.source for point in p.points if point.source})
    missing_required = p.missing_required or (list(p.missing) if p.status in {"unavailable", "error"} else [])
    return PerspectiveEvidence(
        p.key,
        p.title,
        p.status,
        p.view,
        p.summary,
        points=p.points,
        conflicts=p.conflicts,
        missing=p.missing,
        freshness=p.freshness,
        raw=p.raw,
        latency_ms=latency_ms,
        source_tables=sources,
        missing_required=missing_required,
    )


def parse_dateish(value: Any) -> dt.date | None:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    if value is None:
        return None
    text_value = str(value)
    if not text_value or text_value == "None":
        return None
    try:
        return dt.date.fromisoformat(text_value[:10])
    except ValueError:
        return None


def days_between(start: Any, end: dt.date) -> int | None:
    start_date = parse_dateish(start)
    if start_date is None:
        return None
    return (end - start_date).days


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
