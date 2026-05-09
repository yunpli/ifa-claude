"""Weekly theme heat cache for Stock Edge sector-cycle research.

The table is intentionally separate from generic report/model output audit rows:
backtests need one PIT-safe, queryable weekly feature surface instead of parsing
HTML reports or LLM logs.  Backfill can start with operator-curated stubs and
later replace them with cached LLM extraction from news/report inputs.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from typing import Any, Sequence


PROMPT_VERSION = "stock_theme_heat_v1"
SOURCE_POLICY_VERSION = "stock_theme_heat_local_sources_v1"


@dataclass(frozen=True)
class WeeklyThemeHeat:
    valid_week: dt.date
    theme_rank: int
    theme_label: str
    category: str
    heat_score: float
    confidence: float | None = None
    affected_sectors: list[dict[str, Any]] = field(default_factory=list)
    representative_stocks: list[dict[str, Any]] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    model_name: str | None = None
    prompt_version: str = PROMPT_VERSION
    run_mode: str = "manual"
    quality_flag: str = "stub"


def week_start(value: dt.date) -> dt.date:
    return value - dt.timedelta(days=value.weekday())


def default_stub_themes(valid_week: dt.date) -> list[WeeklyThemeHeat]:
    """Return explicit non-LLM placeholders for pipeline and schema testing.

    These rows are not alpha evidence.  They make the downstream feature join and
    audit path executable while historical news/LLM backfill is designed.
    """
    week = week_start(valid_week)
    return [
        WeeklyThemeHeat(week, 1, "政策与稳增长", "policy", 0.50, 0.10, quality_flag="stub"),
        WeeklyThemeHeat(week, 2, "科技与AI应用", "AI", 0.50, 0.10, quality_flag="stub"),
        WeeklyThemeHeat(week, 3, "半导体与国产替代", "semiconductors", 0.50, 0.10, quality_flag="stub"),
        WeeklyThemeHeat(week, 4, "资源与有色金属", "resources_metals", 0.50, 0.10, quality_flag="stub"),
        WeeklyThemeHeat(week, 5, "消费与出行复苏", "consumption", 0.50, 0.10, quality_flag="stub"),
    ]


def upsert_weekly_theme_heat(engine: Engine, rows: Sequence[WeeklyThemeHeat]) -> int:
    if not rows:
        return 0
    sql = text("""
        INSERT INTO stock.theme_heat_weekly (
            valid_week, theme_rank, theme_label, category, heat_score, confidence,
            affected_sectors_json, representative_stocks_json, source_urls_json,
            evidence_json, model_name, prompt_version, run_mode, quality_flag
        ) VALUES (
            :valid_week, :theme_rank, :theme_label, :category, :heat_score, :confidence,
            CAST(:affected_sectors AS JSONB), CAST(:representative_stocks AS JSONB),
            CAST(:source_urls AS JSONB), CAST(:evidence AS JSONB),
            :model_name, :prompt_version, :run_mode, :quality_flag
        )
        ON CONFLICT (valid_week, theme_rank) DO UPDATE SET
            theme_label = EXCLUDED.theme_label,
            category = EXCLUDED.category,
            heat_score = EXCLUDED.heat_score,
            confidence = EXCLUDED.confidence,
            affected_sectors_json = EXCLUDED.affected_sectors_json,
            representative_stocks_json = EXCLUDED.representative_stocks_json,
            source_urls_json = EXCLUDED.source_urls_json,
            evidence_json = EXCLUDED.evidence_json,
            model_name = EXCLUDED.model_name,
            prompt_version = EXCLUDED.prompt_version,
            generated_at = now(),
            run_mode = EXCLUDED.run_mode,
            quality_flag = EXCLUDED.quality_flag
    """)
    payload = [
        {
            "valid_week": row.valid_week,
            "theme_rank": row.theme_rank,
            "theme_label": row.theme_label,
            "category": row.category,
            "heat_score": row.heat_score,
            "confidence": row.confidence,
            "affected_sectors": json.dumps(row.affected_sectors, ensure_ascii=False, default=str),
            "representative_stocks": json.dumps(row.representative_stocks, ensure_ascii=False, default=str),
            "source_urls": json.dumps(row.source_urls, ensure_ascii=False, default=str),
            "evidence": json.dumps(row.evidence, ensure_ascii=False, default=str),
            "model_name": row.model_name,
            "prompt_version": row.prompt_version,
            "run_mode": row.run_mode,
            "quality_flag": row.quality_flag,
        }
        for row in rows
    ]
    with engine.begin() as conn:
        conn.execute(sql, payload)
    return len(payload)


def load_weekly_theme_heat(engine: Engine, valid_week: dt.date) -> list[dict[str, Any]]:
    sql = text("""
        SELECT valid_week, theme_rank, theme_label, category, heat_score, confidence,
               affected_sectors_json, representative_stocks_json, source_urls_json,
               evidence_json, model_name, prompt_version, generated_at, run_mode, quality_flag
        FROM stock.theme_heat_weekly
        WHERE valid_week = :valid_week
        ORDER BY theme_rank
    """)
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(sql, {"valid_week": week_start(valid_week)}).mappings().all()]


def build_weekly_theme_heat_from_local_sources(
    engine: Engine,
    valid_week: dt.date,
    *,
    min_source_rows: int = 3,
    max_themes: int = 5,
    run_mode: str = "manual",
) -> dict[str, Any]:
    """Build non-stub weekly theme heat from already cached local event tables.

    This is intentionally a conservative source-policy builder, not an online
    extractor.  It reads only structured local memories that were produced by
    other jobs.  If those rows do not include enough event/theme evidence for a
    week, it returns a blocker and leaves operator JSON ingestion as the
    supported path.
    """
    week = week_start(valid_week)
    end = week + dt.timedelta(days=7)
    rows = _load_theme_source_rows(engine, week, end)
    if len(rows) < min_source_rows:
        return {
            "status": "blocked",
            "valid_week": week.isoformat(),
            "source_policy": SOURCE_POLICY_VERSION,
            "source_rows": len(rows),
            "required_source_rows": min_source_rows,
            "reason": "insufficient_cached_local_sources",
            "message": (
                "No external LLM/news calls are made by this builder. Provide "
                "--from-json with approved cached/manual theme rows, or backfill "
                "research.company_event_memory / ta.catalyst_event_memory first."
            ),
        }
    themes = _aggregate_theme_rows(rows, week=week, max_themes=max_themes, run_mode=run_mode)
    if not themes:
        return {
            "status": "blocked",
            "valid_week": week.isoformat(),
            "source_policy": SOURCE_POLICY_VERSION,
            "source_rows": len(rows),
            "required_source_rows": min_source_rows,
            "reason": "source_rows_not_theme_mappable",
        }
    return {
        "status": "ready",
        "valid_week": week.isoformat(),
        "source_policy": SOURCE_POLICY_VERSION,
        "source_rows": len(rows),
        "rows": themes,
    }


def _load_theme_source_rows(engine: Engine, week: dt.date, end: dt.date) -> list[dict[str, Any]]:
    sql = text("""
        SELECT 'research.company_event_memory' AS source_table,
               capture_date,
               event_type,
               title,
               summary,
               polarity,
               importance,
               source_url,
               ts_code,
               NULL::text[] AS target_ts_codes,
               NULL::text[] AS target_sectors
        FROM research.company_event_memory
        WHERE capture_date >= :week AND capture_date < :end
        UNION ALL
        SELECT 'ta.catalyst_event_memory' AS source_table,
               capture_date,
               event_type,
               title,
               summary,
               polarity,
               importance,
               source_url,
               NULL AS ts_code,
               target_ts_codes,
               target_sectors
        FROM ta.catalyst_event_memory
        WHERE capture_date >= :week AND capture_date < :end
    """)
    try:
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(sql, {"week": week, "end": end}).mappings().all()]
    except Exception:
        return []


def _aggregate_theme_rows(
    rows: Sequence[dict[str, Any]],
    *,
    week: dt.date,
    max_themes: int,
    run_mode: str,
) -> list[WeeklyThemeHeat]:
    buckets: dict[str, dict[str, Any]] = {}
    total_weight = 0.0
    for row in rows:
        label = _theme_label(row)
        bucket = buckets.setdefault(
            label,
            {
                "label": label,
                "weight": 0.0,
                "rows": 0,
                "sources": set(),
                "urls": set(),
                "stocks": {},
                "sectors": {},
                "polarity": {"positive": 0, "neutral": 0, "negative": 0},
            },
        )
        weight = _row_weight(row)
        total_weight += weight
        bucket["weight"] += weight
        bucket["rows"] += 1
        bucket["sources"].add(row.get("source_table"))
        if row.get("source_url"):
            bucket["urls"].add(str(row["source_url"]))
        for stock in _stock_codes(row):
            bucket["stocks"][stock] = {"ts_code": stock}
        for sector in _sector_names(row):
            bucket["sectors"][sector] = {"sector_name": sector}
        polarity = str(row.get("polarity") or "neutral")
        if polarity in bucket["polarity"]:
            bucket["polarity"][polarity] += 1
    ranked = sorted(buckets.values(), key=lambda item: (item["weight"], item["rows"], item["label"]), reverse=True)
    denom = max(total_weight, 1.0)
    output: list[WeeklyThemeHeat] = []
    for rank, bucket in enumerate(ranked[:max_themes], start=1):
        heat_score = max(0.05, min(1.0, float(bucket["weight"]) / denom))
        output.append(
            WeeklyThemeHeat(
                valid_week=week,
                theme_rank=rank,
                theme_label=str(bucket["label"]),
                category=str(bucket["label"]),
                heat_score=round(heat_score, 4),
                confidence=round(min(0.95, 0.35 + 0.1 * int(bucket["rows"])), 4),
                affected_sectors=list(bucket["sectors"].values())[:20],
                representative_stocks=list(bucket["stocks"].values())[:20],
                source_urls=sorted(bucket["urls"])[:20],
                evidence={
                    "source_policy": SOURCE_POLICY_VERSION,
                    "source_tables": sorted(s for s in bucket["sources"] if s),
                    "source_rows": bucket["rows"],
                    "polarity_counts": bucket["polarity"],
                    "builder": "local_cached_event_memory",
                },
                model_name=None,
                prompt_version=SOURCE_POLICY_VERSION,
                run_mode=run_mode,
                quality_flag="local_source_cache",
            )
        )
    return output


def _theme_label(row: dict[str, Any]) -> str:
    event_type = str(row.get("event_type") or "").strip()
    if event_type:
        return event_type
    title = str(row.get("title") or "").strip()
    return title[:24] if title else "uncategorized_event"


def _row_weight(row: dict[str, Any]) -> float:
    importance = {"high": 1.0, "medium": 0.65, "low": 0.35}.get(str(row.get("importance") or "").lower(), 0.5)
    polarity = {"positive": 1.0, "neutral": 0.75, "negative": 0.85}.get(str(row.get("polarity") or "").lower(), 0.75)
    return importance * polarity


def _stock_codes(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    if row.get("ts_code"):
        values.append(str(row["ts_code"]))
    targets = row.get("target_ts_codes") or []
    if isinstance(targets, list):
        values.extend(str(item) for item in targets if item)
    return sorted(set(values))


def _sector_names(row: dict[str, Any]) -> list[str]:
    sectors = row.get("target_sectors") or []
    if not isinstance(sectors, list):
        return []
    return sorted({str(item) for item in sectors if item})
