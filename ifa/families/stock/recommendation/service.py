"""Stock Edge recommendation brief service."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.report.disclaimer import (
    DISCLAIMER_PARAGRAPHS_EN,
    DISCLAIMER_PARAGRAPHS_ZH,
    FOOTER_SHORT_EN,
    FOOTER_SHORT_ZH,
    SHORT_HEADER_EN,
    SHORT_HEADER_ZH,
)
from ifa.core.report.timezones import BJT, bjt_now
from ifa.families.stock.context import resolve_as_of_trade_date
from .models import (
    LOGIC_VERSION,
    RecommendationBriefReport,
    RecommendationBriefRequest,
    RecommendationCandidate,
    RecommendationEvidence,
)


def resolve_recommendation_trade_date(
    engine: Engine,
    *,
    as_of: dt.date | None = None,
    requested_at: dt.datetime | None = None,
) -> tuple[dt.date, str, dt.datetime]:
    """Resolve the last fully closed trading day for a recommendation brief.

    Explicit `--as-of` is interpreted as a desired observation date, not a
    permission to read incomplete current-day data.  If it is today before the
    Stock Edge market-close cutoff, or a non-trading day, the same completed-day
    resolver used by single-stock Stock Edge clamps it to the latest closed day.
    """
    if as_of is None:
        ctx = resolve_as_of_trade_date(requested_at=requested_at, engine=engine)
        return ctx.as_of_trade_date, ctx.rule, ctx.data_cutoff_at_bjt

    requested_bjt = dt.datetime.combine(as_of, dt.time(15, 1), tzinfo=BJT)
    now_bjt = (requested_at.astimezone(BJT) if requested_at and requested_at.tzinfo else requested_at.replace(tzinfo=BJT) if requested_at else bjt_now())
    if as_of >= now_bjt.date():
        requested_bjt = now_bjt
    ctx = resolve_as_of_trade_date(requested_at=requested_bjt, engine=engine)
    return ctx.as_of_trade_date, f"explicit_{ctx.rule}", ctx.data_cutoff_at_bjt


def build_recommendation_brief(request: RecommendationBriefRequest, *, engine: Engine) -> RecommendationBriefReport:
    trade_date, as_of_rule, cutoff_bjt = resolve_recommendation_trade_date(
        engine,
        as_of=request.as_of,
        requested_at=request.requested_at,
    )
    source_status = _source_status(engine, trade_date)
    rows = _load_sector_cycle_rows(engine, trade_date) if source_status["stock.sector_cycle_leader_daily"]["available"] else []
    if not rows:
        rows = _load_fallback_rows(engine, trade_date, source_status)

    groups = {"strong": [], "watchlist": [], "avoid": []}
    seen: set[str] = set()
    for row in rows:
        if row["ts_code"] in seen:
            continue
        candidate = _candidate_from_row(row, trade_date)
        seen.add(candidate.ts_code)
        if len(groups[candidate.group]) < request.limit_per_group:
            groups[candidate.group].append(candidate)
        if all(len(items) >= request.limit_per_group for items in groups.values()):
            break

    generated = bjt_now()
    title = f"Stock Edge 推荐简报 · {trade_date:%Y年%m月%d日}"
    return RecommendationBriefReport(
        title=title,
        as_of_trade_date=trade_date,
        generated_at_bjt=generated.strftime("%Y-%m-%d %H:%M:%S %Z"),
        data_cutoff_bjt=cutoff_bjt.strftime("%Y-%m-%d %H:%M:%S %Z"),
        run_mode=request.run_mode,
        as_of_rule=as_of_rule,
        logic_version=LOGIC_VERSION,
        groups=groups,  # type: ignore[arg-type]
        source_status=source_status,
        audit={
            "candidate_count": sum(len(v) for v in groups.values()),
            "selection_order": [
                "stock.sector_cycle_leader_daily",
                "stock.risk_veto_daily",
                "ta.candidates_daily",
                "ningbo.recommendations_daily",
                "outcome proxy artifacts",
            ],
        },
        disclaimer={
            "short_header_zh": SHORT_HEADER_ZH,
            "short_header_en": SHORT_HEADER_EN,
            "footer_short_zh": FOOTER_SHORT_ZH,
            "footer_short_en": FOOTER_SHORT_EN,
            "paragraphs_zh": list(DISCLAIMER_PARAGRAPHS_ZH),
            "paragraphs_en": list(DISCLAIMER_PARAGRAPHS_EN),
        },
    )


def _source_status(engine: Engine, trade_date: dt.date) -> dict[str, dict[str, Any]]:
    specs = {
        "stock.sector_cycle_leader_daily": ("stock", "sector_cycle_leader_daily", "trade_date"),
        "stock.risk_veto_daily": ("stock", "risk_veto_daily", "trade_date"),
        "ta.candidates_daily": ("ta", "candidates_daily", "trade_date"),
        "ningbo.recommendations_daily": ("ningbo", "recommendations_daily", "rec_date"),
        "stock.theme_heat_weekly": ("stock", "theme_heat_weekly", None),
    }
    out: dict[str, dict[str, Any]] = {}
    with engine.connect() as conn:
        for name, (schema, table, date_col) in specs.items():
            exists = bool(conn.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"{schema}.{table}"}).scalar_one())
            rows = None
            latest = None
            if exists and date_col:
                row = conn.execute(
                    text(f"SELECT count(*) AS rows, max({date_col}) AS latest FROM {schema}.{table} WHERE {date_col} = :d"),
                    {"d": trade_date},
                ).mappings().one()
                rows = int(row["rows"] or 0)
                latest = row["latest"].isoformat() if row["latest"] else None
            elif exists:
                latest_val = conn.execute(text(f"SELECT max(valid_week) FROM {schema}.{table} WHERE valid_week <= :d"), {"d": trade_date}).scalar_one_or_none()
                rows = int(conn.execute(text(f"SELECT count(*) FROM {schema}.{table} WHERE valid_week = :w"), {"w": latest_val}).scalar_one() or 0) if latest_val else 0
                latest = latest_val.isoformat() if latest_val else None
            out[name] = {"available": exists and (rows is None or rows > 0), "table_exists": exists, "rows": rows, "latest": latest}
    out["stock_edge.outcome_proxy_artifacts"] = {
        "available": False,
        "table_exists": False,
        "rows": 0,
        "latest": None,
        "reason": "MVP does not scan parquet/json tuning artifacts for report generation.",
    }
    return out


def _load_sector_cycle_rows(engine: Engine, trade_date: dt.date) -> list[dict[str, Any]]:
    sql = text("""
        WITH rv AS (
            SELECT ts_code,
                   bool_or(hard_veto) AS hard_veto,
                   string_agg(DISTINCT veto_category, ', ' ORDER BY veto_category) AS veto_categories,
                   string_agg(DISTINCT COALESCE(reason, severity, veto_category), '; ' ORDER BY COALESCE(reason, severity, veto_category)) AS veto_reasons
            FROM stock.risk_veto_daily
            WHERE trade_date = :d
            GROUP BY ts_code
        ),
        ta AS (
            SELECT ts_code,
                   max(final_score::float) AS ta_score,
                   array_agg(setup_name ORDER BY final_score DESC NULLS LAST, setup_name) AS ta_setups,
                   bool_or(in_top_watchlist) AS ta_watchlist
            FROM ta.candidates_daily
            WHERE trade_date = :d
            GROUP BY ts_code
        ),
        nb AS (
            SELECT ts_code,
                   max(confidence_score::float) AS ningbo_score,
                   array_agg(DISTINCT scoring_mode ORDER BY scoring_mode) AS ningbo_modes
            FROM ningbo.recommendations_daily
            WHERE rec_date = :d
            GROUP BY ts_code
        ),
        px AS (
            SELECT DISTINCT ON (ts_code)
                   ts_code, close::float AS close, high::float AS high, low::float AS low, pct_chg::float AS pct_chg
            FROM smartmoney.raw_daily
            WHERE trade_date = :d
            ORDER BY ts_code
        ),
        joined AS (
            SELECT s.*, rv.hard_veto, rv.veto_categories, rv.veto_reasons,
                   ta.ta_score, ta.ta_setups, ta.ta_watchlist,
                   nb.ningbo_score, nb.ningbo_modes,
                   px.close, px.high, px.low, px.pct_chg
            FROM stock.sector_cycle_leader_daily s
            LEFT JOIN rv ON rv.ts_code=s.ts_code
            LEFT JOIN ta ON ta.ts_code=s.ts_code
            LEFT JOIN nb ON nb.ts_code=s.ts_code
            LEFT JOIN px ON px.ts_code=s.ts_code
            WHERE s.trade_date = :d
        ),
        ranked AS (
            SELECT *,
                   row_number() OVER (ORDER BY sector_score DESC NULLS LAST, leader_score DESC NULLS LAST, rank_in_sector ASC, ts_code) AS rn_top,
                   row_number() OVER (ORDER BY leader_score ASC NULLS FIRST, sector_score ASC NULLS FIRST, ts_code) AS rn_weak,
                   row_number() OVER (ORDER BY abs(COALESCE(leader_score, 0.0) - 0.56), ts_code) AS rn_middle
            FROM joined
        )
        SELECT *
        FROM ranked
        WHERE rn_top <= 220
           OR rn_weak <= 100
           OR rn_middle <= 120
           OR COALESCE(hard_veto, false)
        ORDER BY rn_top ASC, rn_weak ASC, ts_code
    """)
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(sql, {"d": trade_date}).mappings().all()]


def _load_fallback_rows(engine: Engine, trade_date: dt.date, source_status: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if source_status.get("ta.candidates_daily", {}).get("available"):
        sql = text("""
            SELECT trade_date, ts_code, NULL AS name, NULL AS l1_name, NULL AS l2_name,
                   NULL::int AS rank_in_sector, NULL::int AS sector_rank_count,
                   final_score::float AS leader_score, NULL::float AS sector_score, final_score::float AS stock_score,
                   'ta_fallback' AS quality_flag, '{}'::jsonb AS evidence_json,
                   false AS hard_veto, NULL AS veto_categories, NULL AS veto_reasons,
                   final_score::float AS ta_score, ARRAY[setup_name] AS ta_setups, in_top_watchlist AS ta_watchlist,
                   NULL::float AS ningbo_score, NULL::text[] AS ningbo_modes,
                   NULL::float AS close, NULL::float AS high, NULL::float AS low, NULL::float AS pct_chg
            FROM ta.candidates_daily
            WHERE trade_date = :d
            ORDER BY in_top_watchlist DESC, final_score DESC NULLS LAST, rank NULLS LAST
            LIMIT 80
        """)
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(sql, {"d": trade_date}).mappings().all()]
    if source_status.get("ningbo.recommendations_daily", {}).get("available"):
        sql = text("""
            SELECT rec_date AS trade_date, ts_code, NULL AS name, NULL AS l1_name, NULL AS l2_name,
                   NULL::int AS rank_in_sector, NULL::int AS sector_rank_count,
                   confidence_score::float AS leader_score, NULL::float AS sector_score, confidence_score::float AS stock_score,
                   'ningbo_fallback' AS quality_flag, '{}'::jsonb AS evidence_json,
                   false AS hard_veto, NULL AS veto_categories, NULL AS veto_reasons,
                   NULL::float AS ta_score, NULL::text[] AS ta_setups, false AS ta_watchlist,
                   confidence_score::float AS ningbo_score, ARRAY[scoring_mode] AS ningbo_modes,
                   NULL::float AS close, NULL::float AS high, NULL::float AS low, NULL::float AS pct_chg
            FROM ningbo.recommendations_daily
            WHERE rec_date = :d
            ORDER BY confidence_score DESC, ts_code
            LIMIT 80
        """)
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(sql, {"d": trade_date}).mappings().all()]
    return []


def _candidate_from_row(row: dict[str, Any], trade_date: dt.date) -> RecommendationCandidate:
    evidence_json = _as_dict(row.get("evidence_json"))
    score = _float(row.get("leader_score"))
    sector_score = _float(row.get("sector_score"))
    stock_score = _float(row.get("stock_score"))
    hard_veto = bool(row.get("hard_veto"))
    quality = str(row.get("quality_flag") or "")
    risk_flags = _risk_flags(evidence_json)
    group = _classify_group(row, risk_flags)
    risk_notes = _risk_notes(row, risk_flags)
    conflicts = _conflicts(row, risk_flags)
    evidence = _evidence_points(row, evidence_json, trade_date)
    return RecommendationCandidate(
        ts_code=str(row["ts_code"]),
        name=row.get("name"),
        group=group,  # type: ignore[arg-type]
        l1_name=row.get("l1_name"),
        l2_name=row.get("l2_name"),
        rank_in_sector=_int(row.get("rank_in_sector")),
        sector_rank_count=_int(row.get("sector_rank_count")),
        leader_score=score,
        sector_score=sector_score,
        stock_score=stock_score,
        quality_flag=quality or None,
        horizon_suitability=_horizon_suitability(score, sector_score, stock_score, hard_veto, risk_flags),
        trigger=_trigger(row),
        invalidation=_invalidation(row),
        evidence=evidence,
        conflicts=conflicts,
        risk_notes=risk_notes,
        source_flags={
            "hard_veto": hard_veto,
            "risk_flags": risk_flags,
            "ta_watchlist": bool(row.get("ta_watchlist")),
            "fallback": quality in {"ta_fallback", "ningbo_fallback"},
        },
    )


def _classify_group(row: dict[str, Any], risk_flags: list[str]) -> str:
    score = _float(row.get("leader_score")) or 0.0
    sector = _float(row.get("sector_score")) or 0.0
    rank = _int(row.get("rank_in_sector"))
    quality = str(row.get("quality_flag") or "")
    if row.get("hard_veto") or {"distribution_risk", "leader_crowded", "retail_chase"} & set(risk_flags):
        return "avoid"
    if quality == "degraded" and score < 0.70:
        return "watchlist"
    if rank is not None and rank <= 3 and score >= 0.68 and (sector >= 0.56 or sector == 0.0):
        return "strong"
    if score >= 0.74:
        return "strong"
    if score <= 0.42:
        return "avoid"
    return "watchlist"


def _horizon_suitability(score: float | None, sector: float | None, stock: float | None, hard_veto: bool, risk_flags: list[str]) -> dict[str, str]:
    if hard_veto:
        return {"5d": "不适合", "10d": "不适合", "20d": "不适合"}
    score = score or 0.0
    sector = sector or 0.0
    stock = stock or 0.0
    crowded = bool({"leader_crowded", "retail_chase"} & set(risk_flags))
    return {
        "5d": "适合" if score >= 0.68 and stock >= 0.58 and not crowded else ("观察" if score >= 0.50 else "不适合"),
        "10d": "适合" if score >= 0.64 and sector >= 0.54 and not crowded else ("观察" if score >= 0.48 else "不适合"),
        "20d": "适合" if score >= 0.70 and sector >= 0.60 and not crowded else ("观察" if sector >= 0.50 else "不适合"),
    }


def _trigger(row: dict[str, Any]) -> str:
    high = _float(row.get("high"))
    close = _float(row.get("close"))
    ta_setups = row.get("ta_setups") or []
    setup_note = f"；TA 触发 {', '.join(ta_setups[:2])}" if ta_setups else ""
    if high:
        return f"下一交易日放量站稳或回踩不破上一交易日高点附近 {high:.2f}{setup_note}。"
    if close:
        return f"下一交易日保持在上一交易日收盘价 {close:.2f} 上方并有资金延续{setup_note}。"
    return f"等待下一交易日价格与主力资金同步确认{setup_note}。"


def _invalidation(row: dict[str, Any]) -> str:
    low = _float(row.get("low"))
    if row.get("hard_veto"):
        return f"存在硬风险屏蔽：{row.get('veto_reasons') or row.get('veto_categories') or '本地风险表命中'}。"
    if low:
        return f"跌破上一交易日低点 {low:.2f} 且主力资金转弱，先按失效处理。"
    return "板块扩散转弱、主力净流入消失或风险表新增硬屏蔽时失效。"


def _evidence_points(row: dict[str, Any], evidence_json: dict[str, Any], trade_date: dt.date) -> list[RecommendationEvidence]:
    out = [
        RecommendationEvidence("板块内排名", _rank_text(row), "stock.sector_cycle_leader_daily"),
        RecommendationEvidence("综合 leader score", _round(row.get("leader_score")), "stock.sector_cycle_leader_daily"),
    ]
    if row.get("sector_score") is not None:
        out.append(RecommendationEvidence("板块周期分", _round(row.get("sector_score")), "stock.sector_cycle_leader_daily"))
    if evidence_json.get("main_net_yuan") is not None:
        out.append(RecommendationEvidence("主力净流入", _fmt_yuan(evidence_json.get("main_net_yuan")), "sme.sme_stock_orderflow_daily"))
    if evidence_json.get("diffusion_phase"):
        out.append(RecommendationEvidence("板块扩散阶段", evidence_json.get("diffusion_phase"), "sme.sme_sector_diffusion_daily"))
    if row.get("ta_score") is not None:
        out.append(RecommendationEvidence("TA 候选", f"{_round(row.get('ta_score'))} / {', '.join((row.get('ta_setups') or [])[:3])}", "ta.candidates_daily"))
    if row.get("ningbo_score") is not None:
        out.append(RecommendationEvidence("Ningbo 推荐", _round(row.get("ningbo_score")), "ningbo.recommendations_daily"))
    if row.get("veto_categories"):
        out.append(RecommendationEvidence("风险屏蔽", row.get("veto_categories"), "stock.risk_veto_daily", note=row.get("veto_reasons")))
    if not out:
        out.append(RecommendationEvidence("可用证据", f"{trade_date.isoformat()} 无结构化证据", "local"))
    return out


def _risk_notes(row: dict[str, Any], risk_flags: list[str]) -> list[str]:
    notes: list[str] = []
    if row.get("hard_veto"):
        notes.append(str(row.get("veto_reasons") or row.get("veto_categories") or "硬风险屏蔽"))
    if row.get("quality_flag") == "degraded":
        notes.append("底层行情/资金覆盖质量为 degraded，不能按满置信度使用。")
    notes.extend(risk_flags)
    return notes


def _conflicts(row: dict[str, Any], risk_flags: list[str]) -> list[str]:
    conflicts: list[str] = []
    if _float(row.get("leader_score")) and _float(row.get("leader_score")) >= 0.68 and row.get("quality_flag") == "degraded":
        conflicts.append("leader 排名较强，但底层数据质量降级。")
    if row.get("ta_score") is None:
        conflicts.append("未命中当日 TA 候选，执行触发需要下一交易日确认。")
    if {"leader_crowded", "retail_chase"} & set(risk_flags):
        conflicts.append("板块/个股热度可能偏拥挤，追高风险高于普通候选。")
    return conflicts


def _risk_flags(evidence_json: dict[str, Any]) -> list[str]:
    raw = evidence_json.get("risk_flags_json")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            return [raw]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _rank_text(row: dict[str, Any]) -> str:
    rank = _int(row.get("rank_in_sector"))
    total = _int(row.get("sector_rank_count"))
    if rank and total:
        return f"{rank}/{total}"
    return "无板块排名"


def _fmt_yuan(value: Any) -> str:
    v = _float(value)
    if v is None:
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f} 万"
    return f"{v:.0f} 元"


def _round(value: Any) -> str:
    v = _float(value)
    return "—" if v is None else f"{v:.3f}"


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
