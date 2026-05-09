"""Research, fundamentals, news and theme perspective adapter."""
from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from ifa.families.stock.diagnostic.models import EvidencePoint, PerspectiveEvidence

from .common import freshness_from_points, query_dicts, timed


def collect(*, engine: Engine, snapshot: Any) -> PerspectiveEvidence:
    return timed("research_news", lambda: _collect(engine, snapshot))


def _collect(engine: Engine, snapshot: Any) -> PerspectiveEvidence:
    lineup = snapshot.research_lineup.data or {}
    events = (snapshot.event_context.data or {}).get("company_events") or []
    catalysts = (snapshot.event_context.data or {}).get("catalyst_events") or []
    theme_heat = _load_theme_heat(engine, snapshot.ctx.as_of.as_of_trade_date)
    sector = snapshot.sector_membership.data or {}
    ts_code = snapshot.ctx.request.ts_code
    points: list[EvidencePoint] = []
    for key, label in [("annual_factors", "annual fundamentals"), ("quarterly_factors", "quarterly fundamentals"), ("recent_research_reports", "recent sell-side reports")]:
        values = lineup.get(key) or []
        if values:
            points.append(EvidencePoint(label, len(values), "research.period_factor_decomposition/pdf_extract_cache"))
    for row in [*events[:3], *catalysts[:3]]:
        points.append(EvidencePoint(row.get("event_type") or "event", row.get("polarity"), "research/ta event memory", str(row.get("capture_date")), note=row.get("title") or row.get("summary")))
    theme_hits = []
    for row in theme_heat[:5]:
        hit = _theme_hit(row, ts_code, sector)
        if hit:
            theme_hits.append(row)
        label = f"weekly theme #{row.get('theme_rank')}"
        value = row.get("theme_label")
        note = f"quality={row.get('quality_flag')} heat={row.get('heat_score')}"
        if hit:
            note += f" theme_hit={hit}"
        points.append(EvidencePoint(label, value, "stock.theme_heat_weekly", str(row.get("valid_week")), note=note))
    if not points:
        return PerspectiveEvidence("research_news", "Research / Fundamentals / News", "unavailable", "unknown", "未找到可复用的基本面、公告/新闻或主题热度证据。", missing=["research memory", "event memory", "stock.theme_heat_weekly"])
    view = "neutral"
    polarities = [str(row.get("polarity") or "").lower() for row in [*events, *catalysts]]
    if any(p in {"positive", "bullish"} for p in polarities):
        view = "positive"
    if any(p in {"negative", "bearish"} for p in polarities):
        view = "negative"
    summary = "已收集基本面/事件/主题热度证据；stub 主题热度不作为 alpha 证据。"
    if theme_hits:
        summary = f"命中 {len(theme_hits)} 个周度主题缓存；仍需区分人工/LLM缓存质量。"
    return PerspectiveEvidence("research_news", "Research / Fundamentals / News / Theme", "partial", view, summary, points=points, freshness=freshness_from_points(points), raw={"theme_heat": theme_heat, "theme_hits": theme_hits})  # type: ignore[arg-type]


def _load_theme_heat(engine: Engine, as_of) -> list[dict[str, Any]]:
    from ifa.families.stock.theme_heat import week_start

    return query_dicts(engine, """
        SELECT valid_week, theme_rank, theme_label, category, heat_score,
               confidence, affected_sectors_json, representative_stocks_json,
               quality_flag
        FROM stock.theme_heat_weekly
        WHERE valid_week <= :week
        ORDER BY valid_week DESC, theme_rank
        LIMIT 5
    """, {"week": week_start(as_of)})


def _theme_hit(row: dict[str, Any], ts_code: str, sector: dict[str, Any]) -> str | None:
    sectors = row.get("affected_sectors_json") or []
    stocks = row.get("representative_stocks_json") or []
    l1 = str(sector.get("l1_code") or sector.get("l1_name") or "")
    l2 = str(sector.get("l2_code") or sector.get("l2_name") or "")
    for stock in stocks if isinstance(stocks, list) else []:
        if isinstance(stock, dict) and ts_code in {str(stock.get("ts_code")), str(stock.get("code"))}:
            return "stock"
    for item in sectors if isinstance(sectors, list) else []:
        if not isinstance(item, dict):
            continue
        values = {str(item.get(k) or "") for k in ("l1_code", "l1_name", "l2_code", "l2_name", "sector_code", "sector_name")}
        if l1 in values or l2 in values:
            return "sector"
    return None
