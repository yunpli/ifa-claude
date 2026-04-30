"""Shared scan-and-extract runner.

Both jobs follow the same outer loop:

    for each source in sources:
        determine [start, end] window from watermark + lookback
        fetch news rows in that window
        keyword-filter to candidates
        batch-extract via LLM
        upsert results into the job's target table
        update the watermark

This module owns that loop. Each job plugs in:
  - its keyword specs
  - its LLM prompt + schema hint
  - its `process_batch_results` callback (which knows how to turn LLM JSON into
    the right repo upserts)
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import structlog
from sqlalchemy.engine import Engine

from ifa.config import RunMode
from ifa.core.report.timezones import BJT
from ifa.core.llm import LLMClient
from ifa.core.tushare import TuShareClient
from ifa.jobs.common.llm_batch import (
    BatchResult,
    CandidateInput,
    call_batch,
    chunked,
)
from ifa.jobs.common.news_source import NewsSource, fetch_window
from ifa.jobs.common.text_filter import KeywordSpec, first_matching_keywords
from ifa.jobs.common.watermark import get_watermark, upsert_watermark

log = structlog.get_logger(__name__)


@dataclass
class SourceStats:
    label: str
    rows_scanned: int = 0
    candidates_filtered: int = 0
    extracted: int = 0
    batches_attempted: int = 0
    batches_failed: int = 0
    new_high_water: dt.datetime | None = None


@dataclass
class JobReport:
    job_name: str
    run_mode: RunMode
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    per_source: dict[str, SourceStats] = field(default_factory=dict)
    extracted_total: int = 0
    candidates_filtered_total: int = 0
    rows_scanned_total: int = 0
    errors: list[str] = field(default_factory=list)


def run_scan_and_extract(
    *,
    job_name: str,
    sources: list[NewsSource],
    keyword_specs: list[KeywordSpec],
    system_prompt: str,
    instructions: str,
    output_schema_hint: str,
    process_batch_results: Callable[[list[CandidateInput], BatchResult], int],
    engine: Engine,
    tushare: TuShareClient,
    llm: LLMClient,
    run_mode: RunMode,
    lookback_days: int,
    batch_size: int = 5,
    max_extra_lookback_days: int = 90,
    chunk_days: int = 7,
    timezone: str = "Asia/Shanghai",
    on_log: Callable[[str], None] | None = None,
) -> JobReport:
    """Drive the full scan→filter→batch-extract→upsert loop.

    `process_batch_results(candidates, batch_result)` returns the number of
    rows actually persisted from this batch (used for stats only).
    """
    now = dt.datetime.now(dt.timezone.utc)
    floor = now - dt.timedelta(days=max_extra_lookback_days)
    requested_start = now - dt.timedelta(days=lookback_days)

    report = JobReport(job_name=job_name, run_mode=run_mode, started_at=now)

    def _log(msg: str) -> None:
        log.info(msg)
        if on_log:
            on_log(msg)

    for source in sources:
        stats = SourceStats(label=source.label)
        report.per_source[source.label] = stats

        # Determine window: max(watermark + 1ms, requested_start, floor) → now
        wm = get_watermark(engine, job_name=job_name, source_label=source.label)
        window_start = requested_start
        if wm and wm.last_publish_time_scanned:
            wm_ts = wm.last_publish_time_scanned
            if wm_ts.tzinfo is None:
                wm_ts = wm_ts.replace(tzinfo=dt.timezone.utc)
            # Resume strictly after the last seen publish_time
            window_start = max(window_start, wm_ts + dt.timedelta(seconds=1))
        if window_start < floor:
            window_start = floor
        window_end = now

        if window_start >= window_end:
            _log(f"[{source.label}] up-to-date (watermark ≥ now); skipping")
            continue

        _log(f"[{source.label}] scanning {window_start.isoformat()} → {window_end.isoformat()}")

        # Fetch
        try:
            df = fetch_window(
                tushare, source,
                start=window_start,
                end=window_end,
                chunk_days=chunk_days,
            )
        except Exception as exc:  # noqa: BLE001
            err = f"[{source.label}] fetch failed: {type(exc).__name__}: {exc}"
            _log(err)
            report.errors.append(err)
            continue

        stats.rows_scanned = len(df)
        if df.empty:
            _log(f"[{source.label}] 0 rows in window")
            upsert_watermark(
                engine, job_name=job_name, source_label=source.label,
                new_high_water=window_end, run_mode=run_mode,
                rows_scanned_delta=0, candidates_filtered_delta=0, candidates_extracted_delta=0,
            )
            continue

        # Filter
        candidates = _filter_to_candidates(df, keyword_specs)
        stats.candidates_filtered = len(candidates)
        _log(f"[{source.label}] {stats.rows_scanned} rows scanned, {stats.candidates_filtered} candidates")

        if not candidates:
            new_hw = _max_publish_time(df) or window_end
            stats.new_high_water = new_hw
            upsert_watermark(
                engine, job_name=job_name, source_label=source.label,
                new_high_water=new_hw, run_mode=run_mode,
                rows_scanned_delta=stats.rows_scanned, candidates_filtered_delta=0, candidates_extracted_delta=0,
            )
            continue

        # Batch-extract
        batches = chunked(candidates, batch_size)
        for batch in batches:
            stats.batches_attempted += 1
            try:
                br = call_batch(
                    llm,
                    system_prompt=system_prompt,
                    instructions=instructions,
                    output_schema_hint=output_schema_hint,
                    candidates=batch,
                )
            except Exception as exc:  # noqa: BLE001
                err = f"[{source.label}] LLM call failed: {type(exc).__name__}: {exc}"
                _log(err)
                report.errors.append(err)
                stats.batches_failed += 1
                continue

            if br.parse_status not in {"parsed", "fallback_used"}:
                err = f"[{source.label}] batch parse {br.parse_status}: {br.error}"
                _log(err)
                report.errors.append(err)
                stats.batches_failed += 1
                continue

            try:
                n_inserted = process_batch_results(batch, br)
            except Exception as exc:  # noqa: BLE001
                err = f"[{source.label}] persist failed: {type(exc).__name__}: {exc}"
                _log(err)
                report.errors.append(err)
                stats.batches_failed += 1
                continue
            stats.extracted += n_inserted
            _log(f"[{source.label}] batch ({len(batch)}): persisted {n_inserted} rows "
                 f"(model={br.raw_response.model}, latency={br.raw_response.latency_seconds:.1f}s)")

        new_hw = _max_publish_time(df) or window_end
        stats.new_high_water = new_hw
        upsert_watermark(
            engine, job_name=job_name, source_label=source.label,
            new_high_water=new_hw, run_mode=run_mode,
            rows_scanned_delta=stats.rows_scanned,
            candidates_filtered_delta=stats.candidates_filtered,
            candidates_extracted_delta=stats.extracted,
        )

        report.rows_scanned_total += stats.rows_scanned
        report.candidates_filtered_total += stats.candidates_filtered
        report.extracted_total += stats.extracted

    report.finished_at = dt.datetime.now(dt.timezone.utc)
    return report


def _filter_to_candidates(df: pd.DataFrame, specs: list[KeywordSpec]) -> list[CandidateInput]:
    out: list[CandidateInput] = []
    for idx, row in df.iterrows():
        text = (str(row.get("title", "")) + "\n" + str(row.get("content", ""))).strip()
        matched = first_matching_keywords(text, specs)
        if not matched:
            continue
        pt = row.get("publish_time")
        pt_iso = ""
        if isinstance(pt, (pd.Timestamp, dt.datetime)) and not pd.isna(pt):
            # TuShare news/major_news/npr return Beijing wall-clock timestamps as
            # naive datetimes. Tag them as BJT so the downstream UTC conversion
            # is correct (otherwise everything ends up shifted by +8h).
            py_pt = pt.to_pydatetime() if isinstance(pt, pd.Timestamp) else pt
            if py_pt.tzinfo is None:
                py_pt = py_pt.replace(tzinfo=BJT)
            pt_iso = py_pt.isoformat()
        out.append(CandidateInput(
            candidate_index=len(out),
            title=str(row.get("title", "")),
            source=str(row.get("src", "")) or row.get("source_label", ""),
            publish_time=pt_iso,
            url=str(row.get("url", "")),
            content=str(row.get("content", "")),
            matched_keywords=matched,
        ))
    # Re-index after filter
    for i, c in enumerate(out):
        c.candidate_index = i  # type: ignore[misc]
    return out


def _max_publish_time(df: pd.DataFrame) -> dt.datetime | None:
    if df.empty or "publish_time" not in df.columns:
        return None
    s = pd.to_datetime(df["publish_time"], errors="coerce", utc=True)
    s = s.dropna()
    if s.empty:
        return None
    return s.max().to_pydatetime()
