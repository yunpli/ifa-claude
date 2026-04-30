"""Persistence layer for macro_text_derived_indicators."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .prompts import PROMPT_VERSION

# Indicator names we accept; LLM output naming anything else (e.g. M2, 社融) is
# rejected — those go through structured TuShare endpoints, not text extraction.
ALLOWED_INDICATORS = {"new_rmb_loans", "rmb_loan_balance"}


def _to_decimal(s: str | None) -> float | None:
    if s is None or s == "" or s == "null":
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def upsert_indicator_rows(
    engine: Engine,
    *,
    candidate_meta: dict[str, Any],         # title/source/publish_time/url/source_label
    parsed_entry: dict[str, Any],           # one element of `results[]`
    extraction_model: str,
    extraction_endpoint: str,
) -> int:
    """Insert rows for a single LLM-extracted candidate. Returns row count inserted."""
    indicators = parsed_entry.get("indicators") or []
    if not parsed_entry.get("has_extractable_data") or not indicators:
        return 0

    release_type = (parsed_entry.get("release_type") or "unknown").strip()
    publisher = parsed_entry.get("publisher_or_origin")
    reported_period = parsed_entry.get("reported_period")
    confidence = (parsed_entry.get("confidence") or "low").strip()

    sql = text("""
        INSERT INTO macro_text_derived_indicators (
            indicator_name, reported_period, value, unit, yoy, mom,
            release_type, publisher_or_origin,
            source_table, source_name, source_title, source_url, source_publish_time,
            evidence_sentence, extraction_model, extraction_prompt_version,
            confidence, status
        ) VALUES (
            :indicator_name, :reported_period, :value, :unit, :yoy, :mom,
            :release_type, :publisher_or_origin,
            :source_table, :source_name, :source_title, :source_url, :source_publish_time,
            :evidence_sentence, :extraction_model, :extraction_prompt_version,
            :confidence, 'extracted'
        )
        ON CONFLICT (source_url, indicator_name, reported_period) DO NOTHING
    """)

    inserted = 0
    with engine.begin() as conn:
        for ind in indicators:
            name = (ind.get("indicator_name") or "").strip()
            if name not in ALLOWED_INDICATORS:
                # LLM produced M2/社融/CPI/etc. — silently skip; the structured
                # TuShare source is authoritative for those.
                continue
            params = {
                "indicator_name": name,
                "reported_period": reported_period,
                "value": _to_decimal(ind.get("value")),
                "unit": ind.get("unit"),
                "yoy": _to_decimal(ind.get("yoy")),
                "mom": _to_decimal(ind.get("mom")),
                "release_type": release_type if release_type in {
                    "official_release", "media_report_citing_official_data",
                    "forecast_or_expectation", "market_commentary",
                    "unrelated_or_false_positive", "unknown",
                } else "unknown",
                "publisher_or_origin": publisher,
                "source_table": _api_from_label(candidate_meta.get("source_label")),
                "source_name": candidate_meta.get("source"),
                "source_title": candidate_meta.get("title"),
                "source_url": candidate_meta.get("url") or "",
                "source_publish_time": _parse_dt(candidate_meta.get("publish_time")),
                "evidence_sentence": (ind.get("evidence_sentence") or "")[:500],
                "extraction_model": f"{extraction_model}/{extraction_endpoint}",
                "extraction_prompt_version": PROMPT_VERSION,
                "confidence": confidence if confidence in {"high", "medium", "low"} else "low",
            }
            res = conn.execute(sql, params)
            if res.rowcount:
                inserted += 1
    return inserted


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
