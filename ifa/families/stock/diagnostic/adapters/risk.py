"""Risk perspective adapter for Stock Edge diagnostic reports."""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.engine import Engine

from ifa.families.stock.diagnostic.models import EvidencePoint, PerspectiveEvidence
from ifa.families.stock.features import compute_technical_summary

from .common import freshness_from_points, query_dicts, timed, to_float


def collect(*, engine: Engine, snapshot: Any) -> PerspectiveEvidence:
    return timed("risk", lambda: _collect(engine, snapshot))


def _collect(engine: Engine, snapshot: Any) -> PerspectiveEvidence:
    ts_code = snapshot.ctx.request.ts_code
    as_of = snapshot.ctx.as_of.as_of_trade_date
    rows = {
        "risk_veto": query_dicts(engine, "SELECT trade_date, veto_category, hard_veto, severity, source_table, source_date, reason, evidence_json FROM stock.risk_veto_daily WHERE ts_code=:ts_code AND trade_date <= :as_of ORDER BY trade_date DESC, hard_veto DESC LIMIT 10", {"ts_code": ts_code, "as_of": as_of}),
        "blacklist": query_dicts(engine, "SELECT trade_date, reason, severity, ann_title FROM ta.blacklist_daily WHERE ts_code=:ts_code AND trade_date <= :as_of ORDER BY trade_date DESC LIMIT 5", {"ts_code": ts_code, "as_of": as_of}),
        "suspend": query_dicts(engine, "SELECT trade_date, suspend_type, suspend_timing FROM ta.suspend_daily WHERE ts_code=:ts_code AND trade_date <= :as_of ORDER BY trade_date DESC LIMIT 5", {"ts_code": ts_code, "as_of": as_of}),
        "limit": query_dicts(engine, "SELECT trade_date, name, pct_chg_pct, fc_ratio, fl_ratio, fd_amount_yuan, open_times, limit FROM ta.stk_limit_daily WHERE ts_code=:ts_code AND trade_date <= :as_of ORDER BY trade_date DESC LIMIT 5", {"ts_code": ts_code, "as_of": as_of}),
    }
    points: list[EvidencePoint] = []
    vetoes: list[dict[str, Any]] = []
    persisted_vetoes = bool(rows["risk_veto"])
    for row in rows["risk_veto"][:5]:
        vetoes.append({
            "category": row.get("veto_category"),
            "hard": bool(row.get("hard_veto")),
            "source": row.get("source_table") or "stock.risk_veto_daily",
            "as_of": row.get("source_date") or row.get("trade_date"),
            "reason": row.get("reason"),
        })
        points.append(EvidencePoint(
            str(row.get("veto_category") or "risk veto"),
            row.get("severity"),
            "stock.risk_veto_daily",
            str(row.get("trade_date")),
            note=row.get("reason"),
        ))
    if not persisted_vetoes:
        for row in rows["blacklist"]:
            category = "hard_blacklist" if _is_hard_blacklist(row) else "soft_blacklist"
            vetoes.append({"category": category, "hard": category == "hard_blacklist", "source": "ta.blacklist_daily", "as_of": row.get("trade_date"), "reason": row.get("reason") or row.get("ann_title")})
            points.append(EvidencePoint("blacklist", row.get("severity"), "ta.blacklist_daily", str(row.get("trade_date")), note=row.get("reason") or row.get("ann_title")))
        for row in rows["suspend"]:
            vetoes.append({"category": "suspension", "hard": True, "source": "ta.suspend_daily", "as_of": row.get("trade_date"), "reason": row.get("suspend_type")})
            points.append(EvidencePoint("suspension", row.get("suspend_type"), "ta.suspend_daily", str(row.get("trade_date")), note=row.get("suspend_timing")))
        for row in rows["limit"]:
            vetoes.append({"category": "limit_event", "hard": False, "source": "ta.stk_limit_daily", "as_of": row.get("trade_date"), "reason": row.get("limit")})
            points.append(EvidencePoint("limit event", row.get("limit"), "ta.stk_limit_daily", str(row.get("trade_date")), note=f"pct={row.get('pct_chg_pct')} open_times={row.get('open_times')}"))
    if vetoes:
        points.append(EvidencePoint(
            "normalized veto registry",
            {"hard": sum(1 for item in vetoes if item["hard"]), "soft": sum(1 for item in vetoes if not item["hard"])},
            "stock.diagnostic_risk_veto_registry",
            note="Derived from TA risk source tables; no separate production veto table mutation.",
        ))

    daily = snapshot.daily_bars.data
    basic = snapshot.daily_basic.data
    if isinstance(daily, pd.DataFrame) and not daily.empty:
        tech = compute_technical_summary(daily)
        points.append(EvidencePoint("avg amount 7d yuan", tech.avg_amount_7d_yuan, "smartmoney.raw_daily", str(daily["trade_date"].iloc[-1])))
        atr14_pct = (tech.atr14 / tech.close * 100.0) if tech.atr14 is not None and tech.close else None
        points.append(EvidencePoint("ATR14 pct", atr14_pct, "smartmoney.raw_daily"))
    if isinstance(basic, pd.DataFrame) and not basic.empty:
        latest = basic.iloc[-1]
        points.append(EvidencePoint("turnover rate", to_float(latest.get("turnover_rate")), "smartmoney.raw_daily_basic", str(latest.get("trade_date"))))

    hard = bool(any(row.get("hard_veto") for row in rows["risk_veto"]) or (not persisted_vetoes and (rows["suspend"] or any(_is_hard_blacklist(row) for row in rows["blacklist"]))))
    soft_risk = bool(rows["risk_veto"] or (not persisted_vetoes and (rows["blacklist"] or rows["limit"])))
    if hard:
        summary = "命中停牌或硬性黑名单风险。"
    elif soft_risk:
        summary = "命中风险提示或涨跌停事件，但当前接入证据未构成硬性禁入。"
    else:
        summary = "未命中已接入的硬性风险表；仍需结合流动性、波动和限价事件控制仓位。"
    view = "risk" if hard else ("negative" if soft_risk else "neutral")
    raw = dict(rows)
    raw["normalized_vetoes"] = vetoes
    return PerspectiveEvidence("risk", "Risk", "available" if points else "unavailable", view, summary, points=points, freshness=freshness_from_points(points), raw=raw)  # type: ignore[arg-type]


def _is_hard_blacklist(row: dict[str, Any]) -> bool:
    severity = str(row.get("severity") or "").lower()
    return severity in {"hard", "critical", "high", "severe"}
