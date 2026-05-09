"""Read-only Stock Edge diagnostic service."""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.report.timezones import bjt_now
from ifa.families.stock.context import StockEdgeRequest, build_context
from ifa.families.stock.data.availability import LoadResult
from ifa.families.stock.data.gateway import LocalDataGateway
from ifa.families.stock.data.snapshot import StockEdgeSnapshot
from ifa.families.stock.features import compute_technical_summary

from .models import (
    DiagnosticReport,
    DiagnosticRequest,
    DiagnosticSynthesis,
    EvidencePoint,
    PerspectiveEvidence,
)


def build_diagnostic_report(request: DiagnosticRequest, *, engine: Engine) -> DiagnosticReport:
    ctx = build_context(
        StockEdgeRequest(
            ts_code=request.ts_code,
            requested_at=request.requested_at,
            run_mode=request.run_mode,  # type: ignore[arg-type]
        ),
        engine=engine,
    )
    snapshot = _build_light_snapshot(ctx, engine)
    matrix: dict[str, Any] | None = None
    decision_layer: dict[str, Any] | None = None
    matrix_error: str | None = None
    if request.include_full_stock_edge:
        try:
            from ifa.families.stock.decision_layer import build_decision_layer
            from ifa.families.stock.strategies import build_rule_baseline_plan, compute_strategy_matrix

            matrix = compute_strategy_matrix(snapshot)
            plan = build_rule_baseline_plan(snapshot)
            decision_layer = build_decision_layer(snapshot, plan, strategy_matrix=matrix)
        except Exception as exc:  # noqa: BLE001 - diagnostic must degrade rather than fail whole report
            matrix_error = f"{type(exc).__name__}: {exc}"

    perspectives = [
        _safe("stock_edge_sector_cycle", lambda: _stock_edge_perspective(engine, snapshot, matrix, decision_layer, matrix_error, include_full=request.include_full_stock_edge)),
        _safe("ta", lambda: _ta_perspective(snapshot)),
        _safe("ningbo", lambda: _ningbo_perspective(engine, ctx.request.ts_code, ctx.as_of.as_of_trade_date)),
        _safe("research_news", lambda: _research_perspective(engine, snapshot)),
        _safe("risk", lambda: _risk_perspective(engine, snapshot)),
    ]
    perspectives = [_with_quality(p, ctx.as_of.as_of_trade_date) for p in perspectives]
    synthesis = synthesize_diagnostic(perspectives)
    return DiagnosticReport(
        ts_code=ctx.request.ts_code,
        name=_resolve_name(snapshot),
        as_of_trade_date=ctx.as_of.as_of_trade_date,
        generated_at_bjt=bjt_now().isoformat(),
        data_cutoff_bjt=ctx.as_of.data_cutoff_at_bjt.isoformat(),
        perspectives=perspectives,
        synthesis=synthesis,
        audit={
            "as_of_rule": ctx.as_of.rule,
            "param_hash": ctx.param_hash,
            "freshness": snapshot.freshness,
            "degraded_reasons": snapshot.degraded_reasons,
            "db_schema_plan": "If JSON artifacts become insufficient, add stock.diagnostic_runs(run_id, ts_code, name, requested_at, generated_at, as_of_trade_date, conclusion, confidence, output_paths_json, perspective_status_json, evidence_freshness_json) and stock.diagnostic_evidence(run_id, perspective_key, source_table, as_of, freshness_status, payload_json).",
        },
    )


def _build_light_snapshot(ctx, engine: Engine) -> StockEdgeSnapshot:
    """Load only evidence needed by the diagnostic MVP.

    The production Stock Edge report uses `build_local_snapshot()`, which also
    probes optional intraday and reused model contexts.  Those are valuable for
    full reports but too slow for customer-facing ad hoc diagnostics, so the MVP
    keeps this read path narrow and marks absent optional evidence explicitly.
    """
    gateway = LocalDataGateway(engine)
    runtime = ctx.params.get("runtime", {})
    default_window = int(runtime.get("default_lookback_days", 7))
    technical_window = int(ctx.params.get("data", {}).get("technical_lookback_days", 60))
    daily_window = max(default_window, technical_window)
    as_of = ctx.as_of.as_of_trade_date
    ts_code = ctx.request.ts_code
    return StockEdgeSnapshot(
        ctx=ctx,
        daily_bars=gateway.load_daily_bars(ts_code, as_of, lookback_rows=daily_window, min_rows=default_window),
        daily_basic=gateway.load_daily_basic(ts_code, as_of, lookback_rows=max(default_window, 7), min_rows=default_window),
        moneyflow=gateway.load_moneyflow(ts_code, as_of, lookback_rows=max(default_window, 7), min_rows=3),
        event_context=_load_light_event_context(engine, ts_code, as_of),
        sector_membership=_load_light_sector_membership(engine, ts_code, as_of),
        ta_context=_load_light_ta_context(engine, ts_code, as_of),
        research_lineup=_load_light_research_lineup(engine, ts_code),
        model_context=LoadResult(
            name="model_context",
            data=None,
            source="missing",
            status="missing",
            rows=0,
            required=False,
            message="Skipped in lightweight diagnostic MVP; use full Stock Edge report for reused model context.",
        ),
        intraday_5min=LoadResult(
            name="intraday_5min",
            data=None,
            source="missing",
            status="missing",
            rows=0,
            required=False,
            message="Skipped in lightweight diagnostic MVP.",
        ),
    )


def _load_light_sector_membership(engine: Engine, ts_code: str, as_of: dt.date) -> LoadResult:
    snapshot_month = as_of.replace(day=1)
    rows = _query_dicts(engine, """
        SELECT snapshot_month, l1_code, l1_name, l2_code, l2_name, name
        FROM smartmoney.sw_member_monthly
        WHERE ts_code=:ts_code AND snapshot_month <= :snapshot_month
        ORDER BY snapshot_month DESC
        LIMIT 1
    """, {"ts_code": ts_code, "snapshot_month": snapshot_month})
    if not rows:
        return LoadResult("sector_membership", None, "missing", "missing", 0, required=False, message=f"No SW membership found for {ts_code}.")
    return LoadResult("sector_membership", rows[0], "postgres", "ok", 1, as_of=rows[0].get("snapshot_month"), required=False)


def _load_light_ta_context(engine: Engine, ts_code: str, as_of: dt.date) -> LoadResult:
    candidates = _query_dicts(engine, """
        SELECT trade_date, setup_name, rank, final_score, star_rating,
               regime_at_gen, evidence_json, entry_price, stop_loss,
               target_price, rr_ratio, price_basis
        FROM ta.candidates_daily
        WHERE ts_code=:ts_code AND trade_date <= :as_of
        ORDER BY trade_date DESC, rank NULLS LAST
        LIMIT 10
    """, {"ts_code": ts_code, "as_of": as_of})
    warnings = _query_dicts(engine, """
        SELECT trade_date, setup_name, score, triggers, evidence,
               regime_at_gen, sector_role, sector_cycle_phase
        FROM ta.warnings_daily
        WHERE ts_code=:ts_code AND trade_date <= :as_of
        ORDER BY trade_date DESC, score DESC
        LIMIT 10
    """, {"ts_code": ts_code, "as_of": as_of})
    regime_rows = _query_dicts(engine, """
        SELECT trade_date, regime, confidence, evidence_json, transitions_json
        FROM ta.regime_daily
        WHERE trade_date <= :as_of
        ORDER BY trade_date DESC
        LIMIT 1
    """, {"as_of": as_of})
    from ifa.families.stock.data.gateway import _decorate_ta_row

    data = {
        "candidates": [_decorate_ta_row(row) for row in candidates],
        "warnings": [_decorate_ta_row(row) for row in warnings],
        "regime": regime_rows[0] if regime_rows else None,
        "setup_metrics": [],
    }
    rows = len(candidates) + len(warnings) + len(regime_rows)
    return LoadResult(
        "ta_context",
        data if rows else None,
        "postgres" if rows else "missing",
        "ok" if rows else "missing",
        rows,
        as_of=as_of if rows else None,
        required=False,
        message=None if rows else f"No TA context found for {ts_code}.",
    )


def _load_light_research_lineup(engine: Engine, ts_code: str) -> LoadResult:
    annual = _query_dicts(engine, """
        SELECT factor_family, factor_name, period, period_type, value, unit
        FROM research.period_factor_decomposition
        WHERE ts_code=:ts_code AND period_type='annual'
        ORDER BY period DESC
        LIMIT 20
    """, {"ts_code": ts_code})
    quarterly = _query_dicts(engine, """
        SELECT factor_family, factor_name, period, period_type, value, unit
        FROM research.period_factor_decomposition
        WHERE ts_code=:ts_code AND period_type='quarterly'
        ORDER BY period DESC
        LIMIT 20
    """, {"ts_code": ts_code})
    reports = _query_dicts(engine, """
        SELECT run_id, report_type, latest_period, data_cutoff_bjt,
               output_html_path, output_json_path
        FROM research.report_runs
        WHERE ts_code=:ts_code AND status='succeeded'
        ORDER BY data_cutoff_bjt DESC
        LIMIT 5
    """, {"ts_code": ts_code})
    data = {"annual_factors": annual, "quarterly_factors": quarterly, "recent_research_reports": reports}
    rows = len(annual) + len(quarterly) + len(reports)
    return LoadResult(
        "research_lineup",
        data if rows else None,
        "postgres" if rows else "missing",
        "ok" if rows else "missing",
        rows,
        required=False,
        message=None if rows else f"No Research lineup found for {ts_code}.",
    )


def _load_light_event_context(engine: Engine, ts_code: str, as_of: dt.date) -> LoadResult:
    company_events = _query_dicts(engine, """
        SELECT capture_date, event_type, title, summary, polarity,
               importance, source_url, publish_time, extraction_model
        FROM research.company_event_memory
        WHERE ts_code=:ts_code AND capture_date <= :as_of
        ORDER BY capture_date DESC, importance DESC NULLS LAST
        LIMIT 10
    """, {"ts_code": ts_code, "as_of": as_of})
    catalyst_events = _query_dicts(engine, """
        SELECT capture_date, event_type, title, summary, polarity,
               importance, source_url, publish_time, extraction_model,
               target_ts_codes, target_sectors
        FROM ta.catalyst_event_memory
        WHERE :ts_code = ANY(target_ts_codes) AND capture_date <= :as_of
        ORDER BY capture_date DESC, importance DESC NULLS LAST
        LIMIT 10
    """, {"ts_code": ts_code, "as_of": as_of})
    data = {
        "top_list": [],
        "top_inst": [],
        "kpl": [],
        "limit_list": [],
        "block_trade": [],
        "market_margin": [],
        "northbound": [],
        "company_events": company_events,
        "catalyst_events": catalyst_events,
    }
    rows = len(company_events) + len(catalyst_events)
    return LoadResult(
        "event_context",
        data,
        "postgres",
        "ok",
        rows,
        as_of=max([r.get("capture_date") for r in [*company_events, *catalyst_events] if r.get("capture_date")], default=None),
        required=False,
        message=None if rows else "No recent company/catalyst events found; treated as neutral.",
    )


def synthesize_diagnostic(perspectives: list[PerspectiveEvidence]) -> DiagnosticSynthesis:
    risk = next((p for p in perspectives if p.key == "risk"), None)
    stock = next((p for p in perspectives if p.key == "stock_edge_sector_cycle"), None)
    ta = next((p for p in perspectives if p.key == "ta"), None)
    ningbo = next((p for p in perspectives if p.key == "ningbo"), None)
    research = next((p for p in perspectives if p.key == "research_news"), None)

    hard_risk = risk is not None and risk.view == "risk"
    positive_count = sum(1 for p in (stock, ta, ningbo, research) if p and p.view == "positive")
    negative_count = sum(1 for p in (stock, ta, ningbo, research) if p and p.view in {"negative", "risk"})
    conflict_notes = _collect_conflicts([p for p in perspectives if p is not None])
    key_perspectives = [p for p in (stock, ta, research, risk) if p is not None]
    weak_quality = [
        p.title
        for p in key_perspectives
        if p.status in {"unavailable", "error"}
        or (bool(p.freshness) and p.freshness_status in {"stale", "unavailable"})
    ]

    if hard_risk:
        conclusion = "avoid"
    elif stock and "拥挤" in stock.summary and positive_count > 0:
        conclusion = "overheated"
    elif positive_count >= 2 and negative_count == 0 and not conflict_notes:
        conclusion = "short-term tradable"
    elif positive_count >= 1:
        conclusion = "wait for pullback" if ta and ta.view != "positive" else "watch only"
    else:
        conclusion = "watch only" if negative_count == 0 else "avoid"

    confidence = "medium" if positive_count + negative_count >= 2 and not conflict_notes else "low"
    if hard_risk:
        confidence = "high"
    elif weak_quality and confidence == "medium":
        confidence = "low"

    horizon = {"5d": "neutral", "10d": "neutral", "20d": "neutral"}
    if stock and stock.raw.get("horizon_decisions"):
        for key in horizon:
            decision = stock.raw["horizon_decisions"].get(key) or {}
            horizon[key] = str(decision.get("user_facing_label") or decision.get("decision") or "neutral")
    elif positive_count:
        horizon.update({"5d": "watch", "10d": "watch", "20d": "neutral"})
    if hard_risk:
        horizon = {k: "avoid" for k in horizon}

    rationale = [p.summary for p in perspectives if p.status != "unavailable" and p.summary][:5]
    if weak_quality:
        rationale.append(f"置信度已因关键视角证据 stale/unavailable 下调: {', '.join(weak_quality)}.")
    return DiagnosticSynthesis(
        conclusion=conclusion,  # type: ignore[arg-type]
        confidence=confidence,
        horizon_suitability=horizon,
        trigger=_first_point_note(ta, "trigger") or _first_point_note(stock, "trigger") or "等待价格、成交额和主力净流入同步确认。",
        invalidation=_first_point_note(ta, "invalidation") or _first_point_note(stock, "invalidation") or "跌破近期关键支撑或出现硬性风控事件。",
        time_window="优先按 5/10/20 个交易日滚动复核，不把单次诊断当长期评级。",
        position_risk="低置信或冲突证据下只适合小仓试错；硬风险命中时不建议新开仓。",
        conflicts=conflict_notes,
        rationale=rationale,
    )


def _stock_edge_perspective(
    engine: Engine,
    snapshot,
    matrix: dict[str, Any] | None,
    decision_layer: dict[str, Any] | None,
    matrix_error: str | None,
    *,
    include_full: bool,
) -> PerspectiveEvidence:
    sector = snapshot.sector_membership.data or {}
    sme = _load_sme_sector_cycle(engine, snapshot.ctx.request.ts_code, snapshot.ctx.as_of.as_of_trade_date, sector)
    points: list[EvidencePoint] = []
    missing: list[str] = []
    if sector:
        points.append(EvidencePoint("SW L2", sector.get("l2_name"), "smartmoney.sw_member_monthly", str(sector.get("snapshot_month"))))
    else:
        missing.append("SW L2 membership")
    if sme:
        for key, label in [
            ("current_state", "sector cycle stage"),
            ("diffusion_phase", "sector diffusion"),
            ("main_net_ratio", "sector main-money ratio"),
            ("retail_net_ratio", "sector retail ratio"),
            ("leader_ts_code", "sector leader"),
            ("leader_score", "target leader score"),
            ("risk_flags_json", "sector heat/crowding flags"),
        ]:
            if sme.get(key) is not None:
                points.append(EvidencePoint(label, sme.get(key), sme.get("_source", "sme"), sme.get("trade_date")))
        if sme.get("is_sector_leader") is False:
            missing.append("stock-specific sector_cycle_leader replay/proxy rank is not persisted yet; implement stock.sector_cycle_leader_daily before treating leader evidence as target-specific alpha")
    else:
        missing.append("SME sector-cycle/orderflow rows")

    target_flow = _load_target_orderflow(engine, snapshot.ctx.request.ts_code, snapshot.ctx.as_of.as_of_trade_date)
    if target_flow:
        points.extend([
            EvidencePoint("stock main net yuan", target_flow.get("main_net_yuan"), "sme.sme_stock_orderflow_daily", target_flow.get("trade_date")),
            EvidencePoint("stock retail net yuan", target_flow.get("retail_net_yuan"), "sme.sme_stock_orderflow_daily", target_flow.get("trade_date")),
        ])
    else:
        missing.append("SME stock orderflow")

    if matrix:
        points.append(EvidencePoint("strategy aggregate score", matrix.get("aggregate_score"), "Stock Edge strategy_matrix"))
        for sig in _pick_signals(matrix, ["same_sector_leadership", "sector_diffusion_breadth", "smartmoney_sw_l2", "moneyflow_7d", "orderflow_mix"]):
            points.append(EvidencePoint(sig.get("name") or sig.get("key"), sig.get("score"), sig.get("data_source") or "strategy_matrix", note=sig.get("evidence")))
    elif matrix_error:
        missing.append(f"Stock Edge strategy matrix failed: {matrix_error}")
    elif not include_full:
        points.append(EvidencePoint(
            "full strategy matrix",
            "skipped",
            "Stock Edge diagnostic MVP",
            note="Use --full-stock-edge to run the expensive full strategy matrix/decision layer.",
        ))

    latest_record = _load_latest_stock_record(engine, snapshot.ctx.request.ts_code, snapshot.ctx.as_of.as_of_trade_date)
    if latest_record:
        points.append(EvidencePoint(
            "latest Stock Edge report",
            latest_record.get("conclusion_label") or latest_record.get("status"),
            "stock.analysis_record",
            str(latest_record.get("data_cutoff")),
            note=latest_record.get("conclusion_text") or latest_record.get("output_html_path"),
        ))

    horizon_decisions = {}
    if decision_layer:
        for h in ("5d", "10d", "20d"):
            horizon_decisions[h] = decision_layer.get(f"decision_{h}") or {}
            points.append(EvidencePoint(f"{h} decision", horizon_decisions[h].get("user_facing_label"), "Stock Edge decision_layer", note=horizon_decisions[h].get("decision_summary")))

    view = "neutral"
    if target_flow and (target_flow.get("main_net_yuan") or 0) > 0 and matrix and float(matrix.get("aggregate_score") or 0) >= 0.55:
        view = "positive"
    if sme and str(sme.get("current_state") or "").lower() in {"distribution", "crowded", "overheat"}:
        view = "negative"
    summary = "Stock Edge sector-cycle evidence collected."
    if sme:
        summary = f"板块处于 {sme.get('current_state') or 'unknown'}，扩散 {sme.get('diffusion_phase') or 'unknown'}；个股主力/散户资金见证据。"
    if missing and not points:
        return PerspectiveEvidence("stock_edge_sector_cycle", "Stock Edge / Sector-Cycle-Leader", "unavailable", "unknown", "缺少可用板块周期与策略矩阵证据。", missing=missing)
    return PerspectiveEvidence(
        "stock_edge_sector_cycle",
        "Stock Edge / Sector-Cycle-Leader",
        "partial" if missing else "available",
        view,  # type: ignore[arg-type]
        summary,
        points=points,
        missing=missing,
        freshness=_freshness_from_points(points),
        raw={"sme": sme, "target_flow": target_flow, "horizon_decisions": horizon_decisions, "latest_record": latest_record},
    )


def _ta_perspective(snapshot) -> PerspectiveEvidence:
    data = snapshot.ta_context.data or {}
    candidates = data.get("candidates") or []
    warnings = data.get("warnings") or []
    regime = data.get("regime") or {}
    points: list[EvidencePoint] = []
    for row in candidates[:5]:
        points.append(EvidencePoint(
            row.get("setup_label") or row.get("setup_name"),
            row.get("final_score"),
            "ta.candidates_daily",
            str(row.get("trade_date")),
            note=f"rank={row.get('rank')} stars={row.get('star_rating')} entry={row.get('entry_price')} stop={row.get('stop_loss')}",
        ))
    for row in warnings[:3]:
        points.append(EvidencePoint(
            row.get("setup_label") or row.get("setup_name"),
            row.get("score"),
            "ta.warnings_daily",
            str(row.get("trade_date")),
            note="risk warning",
        ))
    if regime:
        points.append(EvidencePoint("market TA regime", regime.get("regime"), "ta.regime_daily", str(regime.get("trade_date")), note=f"confidence={regime.get('confidence')}"))

    view = "neutral"
    if candidates:
        view = "positive"
    if warnings and not candidates:
        view = "risk"
    if not points:
        return PerspectiveEvidence("ta", "TA", "unavailable", "unknown", "未找到目标股近期 TA setup；按中性/信号不足处理。", missing=["ta.candidates_daily", "ta.warnings_daily"])
    summary = "TA 有近期多头 setup。" if candidates else "TA 未见多头 setup，但存在风险形态。"
    return PerspectiveEvidence("ta", "TA", "available", view, summary, points=points, freshness=_freshness_from_points(points))  # type: ignore[arg-type]


def _ningbo_perspective(engine: Engine, ts_code: str, as_of: dt.date) -> PerspectiveEvidence:
    rows = _query_dicts(engine, """
        SELECT rec_date, ts_code, strategy, scoring_mode, param_version,
               rec_price, confidence_score, rec_signal_meta
        FROM ningbo.recommendations_daily
        WHERE ts_code = :ts_code AND rec_date <= :as_of
        ORDER BY rec_date DESC, confidence_score DESC NULLS LAST
        LIMIT 10
    """, {"ts_code": ts_code, "as_of": as_of})
    if not rows:
        rows = _query_dicts(engine, """
            SELECT rec_date, ts_code, strategy, confidence_score, rec_price, signal_meta
            FROM ningbo.candidates_daily
            WHERE ts_code = :ts_code AND rec_date <= :as_of
            ORDER BY rec_date DESC, confidence_score DESC NULLS LAST
            LIMIT 10
        """, {"ts_code": ts_code, "as_of": as_of})
    if not rows:
        return PerspectiveEvidence("ningbo", "Ningbo", "unavailable", "unknown", "宁波短线策略近期未命中目标股。", missing=["ningbo.recommendations_daily", "ningbo.candidates_daily"])
    points = [
        EvidencePoint(
            f"{row.get('strategy')} {row.get('scoring_mode') or 'heuristic'}",
            _float(row.get("confidence_score")),
            "ningbo.recommendations_daily/candidates_daily",
            str(row.get("rec_date")),
            note=f"rec_price={row.get('rec_price')}",
        )
        for row in rows[:5]
    ]
    return PerspectiveEvidence("ningbo", "Ningbo", "available", "positive", "宁波独立短线策略近期命中目标股；可作为独立参考，不强制与其他视角一致。", points=points, freshness=_freshness_from_points(points), raw={"rows": rows})


def _research_perspective(engine: Engine, snapshot) -> PerspectiveEvidence:
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
    return PerspectiveEvidence("research_news", "Research / Fundamentals / News / Theme", "partial", view, summary, points=points, freshness=_freshness_from_points(points), raw={"theme_heat": theme_heat, "theme_hits": theme_hits})  # type: ignore[arg-type]


def _risk_perspective(engine: Engine, snapshot) -> PerspectiveEvidence:
    ts_code = snapshot.ctx.request.ts_code
    as_of = snapshot.ctx.as_of.as_of_trade_date
    rows = {
        "blacklist": _query_dicts(engine, "SELECT trade_date, reason, severity, ann_title FROM ta.blacklist_daily WHERE ts_code=:ts_code AND trade_date <= :as_of ORDER BY trade_date DESC LIMIT 5", {"ts_code": ts_code, "as_of": as_of}),
        "suspend": _query_dicts(engine, "SELECT trade_date, suspend_type, suspend_timing FROM ta.suspend_daily WHERE ts_code=:ts_code AND trade_date <= :as_of ORDER BY trade_date DESC LIMIT 5", {"ts_code": ts_code, "as_of": as_of}),
        "limit": _query_dicts(engine, "SELECT trade_date, name, pct_chg_pct, fc_ratio, fl_ratio, fd_amount_yuan, open_times, limit FROM ta.stk_limit_daily WHERE ts_code=:ts_code AND trade_date <= :as_of ORDER BY trade_date DESC LIMIT 5", {"ts_code": ts_code, "as_of": as_of}),
    }
    points: list[EvidencePoint] = []
    for row in rows["blacklist"]:
        points.append(EvidencePoint("blacklist", row.get("severity"), "ta.blacklist_daily", str(row.get("trade_date")), note=row.get("reason") or row.get("ann_title")))
    for row in rows["suspend"]:
        points.append(EvidencePoint("suspension", row.get("suspend_type"), "ta.suspend_daily", str(row.get("trade_date")), note=row.get("suspend_timing")))
    for row in rows["limit"]:
        points.append(EvidencePoint("limit event", row.get("limit"), "ta.stk_limit_daily", str(row.get("trade_date")), note=f"pct={row.get('pct_chg_pct')} open_times={row.get('open_times')}"))

    daily = snapshot.daily_bars.data
    basic = snapshot.daily_basic.data
    if isinstance(daily, pd.DataFrame) and not daily.empty:
        tech = compute_technical_summary(daily)
        points.append(EvidencePoint("avg amount 7d yuan", tech.avg_amount_7d_yuan, "smartmoney.raw_daily", str(daily["trade_date"].iloc[-1])))
        atr14_pct = (tech.atr14 / tech.close * 100.0) if tech.atr14 is not None and tech.close else None
        points.append(EvidencePoint("ATR14 pct", atr14_pct, "smartmoney.raw_daily"))
    if isinstance(basic, pd.DataFrame) and not basic.empty:
        latest = basic.iloc[-1]
        points.append(EvidencePoint("turnover rate", _float(latest.get("turnover_rate")), "smartmoney.raw_daily_basic", str(latest.get("trade_date"))))

    hard = bool(rows["suspend"] or any(_is_hard_blacklist(row) for row in rows["blacklist"]))
    soft_risk = bool(rows["blacklist"] or rows["limit"])
    if hard:
        summary = "命中停牌或硬性黑名单风险。"
    elif soft_risk:
        summary = "命中风险提示或涨跌停事件，但当前接入证据未构成硬性禁入。"
    else:
        summary = "未命中已接入的硬性风险表；仍需结合流动性、波动和限价事件控制仓位。"
    view = "risk" if hard else ("negative" if soft_risk else "neutral")
    return PerspectiveEvidence("risk", "Risk", "available" if points else "unavailable", view, summary, points=points, freshness=_freshness_from_points(points), raw=rows)  # type: ignore[arg-type]


def render_markdown(report: DiagnosticReport) -> str:
    lines = [
        f"# Stock Edge 单股诊断 · {report.name or report.ts_code} ({report.ts_code})",
        f"",
        f"- 观察交易日: {report.as_of_trade_date.isoformat()}",
        f"- 数据截止: {report.data_cutoff_bjt}",
        "",
        "## Top Summary",
        f"- Conclusion: {report.synthesis.conclusion}",
        f"- Confidence: {report.synthesis.confidence}",
        f"- Horizon suitability: {json.dumps(report.synthesis.horizon_suitability, ensure_ascii=False)}",
        f"- Trigger: {report.synthesis.trigger}",
        f"- Invalidation: {report.synthesis.invalidation}",
        f"- Key conflict: {_key_conflict(report)}",
        f"- Evidence freshness: {json.dumps(_perspective_quality_summary(report), ensure_ascii=False)}",
        "",
        "## Advisor Synthesis",
        f"- Time window: {report.synthesis.time_window}",
        f"- Position/Risk: {report.synthesis.position_risk}",
    ]
    if report.synthesis.conflicts:
        lines.append(f"- Conflicts: {'; '.join(report.synthesis.conflicts)}")
    for p in report.perspectives:
        sources = sorted({point.source for point in p.points if point.source})
        lines.extend(["", f"## {p.title}", f"- Status/View: {p.status} / {p.view}", f"- Freshness status: {p.freshness_status}", f"- Sources: {', '.join(sources) if sources else '-'}", f"- Summary: {p.summary}"])
        if p.freshness:
            lines.append(f"- Freshness: {json.dumps(p.freshness, ensure_ascii=False, default=str)}")
        if p.missing:
            lines.append(f"- Missing evidence: {'; '.join(p.missing)}")
        for point in p.points[:12]:
            value = point.value
            as_of = f", as_of={point.as_of}" if point.as_of else ""
            lines.append(f"- {point.label}: {value} ({point.source}{as_of})")
            if point.note:
                lines.append(f"  - note: {point.note}")
    return "\n".join(lines) + "\n"


def render_html(report: DiagnosticReport) -> str:
    """Render a standalone diagnostic HTML artifact without touching report crons."""
    css = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;margin:0;background:#f4f5f7;color:#171717}
main{max-width:1120px;margin:0 auto;padding:28px 18px 48px}
header,.summary,.perspective{background:#fff;border:1px solid #d9dee5;border-radius:8px;padding:18px;margin-bottom:14px}
h1{font-size:26px;margin:0 0 10px} h2{font-size:18px;margin:0 0 12px} h3{font-size:16px;margin:16px 0 8px}
.meta,.muted{color:#626b77;font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}
.cell{border:1px solid #e5e8ec;border-radius:6px;padding:10px;background:#fbfcfd}.label{font-size:12px;color:#626b77}.value{font-weight:650;margin-top:4px}
.badge{display:inline-block;border-radius:999px;padding:3px 9px;font-size:12px;background:#eef2f6;margin-right:6px}.positive{background:#e6f4ea}.negative,.risk{background:#fdecea}.neutral,.unknown{background:#eef2f6}.fresh{background:#e6f4ea}.stale{background:#fff4d6}.unavailable,.error{background:#f1f3f5}
.conclusion{border-left:5px solid #253858}.chiprow{margin:10px 0 0}.trigger{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-top:12px}
ul{padding-left:19px} li{margin:5px 0} code{white-space:pre-wrap}
"""
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{html.escape(report.name or report.ts_code)} Stock Edge Diagnostic</title><style>{css}</style></head><body><main>",
        f"<header><h1>Stock Edge 单股诊断 · {html.escape(report.name or report.ts_code)} ({html.escape(report.ts_code)})</h1>",
        f"<div class='meta'>观察交易日 {report.as_of_trade_date.isoformat()} · 数据截止 {html.escape(report.data_cutoff_bjt)} · 生成 {html.escape(report.generated_at_bjt)}</div></header>",
        "<section class='summary conclusion'><h2>Top Conclusion</h2><div class='grid'>",
        _html_cell("Conclusion", report.synthesis.conclusion),
        _html_cell("Confidence", report.synthesis.confidence),
        _html_cell("Horizon", json.dumps(report.synthesis.horizon_suitability, ensure_ascii=False)),
        _html_cell("Key Conflict", _key_conflict(report)),
        "</div>",
        f"<div class='chiprow'>{_quality_chips(report)}{_conflict_chips(report)}</div>",
        "<div class='trigger'>",
        f"<div class='cell'><div class='label'>Trigger</div><div class='value'>{html.escape(report.synthesis.trigger)}</div></div>",
        f"<div class='cell'><div class='label'>Invalidation</div><div class='value'>{html.escape(report.synthesis.invalidation)}</div></div>",
        "</div>",
        f"<p class='muted'>{html.escape(report.synthesis.position_risk)}</p></section>",
    ]
    for p in report.perspectives:
        parts.append(f"<section class='perspective'><h2>{html.escape(p.title)}</h2>")
        parts.append(f"<span class='badge'>{html.escape(p.status)}</span><span class='badge {html.escape(p.view)}'>{html.escape(p.view)}</span><span class='badge {html.escape(p.freshness_status)}'>{html.escape(p.freshness_status)}</span>")
        parts.append(f"<p>{html.escape(p.summary)}</p>")
        sources = sorted({point.source for point in p.points if point.source})
        parts.append(f"<p class='muted'>Sources: {html.escape(', '.join(sources) if sources else '-')}</p>")
        if p.freshness:
            parts.append(f"<p class='muted'>Freshness: {html.escape(json.dumps(p.freshness, ensure_ascii=False, default=str))}</p>")
        if p.missing:
            parts.append("<h3>Missing Evidence</h3><ul>")
            parts.extend(f"<li>{html.escape(item)}</li>" for item in p.missing)
            parts.append("</ul>")
        if p.points:
            parts.append("<h3>Evidence</h3><ul>")
            for point in p.points[:12]:
                as_of = f" · as_of={point.as_of}" if point.as_of else ""
                note = f"<div class='muted'>{html.escape(point.note)}</div>" if point.note else ""
                parts.append(f"<li><strong>{html.escape(str(point.label))}</strong>: {html.escape(str(point.value))} <span class='muted'>({html.escape(point.source)}{html.escape(as_of)})</span>{note}</li>")
            parts.append("</ul>")
        parts.append("</section>")
    parts.append("</main></body></html>")
    return "".join(parts)


def write_diagnostic_artifact(
    report: DiagnosticReport,
    *,
    artifact_dir: Path,
    output_paths: dict[str, str],
    requested_at: dt.datetime | None = None,
) -> Path:
    """Persist a lightweight structured run/evidence artifact.

    The diagnostic product is still evolving, so JSON is the least invasive
    durable format.  The payload is intentionally shaped like the future DB
    schema: run metadata, perspective statuses, conclusion/confidence, output
    paths, and evidence freshness.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stem = (
        f"CN_stock_edge_diagnostic_{report.ts_code.replace('.', '_')}_"
        f"{report.as_of_trade_date.strftime('%Y%m%d')}_manifest"
    )
    path = artifact_dir / f"{stem}.json"
    path = _dedupe_path(path)
    payload = diagnostic_manifest_payload(report, output_paths=output_paths, requested_at=requested_at)
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2) + "\n", encoding="utf-8")
    return path


def diagnostic_manifest_payload(
    report: DiagnosticReport,
    *,
    output_paths: dict[str, str],
    requested_at: dt.datetime | None = None,
) -> dict[str, Any]:
    perspectives = {}
    for p in report.perspectives:
        perspectives[p.key] = {
            "title": p.title,
            "status": p.status,
            "view": p.view,
            "freshness_status": p.freshness_status,
            "freshness": p.freshness,
            "sources": sorted({point.source for point in p.points if point.source}),
            "missing_evidence": p.missing,
        }
    return {
        "artifact_type": "stock_edge_diagnostic_run",
        "schema_version": 1,
        "ts_code": report.ts_code,
        "name": report.name,
        "requested_at": requested_at.isoformat() if requested_at else None,
        "generated_at": report.generated_at_bjt,
        "as_of_trade_date": report.as_of_trade_date.isoformat(),
        "perspective_statuses": perspectives,
        "conclusion": report.synthesis.conclusion,
        "confidence": report.synthesis.confidence,
        "output_paths": output_paths,
        "evidence_freshness": _perspective_quality_summary(report),
        "db_schema_plan": report.audit.get("db_schema_plan"),
    }


def _safe(key: str, fn: Callable[[], PerspectiveEvidence]) -> PerspectiveEvidence:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return PerspectiveEvidence(key, key, "error", "unknown", f"{key} collector failed: {type(exc).__name__}: {exc}")


def _query_dicts(engine: Engine, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(text(sql), params).mappings().all()]
    except Exception:
        return []


def _load_sme_sector_cycle(engine: Engine, ts_code: str, as_of: dt.date, sector: dict[str, Any]) -> dict[str, Any] | None:
    l2_code = sector.get("l2_code")
    if not l2_code:
        return None
    rows = _query_dicts(engine, """
        SELECT o.trade_date, o.l2_code, o.l2_name, o.main_net_ratio, o.retail_net_ratio,
               o.leader_ts_code, o.leader_name, o.leader_main_net_yuan,
               d.diffusion_phase, d.diffusion_score,
               s.current_state, s.state_score, s.state_confidence, s.risk_flags_json
        FROM sme.sme_sector_orderflow_daily o
        LEFT JOIN sme.sme_sector_diffusion_daily d ON d.trade_date=o.trade_date AND d.l2_code=o.l2_code
        LEFT JOIN sme.sme_sector_state_daily s ON s.trade_date=o.trade_date AND s.l2_code=o.l2_code
        WHERE o.l2_code=:l2_code AND o.trade_date <= :as_of
        ORDER BY o.trade_date DESC
        LIMIT 1
    """, {"l2_code": l2_code, "as_of": as_of})
    if not rows:
        return None
    row = rows[0]
    row["_source"] = "sme sector orderflow/diffusion/state"
    row["is_sector_leader"] = row.get("leader_ts_code") == ts_code
    row["leader_score"] = row.get("leader_main_net_yuan") if row["is_sector_leader"] else None
    return row


def _load_target_orderflow(engine: Engine, ts_code: str, as_of: dt.date) -> dict[str, Any] | None:
    rows = _query_dicts(engine, """
        SELECT trade_date, ts_code, main_net_yuan, retail_net_yuan,
               main_net_ratio, retail_net_ratio, quality_flag
        FROM sme.sme_stock_orderflow_daily
        WHERE ts_code=:ts_code AND trade_date <= :as_of
        ORDER BY trade_date DESC
        LIMIT 1
    """, {"ts_code": ts_code, "as_of": as_of})
    return rows[0] if rows else None


def _load_latest_stock_record(engine: Engine, ts_code: str, as_of: dt.date) -> dict[str, Any] | None:
    rows = _query_dicts(engine, """
        SELECT record_id, ts_code, analysis_type, triggered_at, data_cutoff,
               status, conclusion_label, conclusion_text, forecast_json,
               output_html_path
        FROM stock.analysis_record
        WHERE ts_code=:ts_code
          AND data_cutoff::date <= :as_of
          AND status IN ('succeeded', 'partial', 'cached')
        ORDER BY data_cutoff DESC, triggered_at DESC
        LIMIT 1
    """, {"ts_code": ts_code, "as_of": as_of})
    return rows[0] if rows else None


def _load_theme_heat(engine: Engine, as_of: dt.date) -> list[dict[str, Any]]:
    from ifa.families.stock.theme_heat import week_start

    return _query_dicts(engine, """
        SELECT valid_week, theme_rank, theme_label, category, heat_score,
               confidence, affected_sectors_json, representative_stocks_json,
               quality_flag
        FROM stock.theme_heat_weekly
        WHERE valid_week <= :week
        ORDER BY valid_week DESC, theme_rank
        LIMIT 5
    """, {"week": week_start(as_of)})


def _pick_signals(matrix: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    signals = matrix.get("signals") or []
    by_key = {str(s.get("key")): s for s in signals if isinstance(s, dict)}
    return [by_key[k] for k in keys if k in by_key]


def _resolve_name(snapshot) -> str | None:
    sector = snapshot.sector_membership.data or {}
    if sector.get("name"):
        return str(sector["name"])
    event = snapshot.event_context.data or {}
    for bucket in ("limit_list", "kpl", "top_list"):
        rows = event.get(bucket) or []
        if rows and rows[0].get("name"):
            return str(rows[0]["name"])
    return None


def _collect_conflicts(perspectives: list[PerspectiveEvidence]) -> list[str]:
    positives = [p.title for p in perspectives if p.view == "positive"]
    negatives = [p.title for p in perspectives if p.view in {"negative", "risk"}]
    if positives and negatives:
        return [f"Positive evidence from {', '.join(positives)} conflicts with risk/negative evidence from {', '.join(negatives)}."]
    return [c for p in perspectives for c in p.conflicts]


def _first_point_note(p: PerspectiveEvidence | None, token: str) -> str | None:
    if not p:
        return None
    for point in p.points:
        note = point.note or ""
        if token in note.lower():
            return note
    return None


def _freshness_from_points(points: list[EvidencePoint]) -> dict[str, Any]:
    dated = [str(point.as_of) for point in points if point.as_of]
    return {
        "latest_as_of": max(dated) if dated else None,
        "source_count": len({point.source for point in points if point.source}),
        "evidence_count": len(points),
    }


def _with_quality(p: PerspectiveEvidence, as_of: dt.date) -> PerspectiveEvidence:
    freshness = dict(p.freshness or _freshness_from_points(p.points))
    latest = _parse_dateish(freshness.get("latest_as_of"))
    if p.status in {"unavailable", "error"}:
        status = "unavailable"
    elif latest is None:
        status = "fresh" if p.points else "unavailable"
    else:
        max_age = 10 if p.key == "research_news" else 3
        status = "fresh" if (as_of - latest).days <= max_age else "stale"
    freshness.update({
        "status": status,
        "source_tables": sorted({point.source for point in p.points if point.source}),
        "missing_evidence": list(p.missing),
    })
    return PerspectiveEvidence(
        p.key,
        p.title,
        p.status,
        p.view,
        p.summary,
        points=p.points,
        conflicts=p.conflicts,
        missing=p.missing,
        freshness=freshness,
        raw=p.raw,
    )


def _parse_dateish(value: Any) -> dt.date | None:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    if value is None:
        return None
    text_value = str(value)
    if not text_value or text_value == "None":
        return None
    try:
        return dt.date.fromisoformat(text_value[:10])
    except ValueError:
        return None


def _perspective_quality_summary(report: DiagnosticReport) -> dict[str, str]:
    return {p.key: p.freshness_status for p in report.perspectives}


def _quality_chips(report: DiagnosticReport) -> str:
    return "".join(
        f"<span class='badge {html.escape(status)}'>{html.escape(key)}: {html.escape(status)}</span>"
        for key, status in _perspective_quality_summary(report).items()
    )


def _conflict_chips(report: DiagnosticReport) -> str:
    if not report.synthesis.conflicts:
        return "<span class='badge neutral'>no major conflict</span>"
    return "".join(f"<span class='badge risk'>{html.escape(item)}</span>" for item in report.synthesis.conflicts[:3])


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


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    for idx in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{idx}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find unused artifact path for {path}")


def _key_conflict(report: DiagnosticReport) -> str:
    if report.synthesis.conflicts:
        return report.synthesis.conflicts[0]
    views = {p.title: p.view for p in report.perspectives if p.status != "unavailable"}
    if not views:
        return "No usable perspective evidence yet."
    return "No major cross-perspective conflict detected."


def _html_cell(label: str, value: Any) -> str:
    return f"<div class='cell'><div class='label'>{html.escape(label)}</div><div class='value'>{html.escape(str(value))}</div></div>"


def _is_hard_blacklist(row: dict[str, Any]) -> bool:
    severity = str(row.get("severity") or "").lower()
    return severity in {"hard", "critical", "high", "severe"}


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
