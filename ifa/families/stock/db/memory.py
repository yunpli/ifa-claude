"""Persistence helpers for the existing PostgreSQL `stock` schema.

The product is now named Stock Edge, but the repository already has a `stock`
schema from the Stock Intel design. Reuse it; do not create a parallel
`stock_edge` schema.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.stock.context import StockEdgeContext

DbAnalysisType = Literal["fast", "deep", "update", "morning_refresh", "intraday"]


def db_analysis_type(mode: str) -> DbAnalysisType:
    """Map product mode names to existing `stock.analysis_record` values."""
    if mode == "quick":
        return "fast"
    if mode in ("fast", "deep", "update", "morning_refresh", "intraday"):
        return mode  # type: ignore[return-value]
    raise ValueError(f"Unsupported Stock Edge analysis mode for DB: {mode!r}")


def create_analysis_record(
    engine: Engine,
    ctx: StockEdgeContext,
    *,
    record_id: uuid.UUID | None = None,
    status: str = "running",
    conclusion_label: str | None = None,
    conclusion_text: str | None = None,
) -> uuid.UUID:
    """Insert a `stock.analysis_record` row for one Stock Edge run."""
    rid = record_id or uuid.uuid4()
    payload = _metadata_payload(ctx)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO stock.analysis_record
                    (record_id, ts_code, analysis_type, triggered_at, data_cutoff,
                     status, conclusion_label, conclusion_text, validation_json)
                VALUES
                    (:record_id, :ts_code, :analysis_type, NOW(), :data_cutoff,
                     :status, :conclusion_label, :conclusion_text, CAST(:validation_json AS JSON))
            """),
            {
                "record_id": str(rid),
                "ts_code": ctx.request.ts_code,
                "analysis_type": db_analysis_type(ctx.request.mode),
                "data_cutoff": ctx.as_of.data_cutoff_at,
                "status": status,
                "conclusion_label": conclusion_label,
                "conclusion_text": conclusion_text,
                "validation_json": json.dumps(payload, ensure_ascii=False, default=str),
            },
        )
    return rid


def find_reusable_analysis(
    engine: Engine,
    *,
    ts_code: str,
    mode: str,
    data_cutoff_at: dt.datetime,
    param_hash: str | None = None,
) -> dict[str, Any] | None:
    """Find an existing succeeded/cached Stock record for the same cutoff.

    The existing schema does not have `param_hash` yet. Phase D can add stricter
    cache matching once a cache table or additive columns exist; for now this
    finds exact stock + mode + data cutoff reusable assets.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT record_id, ts_code, analysis_type, triggered_at, data_cutoff,
                       status, conclusion_label, conclusion_text, key_levels_json,
                       setup_match_json, validation_json, invalidation_json,
                       next_watch_json, forecast_json, output_html_path,
                       output_pdf_path, error_summary
                FROM stock.analysis_record
                WHERE ts_code = :ts_code
                  AND analysis_type = :analysis_type
                  AND data_cutoff = :data_cutoff
                  AND status IN ('succeeded', 'partial', 'cached')
                  AND output_html_path IS NOT NULL
                ORDER BY triggered_at DESC
                LIMIT 10
            """),
            {
                "ts_code": ts_code.upper(),
                "analysis_type": db_analysis_type(mode),
                "data_cutoff": data_cutoff_at,
            },
        ).mappings().all()
    for row in rows:
        payload = dict(row)
        if param_hash and _payload_param_hash(payload.get("validation_json")) != param_hash:
            continue
        return payload
    return None


def insert_report_section(
    engine: Engine,
    *,
    record_id: uuid.UUID | str,
    section_key: str,
    section_order: int,
    content: dict[str, Any],
    status: str = "ok",
    skip_reason: str | None = None,
    model_used: str | None = None,
    prompt_version: str | None = None,
    latency_seconds: float | None = None,
) -> uuid.UUID:
    """Upsert one `stock.report_sections` row."""
    sid = uuid.uuid4()
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO stock.report_sections
                    (section_id, record_id, section_key, section_order, content_json,
                     status, skip_reason, model_used, prompt_version, latency_seconds)
                VALUES
                    (:section_id, :record_id, :section_key, :section_order,
                     CAST(:content_json AS JSON), :status, :skip_reason,
                     :model_used, :prompt_version, :latency_seconds)
                ON CONFLICT (record_id, section_key) DO UPDATE SET
                    section_order = EXCLUDED.section_order,
                    content_json = EXCLUDED.content_json,
                    status = EXCLUDED.status,
                    skip_reason = EXCLUDED.skip_reason,
                    model_used = EXCLUDED.model_used,
                    prompt_version = EXCLUDED.prompt_version,
                    latency_seconds = EXCLUDED.latency_seconds
                RETURNING section_id
            """),
            {
                "section_id": str(sid),
                "record_id": str(record_id),
                "section_key": section_key,
                "section_order": section_order,
                "content_json": json.dumps(content, ensure_ascii=False, default=str),
                "status": status,
                "skip_reason": skip_reason,
                "model_used": model_used,
                "prompt_version": prompt_version,
                "latency_seconds": latency_seconds,
            },
        ).scalar_one()
    return row


def finalize_analysis_record(
    engine: Engine,
    *,
    record_id: uuid.UUID | str,
    status: str,
    output_html_path: Path | str | None = None,
    output_pdf_path: Path | str | None = None,
    conclusion_label: str | None = None,
    conclusion_text: str | None = None,
    key_levels: dict[str, Any] | None = None,
    setup_match: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    invalidation: dict[str, Any] | None = None,
    next_watch: dict[str, Any] | None = None,
    forecast: dict[str, Any] | None = None,
    error_summary: str | None = None,
) -> None:
    """Finalize an existing `stock.analysis_record` row."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE stock.analysis_record
                   SET status = :status,
                       conclusion_label = COALESCE(:conclusion_label, conclusion_label),
                       conclusion_text = COALESCE(:conclusion_text, conclusion_text),
                       key_levels_json = COALESCE(CAST(:key_levels AS JSON), key_levels_json),
                       setup_match_json = COALESCE(CAST(:setup_match AS JSON), setup_match_json),
                       validation_json = COALESCE(CAST(:validation AS JSON), validation_json),
                       invalidation_json = COALESCE(CAST(:invalidation AS JSON), invalidation_json),
                       next_watch_json = COALESCE(CAST(:next_watch AS JSON), next_watch_json),
                       forecast_json = COALESCE(CAST(:forecast AS JSON), forecast_json),
                       output_html_path = COALESCE(:html_path, output_html_path),
                       output_pdf_path = COALESCE(:pdf_path, output_pdf_path),
                       error_summary = :error_summary
                 WHERE record_id = :record_id
            """),
            {
                "record_id": str(record_id),
                "status": status,
                "conclusion_label": conclusion_label,
                "conclusion_text": conclusion_text,
                "key_levels": _json_or_none(key_levels),
                "setup_match": _json_or_none(setup_match),
                "validation": _json_or_none(validation),
                "invalidation": _json_or_none(invalidation),
                "next_watch": _json_or_none(next_watch),
                "forecast": _json_or_none(forecast),
                "html_path": str(output_html_path) if output_html_path else None,
                "pdf_path": str(output_pdf_path) if output_pdf_path else None,
                "error_summary": error_summary,
            },
        )


def _metadata_payload(ctx: StockEdgeContext) -> dict[str, Any]:
    return {
        "product": "stock_edge",
        "mode": ctx.request.mode,
        "run_mode": ctx.request.run_mode,
        "as_of_trade_date": ctx.as_of.as_of_trade_date,
        "data_cutoff_at": ctx.as_of.data_cutoff_at,
        "data_cutoff_at_bjt": ctx.as_of.data_cutoff_at_bjt,
        "as_of_rule": ctx.as_of.rule,
        "param_hash": ctx.param_hash,
        "model_versions": ctx.params.get("model", {}).get("versions", {}),
        "fresh": ctx.request.fresh,
        "has_base_position": ctx.request.has_base_position,
        "base_position_shares": ctx.request.base_position_shares,
        "runtime_tuning": ctx.params.get("_runtime_tuning"),
    }


def _json_or_none(payload: dict[str, Any] | None) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, default=str)


def _payload_param_hash(payload: Any) -> str | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    if isinstance(payload, dict):
        value = payload.get("param_hash")
        return str(value) if value else None
    return None
