"""Best-effort DB persistence for Stock Edge diagnostic artifacts."""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .models import DiagnosticReport
from .service import diagnostic_manifest_payload


def persist_diagnostic_run(
    report: DiagnosticReport,
    *,
    engine: Engine,
    output_paths: dict[str, str],
    requested_at: dt.datetime | None = None,
) -> dict[str, Any]:
    """Persist one diagnostic run and per-perspective evidence rows.

    This is intentionally best-effort at the CLI boundary.  The function itself
    raises on DB/schema errors so callers can record a fallback status without
    hiding an unexpected migration gap in tests or smoke runs.
    """
    run_id = str(uuid.uuid4())
    payload = diagnostic_manifest_payload(report, output_paths=output_paths, requested_at=requested_at)
    perspective_statuses = payload["perspective_statuses"]
    generated_at = _parse_datetime(report.generated_at_bjt)
    requested_value = requested_at if requested_at is not None else generated_at

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO stock.diagnostic_runs (
                    run_id, ts_code, name, requested_at, generated_at, as_of_trade_date,
                    run_mode, status, conclusion, confidence, logic_version,
                    output_paths_json, perspective_status_json, evidence_freshness_json,
                    synthesis_json, manifest_json
                )
                VALUES (
                    :run_id, :ts_code, :name, :requested_at, :generated_at, :as_of_trade_date,
                    :run_mode, :status, :conclusion, :confidence, :logic_version,
                    CAST(:output_paths_json AS jsonb), CAST(:perspective_status_json AS jsonb),
                    CAST(:evidence_freshness_json AS jsonb), CAST(:synthesis_json AS jsonb),
                    CAST(:manifest_json AS jsonb)
                )
            """),
            {
                "run_id": run_id,
                "ts_code": report.ts_code,
                "name": report.name,
                "requested_at": requested_value,
                "generated_at": generated_at,
                "as_of_trade_date": report.as_of_trade_date,
                "run_mode": report.audit.get("run_mode") or "manual",
                "status": "succeeded",
                "conclusion": report.synthesis.conclusion,
                "confidence": report.synthesis.confidence,
                "logic_version": report.synthesis.logic_version,
                "output_paths_json": _json_dumps(output_paths),
                "perspective_status_json": _json_dumps(perspective_statuses),
                "evidence_freshness_json": _json_dumps(payload["evidence_freshness"]),
                "synthesis_json": _json_dumps(asdict(report.synthesis)),
                "manifest_json": _json_dumps(payload),
            },
        )
        for perspective in report.perspectives:
            source_as_of = _parse_dateish(perspective.freshness.get("latest_as_of"))
            conn.execute(
                text("""
                    INSERT INTO stock.diagnostic_perspective_evidence (
                        run_id, perspective_key, title, status, view, freshness_status,
                        latency_ms, source_tables_json, missing_evidence_json,
                        missing_required_json, source_as_of, summary, evidence_json, raw_json
                    )
                    VALUES (
                        :run_id, :perspective_key, :title, :status, :view, :freshness_status,
                        :latency_ms, CAST(:source_tables_json AS jsonb), CAST(:missing_evidence_json AS jsonb),
                        CAST(:missing_required_json AS jsonb), :source_as_of, :summary,
                        CAST(:evidence_json AS jsonb), CAST(:raw_json AS jsonb)
                    )
                """),
                {
                    "run_id": run_id,
                    "perspective_key": perspective.key,
                    "title": perspective.title,
                    "status": perspective.status,
                    "view": perspective.view,
                    "freshness_status": perspective.freshness_status,
                    "latency_ms": perspective.latency_ms,
                    "source_tables_json": _json_dumps(perspective.source_tables),
                    "missing_evidence_json": _json_dumps(perspective.missing),
                    "missing_required_json": _json_dumps(perspective.missing_required),
                    "source_as_of": source_as_of,
                    "summary": perspective.summary,
                    "evidence_json": _json_dumps([asdict(point) for point in perspective.points]),
                    "raw_json": _json_dumps(perspective.raw),
                },
            )
    return {"status": "persisted", "run_id": run_id}


def try_persist_diagnostic_run(
    report: DiagnosticReport,
    *,
    engine: Engine,
    output_paths: dict[str, str],
    requested_at: dt.datetime | None = None,
) -> dict[str, Any]:
    try:
        return persist_diagnostic_run(report, engine=engine, output_paths=output_paths, requested_at=requested_at)
    except Exception as exc:  # noqa: BLE001 - CLI must keep JSON/HTML generation available.
        return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


def _parse_datetime(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def _parse_dateish(value: Any) -> dt.date | None:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
