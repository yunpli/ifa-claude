"""Theme heat cache helpers for Stock Edge sector-cycle research.

The table is intentionally separate from generic report/model output audit rows:
backtests need PIT-safe, queryable feature surfaces instead of parsing HTML
reports or LLM logs.  The current implementation only writes the weekly
compatibility table.  The intended design is multi-resolution: raw events by
source/endpoint watermark, optional 1h/2h/4h snapshots, daily heat curves, and
weekly report summaries derived from the lower-level evidence.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from typing import Any, Literal, Sequence


PROMPT_VERSION = "stock_theme_heat_v1"
SOURCE_POLICY_VERSION = "stock_theme_heat_local_sources_v1"
TUSHARE_CACHE_SOURCE_POLICY_VERSION = "stock_theme_heat_tushare_cache_v1"
LLM_WEEKLY_PROMPT_VERSION = "stock_theme_heat_llm_weekly_v1"
LLM_DAILY_PROMPT_VERSION = "stock_theme_heat_llm_daily_v1"
ThemeHeatSource = Literal["local-cache", "tushare-cache", "all-cache"]

_THEME_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AI与算力", ("AI", "人工智能", "算力", "大模型", "机器人", "智能驾驶", "端侧")),
    ("半导体与国产替代", ("半导体", "芯片", "封测", "晶圆", "存储", "国产替代")),
    ("新能源与电力设备", ("新能源", "光伏", "储能", "锂电", "电池", "风电", "充电")),
    ("低空经济与军工", ("低空经济", "无人机", "航空", "航天", "军工", "卫星")),
    ("并购重组与资本运作", ("并购", "重组", "收购", "资产注入", "定增", "回购")),
    ("业绩与订单催化", ("业绩", "预增", "预告", "订单", "合同", "中标", "营收", "利润")),
    ("医药与创新药", ("医药", "创新药", "医疗", "CRO", "疫苗", "器械")),
    ("消费与出海", ("消费", "出海", "跨境", "品牌", "旅游", "零售", "食品")),
    ("资源与有色金属", ("有色", "铜", "铝", "黄金", "稀土", "锂", "煤炭")),
    ("金融地产与稳增长", ("地产", "房地产", "银行", "券商", "保险", "稳增长", "基建")),
)


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
    source: ThemeHeatSource = "local-cache",
    min_source_rows: int = 3,
    max_themes: int = 5,
    source_row_limit: int | None = None,
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
    rows = _load_theme_source_rows(engine, week, end, source=source, limit=source_row_limit)
    source_policy = (
        TUSHARE_CACHE_SOURCE_POLICY_VERSION
        if source == "tushare-cache"
        else f"{SOURCE_POLICY_VERSION}+{TUSHARE_CACHE_SOURCE_POLICY_VERSION}"
        if source == "all-cache"
        else SOURCE_POLICY_VERSION
    )
    if len(rows) < min_source_rows:
        return {
            "status": "blocked",
            "valid_week": week.isoformat(),
            "source": source,
            "source_policy": source_policy,
            "source_rows": len(rows),
            "required_source_rows": min_source_rows,
            "reason": "insufficient_cached_local_sources",
            "message": (
                "No external LLM/news calls are made by this builder. Provide "
                "--from-json with approved cached/manual theme rows, or backfill "
                "research.company_event_memory / ta.catalyst_event_memory / "
                "research.api_cache Tushare rows first."
            ),
        }
    themes = _aggregate_theme_rows(
        rows,
        week=week,
        max_themes=max_themes,
        run_mode=run_mode,
        source_policy=source_policy,
    )
    if not themes:
        return {
            "status": "blocked",
            "valid_week": week.isoformat(),
            "source": source,
            "source_policy": source_policy,
            "source_rows": len(rows),
            "required_source_rows": min_source_rows,
            "reason": "source_rows_not_theme_mappable",
        }
    return {
        "status": "ready",
        "valid_week": week.isoformat(),
        "source": source,
        "source_policy": source_policy,
        "source_rows": len(rows),
        "rows": themes,
    }


def build_weekly_theme_heat_with_llm(
    engine: Engine,
    valid_week: dt.date,
    *,
    source: ThemeHeatSource = "all-cache",
    min_source_rows: int = 3,
    max_themes: int = 5,
    source_row_limit: int | None = 300,
    run_mode: str = "manual",
    allow_llm_prior: bool = False,
    llm_client: Any | None = None,
    no_external: bool = False,
) -> dict[str, Any]:
    """Build weekly theme heat via one cached batch LLM call.

    The LLM is used at the weekly/theme level only: it receives a compact pack
    of already-cached local facts and returns a bounded JSON object.  When local
    evidence is thin, callers may set `allow_llm_prior=True`; those outputs must
    carry `quality_flag='llm_prior_only'` or `needs_local_evidence` and are not
    strong alpha evidence until source rows catch up.
    """
    week = week_start(valid_week)
    end = week + dt.timedelta(days=7)
    source_rows = _load_theme_source_rows(engine, week, end, source=source, limit=source_row_limit)
    evidence_quality = "local_evidence" if len(source_rows) >= min_source_rows else "needs_local_evidence"
    if len(source_rows) < min_source_rows and not allow_llm_prior:
        return {
            "status": "blocked",
            "valid_week": week.isoformat(),
            "source": source,
            "source_rows": len(source_rows),
            "required_source_rows": min_source_rows,
            "reason": "insufficient_cached_local_sources",
            "message": "Use --allow-llm-prior to produce clearly flagged LLM-prior rows, or backfill local facts first.",
        }

    messages = weekly_theme_heat_llm_messages(
        week,
        source_rows,
        max_themes=max_themes,
        evidence_quality=evidence_quality,
    )
    schema = weekly_theme_heat_response_schema(max_themes=max_themes)
    if no_external:
        return {
            "status": "llm_dry_run",
            "valid_week": week.isoformat(),
            "source": source,
            "source_rows": len(source_rows),
            "evidence_quality": evidence_quality,
            "prompt_version": LLM_WEEKLY_PROMPT_VERSION,
            "messages": messages,
            "response_schema": schema,
        }

    if llm_client is None:
        from ifa.core.llm import LLMClient

        llm_client = LLMClient(request_timeout=90.0)
    response = llm_client.chat(
        messages=messages,
        max_tokens=2600,
        temperature=0.15,
        response_format={"type": "json_object"},
    )
    parsed = response.parse_json()
    rows = weekly_theme_heat_rows_from_llm_response(
        parsed,
        week=week,
        run_mode=run_mode,
        model_name=getattr(response, "model", None),
        source_rows=source_rows,
        evidence_quality=evidence_quality,
        max_themes=max_themes,
    )
    return {
        "status": "ready",
        "valid_week": week.isoformat(),
        "source": source,
        "source_rows": len(source_rows),
        "evidence_quality": evidence_quality,
        "prompt_version": LLM_WEEKLY_PROMPT_VERSION,
        "model_name": getattr(response, "model", None),
        "endpoint": getattr(response, "endpoint", None),
        "rows": rows,
    }


def weekly_theme_heat_llm_messages(
    week: dt.date,
    source_rows: Sequence[dict[str, Any]],
    *,
    max_themes: int = 5,
    evidence_quality: str,
) -> list[dict[str, str]]:
    week_end = week + dt.timedelta(days=6)
    facts = [_compact_fact_row(row, idx) for idx, row in enumerate(source_rows, start=1)]
    user_payload = {
        "task": "Identify weekly A-share themes that truly changed capital behavior.",
        "market": "China A-share",
        "week_start": week.isoformat(),
        "week_end": week_end.isoformat(),
        "max_themes": max_themes,
        "evidence_quality": evidence_quality,
        "local_cached_facts": facts,
        "instructions": [
            "Use local cached facts as primary evidence. Do not invent numeric market data.",
            "If facts are insufficient, you may use model prior knowledge only for preliminary theme selection, and every affected theme must set evidence_quality to llm_prior_only or needs_local_evidence.",
            "Answer which themes in the past week truly affected capital behavior, which may persist, and which are likely one-day hype.",
            "Separate one-day hype from themes likely to persist for multiple trading days or weeks.",
            "Map themes to A-share sectors and representative stocks when supported by facts or well-known market structure; mark unsupported mappings as needs_local_evidence.",
            "For each theme, describe 主力资金/main-money behavior, 散户追涨/retail chase risk, crowding/distribution risk, and 1/3/5/10/20 trading-day validation signals.",
            "Return JSON only and keep arrays concise.",
        ],
    }
    system = (
        "You are an institutional China A-share thematic strategist. "
        "Your job is high-level theme selection, not per-news tagging. "
        "Produce auditable JSON for a cached weekly theme heat table. "
        "LLM reasoning can rank and connect themes, but weak evidence must be explicitly downgraded."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=str)},
    ]


def weekly_theme_heat_response_schema(*, max_themes: int = 5) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["themes"],
        "properties": {
            "themes": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_themes,
                "items": {
                    "type": "object",
                    "required": [
                        "theme_label",
                        "category",
                        "heat_score",
                        "persistence_score",
                        "freshness",
                        "one_day_wonder_risk",
                        "quality_flag",
                    ],
                    "properties": {
                        "theme_label": {"type": "string"},
                        "category": {"type": "string"},
                        "heat_score": {"type": "number", "minimum": 0, "maximum": 1},
                        "persistence_score": {"type": "number", "minimum": 0, "maximum": 1},
                        "freshness": {"type": "string"},
                        "affected_sectors": {"type": "array", "items": {"type": "object"}},
                        "representative_stocks": {"type": "array", "items": {"type": "object"}},
                        "leader_candidates": {"type": "array", "items": {"type": "object"}},
                        "main_logic": {"type": "string"},
                        "risks": {"type": "array", "items": {"type": "string"}},
                        "one_day_wonder_risk": {"type": "number", "minimum": 0, "maximum": 1},
                        "validation_signals": {"type": "array", "items": {"type": "string"}},
                        "validation_signals_by_horizon": {"type": "object"},
                        "main_money_judgement": {"type": "string"},
                        "retail_chase_judgement": {"type": "string"},
                        "crowding_distribution_risk": {"type": "string"},
                        "evidence_refs": {"type": "array", "items": {"type": "object"}},
                        "evidence_quality": {"type": "string"},
                        "quality_flag": {"type": "string"},
                    },
                },
            }
        },
    }


def weekly_theme_heat_rows_from_llm_response(
    parsed: dict[str, Any],
    *,
    week: dt.date,
    run_mode: str,
    model_name: str | None,
    source_rows: Sequence[dict[str, Any]],
    evidence_quality: str,
    max_themes: int,
) -> list[WeeklyThemeHeat]:
    raw_themes = parsed.get("themes") if isinstance(parsed, dict) else None
    if not isinstance(raw_themes, list):
        raise ValueError("LLM theme heat response must contain a themes list")
    fact_lookup = {int(item["fact_id"]): item for item in (_compact_fact_row(row, idx) for idx, row in enumerate(source_rows, start=1))}
    rows: list[WeeklyThemeHeat] = []
    for rank, raw in enumerate(raw_themes[:max_themes], start=1):
        if not isinstance(raw, dict):
            continue
        refs = raw.get("evidence_refs") or []
        source_urls = _urls_from_evidence_refs(refs, fact_lookup)
        quality_flag = _llm_quality_flag(raw, evidence_quality)
        evidence = {
            "prompt_version": LLM_WEEKLY_PROMPT_VERSION,
            "evidence_quality": raw.get("evidence_quality") or evidence_quality,
            "source_rows": len(source_rows),
            "evidence_refs": refs[:20] if isinstance(refs, list) else [],
            "main_logic": raw.get("main_logic") or "",
            "risks": list(raw.get("risks") or [])[:10],
            "leader_candidates": list(raw.get("leader_candidates") or [])[:20],
            "validation_signals": list(raw.get("validation_signals") or [])[:20],
            "validation_signals_by_horizon": dict(raw.get("validation_signals_by_horizon") or {}),
            "main_money_judgement": str(raw.get("main_money_judgement") or ""),
            "retail_chase_judgement": str(raw.get("retail_chase_judgement") or ""),
            "crowding_distribution_risk": str(raw.get("crowding_distribution_risk") or ""),
            "one_day_wonder_risk": _clip_float(raw.get("one_day_wonder_risk"), default=0.5),
            "persistence_score": _clip_float(raw.get("persistence_score"), default=0.0),
            "freshness": str(raw.get("freshness") or "unknown"),
            "builder": "weekly_llm_theme_heat_batch",
        }
        rows.append(
            WeeklyThemeHeat(
                valid_week=week,
                theme_rank=rank,
                theme_label=str(raw.get("theme_label") or f"theme_{rank}"),
                category=str(raw.get("category") or raw.get("theme_label") or f"theme_{rank}"),
                heat_score=round(_clip_float(raw.get("heat_score"), default=0.0), 4),
                confidence=round(max(0.05, min(0.95, 1.0 - float(evidence["one_day_wonder_risk"]) * 0.35)), 4),
                affected_sectors=list(raw.get("affected_sectors") or [])[:20],
                representative_stocks=list(raw.get("representative_stocks") or [])[:20],
                source_urls=source_urls[:20],
                evidence=evidence,
                model_name=model_name,
                prompt_version=LLM_WEEKLY_PROMPT_VERSION,
                run_mode=run_mode,
                quality_flag=quality_flag,
            )
        )
    return rows


def build_daily_theme_heat_with_llm(
    engine: Engine,
    as_of: dt.date,
    *,
    window_days: int = 7,
    source: ThemeHeatSource = "all-cache",
    min_source_rows: int = 3,
    max_themes: int = 8,
    source_row_limit: int | None = 300,
    run_mode: str = "manual",
    allow_llm_prior: bool = False,
    llm_client: Any | None = None,
    no_external: bool = False,
) -> dict[str, Any]:
    """Build one daily/window theme scan via a single repo LLMClient call.

    Daily rows are intentionally returned as a JSON artifact first.  They are
    not written into a feature table until the rolling heat-curve schema is
    designed, because downstream backtests need stable PIT semantics instead of
    ad hoc daily table churn.
    """
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    start = as_of - dt.timedelta(days=window_days - 1)
    end = as_of + dt.timedelta(days=1)
    source_rows = _load_theme_source_rows(engine, start, end, source=source, limit=source_row_limit)
    evidence_quality = "local_evidence" if len(source_rows) >= min_source_rows else "needs_local_evidence"
    if len(source_rows) < min_source_rows and not allow_llm_prior:
        return {
            "status": "blocked",
            "as_of": as_of.isoformat(),
            "window_days": window_days,
            "source": source,
            "source_rows": len(source_rows),
            "required_source_rows": min_source_rows,
            "reason": "insufficient_cached_local_sources",
            "message": "Use --allow-llm-prior for clearly flagged prior-only rows, or provide --from-json after review.",
        }

    messages = daily_theme_heat_llm_messages(
        as_of,
        source_rows,
        window_days=window_days,
        max_themes=max_themes,
        evidence_quality=evidence_quality,
    )
    schema = daily_theme_heat_response_schema(max_themes=max_themes)
    if no_external:
        return {
            "status": "llm_dry_run",
            "as_of": as_of.isoformat(),
            "window_days": window_days,
            "source": source,
            "source_rows": len(source_rows),
            "evidence_quality": evidence_quality,
            "prompt_version": LLM_DAILY_PROMPT_VERSION,
            "messages": messages,
            "response_schema": schema,
        }

    if llm_client is None:
        from ifa.core.llm import LLMClient

        llm_client = LLMClient(request_timeout=90.0)
    response = llm_client.chat(
        messages=messages,
        max_tokens=3200,
        temperature=0.15,
        response_format={"type": "json_object"},
    )
    parsed = response.parse_json()
    return daily_theme_heat_artifact_from_llm_response(
        parsed,
        as_of=as_of,
        window_days=window_days,
        run_mode=run_mode,
        model_name=getattr(response, "model", None),
        endpoint=getattr(response, "endpoint", None),
        source=source,
        source_rows=source_rows,
        evidence_quality=evidence_quality,
        max_themes=max_themes,
    )


def daily_theme_heat_llm_messages(
    as_of: dt.date,
    source_rows: Sequence[dict[str, Any]],
    *,
    window_days: int,
    max_themes: int = 8,
    evidence_quality: str,
) -> list[dict[str, str]]:
    start = as_of - dt.timedelta(days=window_days - 1)
    facts = [_compact_fact_row(row, idx) for idx, row in enumerate(source_rows, start=1)]
    user_payload = {
        "task": "Daily A-share theme scan for capital-behavior impact.",
        "market": "China A-share",
        "as_of": as_of.isoformat(),
        "window_start": start.isoformat(),
        "window_end": as_of.isoformat(),
        "window_days": window_days,
        "max_themes": max_themes,
        "source_marker": "llm_daily_theme_scan",
        "evidence_quality": evidence_quality,
        "local_cached_facts": facts,
        "questions": [
            "过去一周/近几日中国A股真正影响资金行为的核心热点是什么？",
            "哪些热点可能持续，哪些只是一日游？",
            "对应哪些申万/概念板块？",
            "代表股票/潜在龙头是谁，为什么？",
            "主力资金、散户追涨、拥挤和出货风险如何判断？",
            "未来1/3/5/10/20个交易日应该观察什么验证信号？",
        ],
        "instructions": [
            "Make exactly one batch-level judgement; do not make per-news or per-stock LLM calls or row tags.",
            "Use local_cached_facts as evidence when available. If evidence is thin, mark the whole scan and affected themes as llm_prior_only or needs_local_evidence.",
            "Do not invent exact money-flow numbers, returns, or holdings. Use qualitative flow judgement only unless facts explicitly contain numbers.",
            "Return JSON only. Keep all arrays bounded and auditable.",
        ],
    }
    system = (
        "You are an institutional China A-share thematic strategist. "
        "Identify themes that changed capital behavior and produce structured JSON for local caching. "
        "LLM prior is allowed only when explicitly flagged as weak evidence."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=str)},
    ]


def daily_theme_heat_response_schema(*, max_themes: int = 8) -> dict[str, Any]:
    theme_schema = weekly_theme_heat_response_schema(max_themes=max_themes)["properties"]["themes"]
    return {
        "type": "object",
        "required": ["scan_date", "window_days", "source_quality", "themes"],
        "properties": {
            "scan_date": {"type": "string"},
            "window_days": {"type": "integer"},
            "source_quality": {"type": "string"},
            "market_summary": {"type": "string"},
            "themes": theme_schema,
        },
    }


def daily_theme_heat_artifact_from_llm_response(
    parsed: dict[str, Any],
    *,
    as_of: dt.date,
    window_days: int,
    run_mode: str,
    model_name: str | None,
    endpoint: str | None,
    source: ThemeHeatSource,
    source_rows: Sequence[dict[str, Any]],
    evidence_quality: str,
    max_themes: int,
) -> dict[str, Any]:
    raw_themes = parsed.get("themes") if isinstance(parsed, dict) else None
    if not isinstance(raw_themes, list):
        raise ValueError("LLM daily theme scan response must contain a themes list")
    fact_lookup = {int(item["fact_id"]): item for item in (_compact_fact_row(row, idx) for idx, row in enumerate(source_rows, start=1))}
    themes: list[dict[str, Any]] = []
    for rank, raw in enumerate(raw_themes[:max_themes], start=1):
        if not isinstance(raw, dict):
            continue
        refs = raw.get("evidence_refs") or []
        one_day_risk = _clip_float(raw.get("one_day_wonder_risk"), default=0.5)
        themes.append(
            {
                "theme_rank": rank,
                "theme_label": str(raw.get("theme_label") or f"theme_{rank}"),
                "category": str(raw.get("category") or raw.get("theme_label") or f"theme_{rank}"),
                "heat_score": round(_clip_float(raw.get("heat_score"), default=0.0), 4),
                "confidence": round(max(0.05, min(0.95, 1.0 - one_day_risk * 0.35)), 4),
                "persistence_score": round(_clip_float(raw.get("persistence_score"), default=0.0), 4),
                "freshness": str(raw.get("freshness") or "unknown"),
                "affected_sectors": list(raw.get("affected_sectors") or [])[:20],
                "representative_stocks": list(raw.get("representative_stocks") or [])[:20],
                "leader_candidates": list(raw.get("leader_candidates") or [])[:20],
                "main_logic": str(raw.get("main_logic") or ""),
                "main_money_judgement": str(raw.get("main_money_judgement") or ""),
                "retail_chase_judgement": str(raw.get("retail_chase_judgement") or ""),
                "crowding_distribution_risk": str(raw.get("crowding_distribution_risk") or ""),
                "risks": list(raw.get("risks") or [])[:10],
                "one_day_wonder_risk": round(one_day_risk, 4),
                "validation_signals": list(raw.get("validation_signals") or [])[:20],
                "validation_signals_by_horizon": dict(raw.get("validation_signals_by_horizon") or {}),
                "evidence_refs": refs[:20] if isinstance(refs, list) else [],
                "source_urls": _urls_from_evidence_refs(refs, fact_lookup)[:20],
                "quality_flag": _llm_quality_flag(raw, evidence_quality),
            }
        )
    return {
        "status": "ready",
        "scan_type": "daily",
        "source_marker": "llm_daily_theme_scan",
        "as_of": as_of.isoformat(),
        "window_days": window_days,
        "run_mode": run_mode,
        "source": source,
        "source_rows": len(source_rows),
        "evidence_quality": evidence_quality,
        "quality_flag": "llm_prior_only" if evidence_quality == "needs_local_evidence" else "batch_llm_cache",
        "prompt_version": LLM_DAILY_PROMPT_VERSION,
        "model_name": model_name,
        "endpoint": endpoint,
        "market_summary": str(parsed.get("market_summary") or "") if isinstance(parsed, dict) else "",
        "themes": themes,
    }


def _load_theme_source_rows(
    engine: Engine,
    week: dt.date,
    end: dt.date,
    *,
    source: ThemeHeatSource = "local-cache",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if source in {"local-cache", "all-cache"}:
        rows.extend(_load_local_event_memory_rows(engine, week, end, limit=limit))
    if source in {"tushare-cache", "all-cache"}:
        remaining = max(limit - len(rows), 0) if limit is not None else None
        rows.extend(_load_tushare_api_cache_rows(engine, week, end, limit=remaining))
    deduped = _dedup_source_rows(rows)
    return deduped[:limit] if limit is not None else deduped


def _load_local_event_memory_rows(
    engine: Engine,
    week: dt.date,
    end: dt.date,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
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
        ORDER BY capture_date DESC, importance DESC, title
        LIMIT :limit
    """)
    try:
        with engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    sql,
                    {"week": week, "end": end, "limit": limit or 100000},
                ).mappings().all()
            ]
    except Exception:
        return []


def _load_tushare_api_cache_rows(
    engine: Engine,
    week: dt.date,
    end: dt.date,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Load already-cached Tushare announcement/report rows without API calls.

    `research.api_cache` is keyed by stock and endpoint.  The JSON array is
    expanded once per week, then deduped by source/date/title/url/stock in
    Python.  This keeps theme heat backfills batch-oriented and avoids
    per-stock online Tushare reads.
    """
    sql = text("""
        WITH cached AS (
            SELECT ts_code, api_name, jsonb_array_elements(response_json::jsonb) AS item
            FROM research.api_cache
            WHERE api_name IN ('anns_d', 'research_report')
        )
        SELECT 'research.api_cache.' || api_name AS source_table,
               ts_code,
               api_name,
               COALESCE(item->>'ann_date', item->>'report_date', item->>'pub_date') AS raw_date,
               COALESCE(item->>'title', item->>'ann_title', item->>'report_title') AS title,
               COALESCE(item->>'summary', item->>'abstract', item->>'content', '') AS summary,
               COALESCE(item->>'url', item->>'ann_url', '') AS source_url
        FROM cached
        WHERE COALESCE(item->>'ann_date', item->>'report_date', item->>'pub_date') >= :start_yyyymmdd
          AND COALESCE(item->>'ann_date', item->>'report_date', item->>'pub_date') < :end_yyyymmdd
          AND COALESCE(item->>'title', item->>'ann_title', item->>'report_title') IS NOT NULL
        ORDER BY raw_date DESC, title
        LIMIT :limit
    """)
    params = {
        "start_yyyymmdd": week.strftime("%Y%m%d"),
        "end_yyyymmdd": end.strftime("%Y%m%d"),
        "limit": limit or 100000,
    }
    try:
        with engine.connect() as conn:
            raw_rows = [dict(row) for row in conn.execute(sql, params).mappings().all()]
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for row in raw_rows:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        label = _keyword_theme_label(f"{title} {row.get('summary') or ''}")
        if not label:
            continue
        capture_date = _parse_yyyymmdd(row.get("raw_date"))
        if capture_date is None:
            continue
        out.append(
            {
                "source_table": row.get("source_table"),
                "capture_date": capture_date,
                "event_type": label,
                "title": title,
                "summary": row.get("summary") or "",
                "polarity": "neutral",
                "importance": _tushare_row_importance(title),
                "source_url": row.get("source_url") or "",
                "ts_code": row.get("ts_code"),
                "target_ts_codes": [row["ts_code"]] if row.get("ts_code") else [],
                "target_sectors": [],
            }
        )
    return out


def _aggregate_theme_rows(
    rows: Sequence[dict[str, Any]],
    *,
    week: dt.date,
    max_themes: int,
    run_mode: str,
    source_policy: str = SOURCE_POLICY_VERSION,
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
                    "source_policy": source_policy,
                    "source_tables": sorted(s for s in bucket["sources"] if s),
                    "source_rows": bucket["rows"],
                    "polarity_counts": bucket["polarity"],
                    "builder": "weekly_cached_theme_heat",
                },
                model_name=None,
                prompt_version=source_policy,
                run_mode=run_mode,
                quality_flag=_quality_flag_for_sources(bucket["sources"]),
            )
        )
    return output


def _theme_label(row: dict[str, Any]) -> str:
    event_type = str(row.get("event_type") or "").strip()
    if event_type:
        return event_type
    title = str(row.get("title") or "").strip()
    summary = str(row.get("summary") or "").strip()
    label = _keyword_theme_label(f"{title} {summary}")
    if label:
        return label
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


def _keyword_theme_label(text_blob: str) -> str:
    for label, keywords in _THEME_KEYWORDS:
        if any(keyword.lower() in text_blob.lower() for keyword in keywords):
            return label
    return ""


def _tushare_row_importance(title: str) -> str:
    if any(k in title for k in ("重大", "重组", "收购", "中标", "合同", "预增", "业绩快报", "回购")):
        return "high"
    if any(k in title for k in ("公告", "报告", "调研", "投资者关系", "业绩")):
        return "medium"
    return "low"


def _parse_yyyymmdd(raw: Any) -> dt.date | None:
    s = str(raw or "").strip()[:8]
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _dedup_source_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("source_table") or ""),
            str(row.get("source_url") or ""),
            str(row.get("title") or ""),
            str(row.get("capture_date") or ""),
            str(row.get("ts_code") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


def _quality_flag_for_sources(sources: set[Any]) -> str:
    names = {str(source or "") for source in sources}
    has_tushare_cache = any(name.startswith("research.api_cache.") for name in names)
    has_local_memory = bool(names - {name for name in names if name.startswith("research.api_cache.")})
    if has_tushare_cache and has_local_memory:
        return "local_news_cache"
    if has_tushare_cache:
        return "tushare_cached"
    return "local_source_cache"


def _compact_fact_row(row: dict[str, Any], fact_id: int) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "source_table": row.get("source_table"),
        "date": str(row.get("capture_date") or row.get("raw_date") or ""),
        "event_type": row.get("event_type"),
        "title": _truncate_text(row.get("title"), 160),
        "summary": _truncate_text(row.get("summary"), 260),
        "polarity": row.get("polarity"),
        "importance": row.get("importance"),
        "source_url": row.get("source_url"),
        "ts_code": row.get("ts_code"),
        "target_ts_codes": list(row.get("target_ts_codes") or [])[:12] if isinstance(row.get("target_ts_codes") or [], list) else [],
        "target_sectors": list(row.get("target_sectors") or [])[:12] if isinstance(row.get("target_sectors") or [], list) else [],
    }


def _truncate_text(value: Any, limit: int) -> str:
    text_value = " ".join(str(value or "").split())
    if len(text_value) <= limit:
        return text_value
    return text_value[: limit - 1] + "..."


def _urls_from_evidence_refs(refs: Any, fact_lookup: dict[int, dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    if not isinstance(refs, list):
        return urls
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("source_url"):
            urls.append(str(ref["source_url"]))
            continue
        try:
            fact_id = int(ref.get("fact_id"))
        except (TypeError, ValueError):
            continue
        fact = fact_lookup.get(fact_id) or {}
        if fact.get("source_url"):
            urls.append(str(fact["source_url"]))
    return sorted(set(urls))


def _llm_quality_flag(raw: dict[str, Any], evidence_quality: str) -> str:
    explicit = str(raw.get("quality_flag") or "").strip()
    if explicit in {"llm_prior_only", "needs_local_evidence", "batch_llm_cache", "local_llm_evidence_cache"}:
        return explicit
    row_quality = str(raw.get("evidence_quality") or evidence_quality)
    if row_quality == "llm_prior_only":
        return "llm_prior_only"
    if row_quality == "needs_local_evidence":
        return "needs_local_evidence"
    return "batch_llm_cache"


def _clip_float(value: Any, *, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    return max(0.0, min(1.0, numeric))
