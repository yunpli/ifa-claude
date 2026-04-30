"""ReportRun lifecycle: create row in report_runs, attach sections, finalize.

Each run is the immutable unit of work. Sections are persisted as we build
them so that a partial failure leaves a `partial`-status run rather than
losing all state.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.config import RunMode

from .timezones import utc_now


@dataclass
class ReportRun:
    report_run_id: uuid.UUID
    market: str
    report_family: str
    report_type: str
    report_date: dt.date
    slot: str
    timezone_name: str
    data_cutoff_at: dt.datetime
    run_mode: RunMode
    template_version: str
    prompt_version: str
    triggered_by: str | None = None
    started_at: dt.datetime = field(default_factory=utc_now)
    completed_at: dt.datetime | None = None
    fallback_used: bool = False


def insert_report_run(engine: Engine, run: ReportRun) -> None:
    sql = text("""
        INSERT INTO report_runs (
            report_run_id, market, report_family, report_type, report_date, slot,
            timezone, data_cutoff_at, status, run_mode, triggered_by,
            template_version, prompt_version, started_at
        ) VALUES (
            :rid, :mkt, :fam, :typ, :rd, :slt, :tz, :cut, 'running',
            :mode, :trg, :tver, :pver, :sat
        )
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "rid": str(run.report_run_id), "mkt": run.market, "fam": run.report_family,
            "typ": run.report_type, "rd": run.report_date, "slt": run.slot,
            "tz": run.timezone_name, "cut": run.data_cutoff_at, "mode": run.run_mode.value,
            "trg": run.triggered_by, "tver": run.template_version,
            "pver": run.prompt_version, "sat": run.started_at,
        })


def insert_section(engine: Engine, *, report_run_id: uuid.UUID,
                   section_key: str, section_title: str, section_order: int,
                   content_json: dict[str, Any], prompt_name: str | None = None,
                   prompt_version: str | None = None,
                   model_output_id: uuid.UUID | None = None,
                   fallback_used: bool = False) -> uuid.UUID:
    import json
    section_id = uuid.uuid4()
    sql = text("""
        INSERT INTO report_sections (
            section_id, report_run_id, section_key, section_title, section_order,
            content_json, prompt_name, prompt_version, model_output_id, fallback_used
        ) VALUES (
            :sid, :rid, :key, :ttl, :ord, CAST(:cj AS JSONB),
            :pn, :pv, :moid, :fb
        )
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "sid": str(section_id), "rid": str(report_run_id),
            "key": section_key, "ttl": section_title, "ord": section_order,
            "cj": json.dumps(content_json, ensure_ascii=False, default=str),
            "pn": prompt_name, "pv": prompt_version,
            "moid": str(model_output_id) if model_output_id else None,
            "fb": fallback_used,
        })
    return section_id


def insert_model_output(engine: Engine, *, report_run_id: uuid.UUID, section_key: str,
                        prompt_name: str, prompt_version: str, model_name: str,
                        endpoint: str, parsed_json: dict[str, Any] | None,
                        status: str, prompt_tokens: int | None,
                        completion_tokens: int | None, latency_seconds: float) -> uuid.UUID:
    import json
    moid = uuid.uuid4()
    sql = text("""
        INSERT INTO report_model_outputs (
            model_output_id, report_run_id, section_key, prompt_name, prompt_version,
            model_name, endpoint, parsed_json, status,
            prompt_tokens, completion_tokens, latency_seconds
        ) VALUES (
            :moid, :rid, :key, :pn, :pv, :mn, :ep, CAST(:pj AS JSONB), :st,
            :pt, :ct, :ls
        )
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "moid": str(moid), "rid": str(report_run_id), "key": section_key,
            "pn": prompt_name, "pv": prompt_version, "mn": model_name, "ep": endpoint,
            "pj": json.dumps(parsed_json, ensure_ascii=False, default=str) if parsed_json else None,
            "st": status, "pt": prompt_tokens, "ct": completion_tokens, "ls": latency_seconds,
        })
    return moid


def insert_judgment(engine: Engine, *, report_run_id: uuid.UUID, section_key: str,
                    judgment_type: str, judgment_text: str, target: str | None,
                    horizon: str, confidence: str,
                    validation_method: str | None = None) -> uuid.UUID:
    jid = uuid.uuid4()
    sql = text("""
        INSERT INTO report_judgments (
            judgment_id, report_run_id, section_key, judgment_type, judgment_text,
            target, horizon, confidence, validation_method
        ) VALUES (
            :jid, :rid, :key, :jt, :txt, :tgt, :hz, :cf, :vm
        )
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "jid": str(jid), "rid": str(report_run_id), "key": section_key,
            "jt": judgment_type, "txt": judgment_text, "tgt": target,
            "hz": horizon, "cf": confidence, "vm": validation_method,
        })
    return jid


def finalize_report_run(engine: Engine, run: ReportRun, *, status: str,
                        output_html_path: Path | None = None,
                        output_json_path: Path | None = None,
                        error_summary: str | None = None) -> None:
    completed = utc_now()
    duration = (completed - run.started_at).total_seconds()
    sql = text("""
        UPDATE report_runs
           SET status = :st, completed_at = :cat, duration_seconds = :ds,
               output_html_path = :hp, output_json_path = :jp,
               fallback_used = :fb, error_summary = :err
         WHERE report_run_id = :rid
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "st": status, "cat": completed, "ds": duration,
            "hp": str(output_html_path) if output_html_path else None,
            "jp": str(output_json_path) if output_json_path else None,
            "fb": run.fallback_used, "err": error_summary,
            "rid": str(run.report_run_id),
        })
