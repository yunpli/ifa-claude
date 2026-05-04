"""Persistent Research memory APIs.

Research reports are composed from durable Postgres state, not from one-off
HTML rendering side effects. This module is the public boundary for other
families (Stock Intel / TA / SmartMoney) that need fundamental lineup inputs.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upsert_period_factor_decomposition(
    engine: Engine,
    *,
    ts_code: str,
    factor_family: str,
    factor_name: str,
    periods: list[str],
    values: list[float | None],
    payload: dict,
    source_hash: str,
    source: str = "financial_statement_history",
    unit: str | None = None,
) -> int:
    """Persist one derived factor series, one row per reporting period."""
    if not periods or not values:
        return 0
    sql = text("""
        INSERT INTO research.period_factor_decomposition
            (ts_code, factor_family, factor_name, period, period_type,
             value, unit, source, source_hash, payload_json, computed_at)
        VALUES
            (:ts_code, :family, :factor_name, :period, :period_type,
             :value, :unit, :source, :source_hash, CAST(:payload AS JSONB), NOW())
        ON CONFLICT (ts_code, factor_family, factor_name, period) DO UPDATE SET
            value = EXCLUDED.value,
            unit = EXCLUDED.unit,
            source = EXCLUDED.source,
            source_hash = EXCLUDED.source_hash,
            payload_json = EXCLUDED.payload_json,
            computed_at = EXCLUDED.computed_at
    """)
    n = 0
    with engine.begin() as conn:
        for period, value in zip(periods, values):
            conn.execute(sql, {
                "ts_code": ts_code,
                "family": factor_family,
                "factor_name": factor_name,
                "period": str(period),
                "period_type": "annual" if str(period).endswith("1231") else "quarterly",
                "value": value,
                "unit": unit,
                "source": source,
                "source_hash": source_hash,
                "payload": json.dumps(payload, default=str),
            })
            n += 1
    return n


def load_period_factor_decomposition(
    engine: Engine,
    ts_code: str,
    *,
    period_type: str | None = None,
    factor_family: str | None = None,
    limit_periods: int | None = None,
) -> list[dict]:
    """Load persisted period factors for composition by reports/other families."""
    params: dict[str, Any] = {"ts_code": ts_code}
    where = ["ts_code = :ts_code"]
    if period_type:
        where.append("period_type = :period_type")
        params["period_type"] = period_type
    if factor_family:
        where.append("factor_family = :factor_family")
        params["factor_family"] = factor_family
    limit_sql = "LIMIT :limit" if limit_periods else ""
    if limit_periods:
        params["limit"] = limit_periods
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT ts_code, factor_family, factor_name, period, period_type,
                   value, unit, source, source_hash, payload_json, computed_at
            FROM research.period_factor_decomposition
            WHERE {' AND '.join(where)}
            ORDER BY period DESC, factor_family, factor_name
            {limit_sql}
        """), params).mappings().all()
    return [dict(r) for r in rows]


def load_fundamental_lineup(
    engine: Engine,
    ts_code: str,
    *,
    annual_years: int = 3,
    quarterly_periods: int = 12,
) -> dict:
    """Return a compact fundamental lineup for Stock Intel / TA composition."""
    annual = load_period_factor_decomposition(
        engine, ts_code, period_type="annual", limit_periods=annual_years * 5,
    )
    quarterly = load_period_factor_decomposition(
        engine, ts_code, period_type="quarterly", limit_periods=quarterly_periods * 5,
    )
    pdfs = load_pdf_extracts(engine, ts_code, limit=5)
    return {
        "ts_code": ts_code,
        "annual_factors": annual,
        "quarterly_factors": quarterly,
        "recent_research_reports": pdfs,
    }


def find_reusable_report(
    engine: Engine,
    *,
    ts_code: str,
    analysis_type: str,
    tier: str,
    latest_period: str | None = None,
    run_mode: str | None = None,
) -> dict | None:
    """Return the latest already-generated report asset for this filing lens.

    The report itself is a durable product asset. Financial statements are
    sparse (annual / quarterly), so an existing succeeded run for the same
    stock, statement lens, tier, and latest filing period should be reused by
    callers unless they explicitly request a fresh run.
    """
    where = [
        "ts_code = :ts_code",
        "report_type = :tier",
        "status IN ('succeeded', 'cached')",
        "scope_json ->> 'analysis_type' = :analysis_type",
        "output_html_path IS NOT NULL",
    ]
    params: dict[str, Any] = {
        "ts_code": ts_code,
        "tier": tier,
        "analysis_type": analysis_type,
    }
    if latest_period:
        where.append("scope_json ->> 'latest_period' = :latest_period")
        params["latest_period"] = latest_period
    if run_mode:
        where.append("run_mode = :run_mode")
        params["run_mode"] = run_mode
    with engine.connect() as conn:
        row = conn.execute(text(f"""
            SELECT run_id, ts_code, company_name, report_type, scope_json,
                   status, run_mode, started_at, completed_at,
                   output_html_path, output_pdf_path, output_json_path
            FROM research.report_runs
            WHERE {' AND '.join(where)}
            ORDER BY completed_at DESC NULLS LAST, started_at DESC
            LIMIT 1
        """), params).mappings().fetchone()
    return dict(row) if row else None


def record_report_asset(
    engine: Engine,
    *,
    report: dict,
    html_path: str,
    md_path: str | None = None,
    pdf_path: str | None = None,
    status: str = "succeeded",
    triggered_by: str = "manual",
) -> str:
    """Persist a generated Research report and its sections in Postgres."""
    scope_json = {
        "analysis_type": report.get("analysis_type"),
        "tier": report.get("tier"),
        "latest_period": _report_latest_period(report),
        "data_cutoff_bjt": report.get("data_cutoff_bjt"),
        "md_path": md_path,
    }
    with engine.begin() as conn:
        run_id = conn.execute(text("""
            INSERT INTO research.report_runs
                (ts_code, company_name, report_type, scope_json, status,
                 triggered_by, template_version, run_mode, started_at, completed_at,
                 output_html_path, output_pdf_path, output_json_path)
            VALUES
                (:ts_code, :company_name, :report_type, CAST(:scope_json AS JSON),
                 :status, :triggered_by, :template_version, :run_mode,
                 NOW(), NOW(), :html_path, :pdf_path, NULL)
            RETURNING run_id
        """), {
            "ts_code": report.get("ts_code"),
            "company_name": report.get("company_name"),
            "report_type": report.get("tier"),
            "scope_json": json.dumps(scope_json, default=str),
            "status": status,
            "triggered_by": triggered_by,
            "template_version": report.get("template_version"),
            "run_mode": report.get("run_mode"),
            "html_path": html_path,
            "pdf_path": pdf_path,
        }).scalar_one()

        for idx, section in enumerate(report.get("sections") or [], start=1):
            section_type = str(section.get("type") or f"section_{idx:02d}")
            conn.execute(text("""
                INSERT INTO research.report_sections
                    (run_id, section_key, section_order, content_json, status)
                VALUES
                    (:run_id, :section_key, :section_order, CAST(:content_json AS JSON), 'ok')
                ON CONFLICT (run_id, section_key) DO UPDATE SET
                    section_order = EXCLUDED.section_order,
                    content_json = EXCLUDED.content_json,
                    status = EXCLUDED.status
            """), {
                "run_id": run_id,
                "section_key": f"{idx:02d}_{section_type}",
                "section_order": idx,
                "content_json": json.dumps(section, default=str),
            })
    return str(run_id)


def _report_latest_period(report: dict) -> str | None:
    for section in report.get("sections") or []:
        if section.get("type") == "research_period_analysis":
            latest = section.get("latest") or {}
            if latest.get("raw_period"):
                return latest.get("raw_period")
            rows = section.get("rows") or []
            if rows and rows[-1].get("raw_period"):
                return rows[-1].get("raw_period")
    for section in report.get("sections") or []:
        if section.get("type") == "research_overview":
            return section.get("latest_period")
    return None


def load_pdf_extract(engine: Engine, source_url: str) -> dict | None:
    url_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT ts_code, source_url, title, source_date, page_count,
                   extractable, text_hash, extract_json, extracted_at
            FROM research.pdf_extract_cache
            WHERE url_hash = :url_hash
        """), {"url_hash": url_hash}).mappings().fetchone()
    return dict(row) if row else None


def upsert_pdf_extract(
    engine: Engine,
    *,
    ts_code: str,
    source_url: str,
    title: str | None,
    source_date: str | None,
    page_count: int,
    extractable: bool,
    text_hash: str | None,
    extract_json: dict,
) -> None:
    url_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO research.pdf_extract_cache
                (url_hash, ts_code, source_url, title, source_date,
                 page_count, extractable, text_hash, extract_json, extracted_at)
            VALUES
                (:url_hash, :ts_code, :source_url, :title, :source_date,
                 :page_count, :extractable, :text_hash, CAST(:extract_json AS JSONB), NOW())
            ON CONFLICT (url_hash) DO UPDATE SET
                ts_code = EXCLUDED.ts_code,
                title = EXCLUDED.title,
                source_date = EXCLUDED.source_date,
                page_count = EXCLUDED.page_count,
                extractable = EXCLUDED.extractable,
                text_hash = EXCLUDED.text_hash,
                extract_json = EXCLUDED.extract_json,
                extracted_at = EXCLUDED.extracted_at
        """), {
            "url_hash": url_hash,
            "ts_code": ts_code,
            "source_url": source_url,
            "title": title,
            "source_date": source_date,
            "page_count": page_count,
            "extractable": extractable,
            "text_hash": text_hash,
            "extract_json": json.dumps(extract_json, default=str),
        })


def load_pdf_extracts(engine: Engine, ts_code: str, *, limit: int = 5) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ts_code, source_url, title, source_date, page_count,
                   extractable, text_hash, extract_json, extracted_at
            FROM research.pdf_extract_cache
            WHERE ts_code = :ts_code
            ORDER BY source_date DESC NULLS LAST, extracted_at DESC
            LIMIT :limit
        """), {"ts_code": ts_code, "limit": limit}).mappings().all()
    return [dict(r) for r in rows]
