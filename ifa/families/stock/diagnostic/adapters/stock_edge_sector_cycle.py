"""Stock Edge sector-cycle perspective adapter."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.engine import Engine

from ifa.families.stock.diagnostic.models import EvidencePoint, PerspectiveEvidence

from .common import freshness_from_points, query_dicts, timed


def collect(
    *,
    engine: Engine,
    snapshot: Any,
    matrix: dict[str, Any] | None,
    decision_layer: dict[str, Any] | None,
    matrix_error: str | None,
    include_full: bool,
) -> PerspectiveEvidence:
    return timed(
        "stock_edge_sector_cycle",
        lambda: _collect(engine, snapshot, matrix, decision_layer, matrix_error, include_full=include_full),
    )


def _collect(
    engine: Engine,
    snapshot: Any,
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
            missing.append("target is not current SME sector leader; use stock.sector_cycle_leader_daily for stock-specific rank context when populated")
    else:
        missing.append("SME sector-cycle/orderflow rows")

    leader_rank = load_sector_cycle_leader_rank(engine, snapshot.ctx.request.ts_code, snapshot.ctx.as_of.as_of_trade_date)
    if leader_rank:
        for key, label in [
            ("rank_in_sector", "sector-cycle leader rank"),
            ("leader_score", "sector-cycle leader score"),
            ("sector_rank_count", "sector rank universe count"),
            ("quality_flag", "sector-cycle leader quality"),
        ]:
            if leader_rank.get(key) is not None:
                points.append(EvidencePoint(label, leader_rank.get(key), "stock.sector_cycle_leader_daily", leader_rank.get("trade_date")))
    else:
        missing.append("stock.sector_cycle_leader_daily")

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
        freshness=freshness_from_points(points),
        raw={"sme": sme, "leader_rank": leader_rank, "target_flow": target_flow, "horizon_decisions": horizon_decisions, "latest_record": latest_record},
    )


def _load_sme_sector_cycle(engine: Engine, ts_code: str, as_of: dt.date, sector: dict[str, Any]) -> dict[str, Any] | None:
    l2_code = sector.get("l2_code")
    if not l2_code:
        return None
    rows = query_dicts(engine, """
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
    rows = query_dicts(engine, """
        SELECT trade_date, ts_code, main_net_yuan, retail_net_yuan,
               main_net_ratio, retail_net_ratio, quality_flag
        FROM sme.sme_stock_orderflow_daily
        WHERE ts_code=:ts_code AND trade_date <= :as_of
        ORDER BY trade_date DESC
        LIMIT 1
    """, {"ts_code": ts_code, "as_of": as_of})
    return rows[0] if rows else None


def load_sector_cycle_leader_rank(engine: Engine, ts_code: str, as_of: dt.date) -> dict[str, Any] | None:
    rows = query_dicts(engine, """
        SELECT trade_date, ts_code, name, l1_code, l1_name, l2_code, l2_name,
               rank_in_sector, sector_rank_count, leader_score,
               sector_score, stock_score, quality_flag, evidence_json
        FROM stock.sector_cycle_leader_daily
        WHERE ts_code=:ts_code AND trade_date <= :as_of
        ORDER BY trade_date DESC
        LIMIT 1
    """, {"ts_code": ts_code, "as_of": as_of})
    return rows[0] if rows else None


def _load_latest_stock_record(engine: Engine, ts_code: str, as_of: dt.date) -> dict[str, Any] | None:
    rows = query_dicts(engine, """
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


def _pick_signals(matrix: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    signals = matrix.get("signals") or []
    by_key = {str(s.get("key")): s for s in signals if isinstance(s, dict)}
    return [by_key[k] for k in keys if k in by_key]
