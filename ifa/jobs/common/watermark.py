"""Repository for `news_scan_watermarks`.

Each (job_name, source_label) row tracks the high-water mark of source
publish_time we've successfully processed.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.config import RunMode


@dataclass
class Watermark:
    job_name: str
    source_label: str
    last_publish_time_scanned: dt.datetime | None
    last_run_at: dt.datetime | None
    last_run_mode: str | None
    rows_scanned_total: int
    candidates_filtered_total: int
    candidates_extracted_total: int


def get_watermark(engine: Engine, *, job_name: str, source_label: str) -> Watermark | None:
    sql = text("""
        SELECT job_name, source_label, last_publish_time_scanned, last_run_at,
               last_run_mode, rows_scanned_total, candidates_filtered_total,
               candidates_extracted_total
          FROM news_scan_watermarks
         WHERE job_name = :j AND source_label = :s
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"j": job_name, "s": source_label}).one_or_none()
    if row is None:
        return None
    return Watermark(
        job_name=row.job_name,
        source_label=row.source_label,
        last_publish_time_scanned=row.last_publish_time_scanned,
        last_run_at=row.last_run_at,
        last_run_mode=row.last_run_mode,
        rows_scanned_total=row.rows_scanned_total,
        candidates_filtered_total=row.candidates_filtered_total,
        candidates_extracted_total=row.candidates_extracted_total,
    )


def upsert_watermark(
    engine: Engine,
    *,
    job_name: str,
    source_label: str,
    new_high_water: dt.datetime | None,
    run_mode: RunMode,
    rows_scanned_delta: int,
    candidates_filtered_delta: int,
    candidates_extracted_delta: int,
) -> None:
    """Advance the watermark, taking max() with the existing value to never go backwards."""
    sql = text("""
        INSERT INTO news_scan_watermarks
            (job_name, source_label, last_publish_time_scanned, last_run_at,
             last_run_mode, rows_scanned_total, candidates_filtered_total,
             candidates_extracted_total)
        VALUES
            (:j, :s, :hw, now(), :m, :rs, :cf, :ce)
        ON CONFLICT (job_name, source_label) DO UPDATE SET
            last_publish_time_scanned = GREATEST(
                COALESCE(news_scan_watermarks.last_publish_time_scanned, EXCLUDED.last_publish_time_scanned),
                COALESCE(EXCLUDED.last_publish_time_scanned, news_scan_watermarks.last_publish_time_scanned)
            ),
            last_run_at  = EXCLUDED.last_run_at,
            last_run_mode = EXCLUDED.last_run_mode,
            rows_scanned_total = news_scan_watermarks.rows_scanned_total + EXCLUDED.rows_scanned_total,
            candidates_filtered_total = news_scan_watermarks.candidates_filtered_total + EXCLUDED.candidates_filtered_total,
            candidates_extracted_total = news_scan_watermarks.candidates_extracted_total + EXCLUDED.candidates_extracted_total
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "j": job_name,
            "s": source_label,
            "hw": new_high_water,
            "m": run_mode.value,
            "rs": rows_scanned_delta,
            "cf": candidates_filtered_delta,
            "ce": candidates_extracted_delta,
        })
