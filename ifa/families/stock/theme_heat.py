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
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine


PROMPT_VERSION = "stock_theme_heat_v1"


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
