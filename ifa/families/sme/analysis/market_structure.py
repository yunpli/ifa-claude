"""Daily market-structure interpreter for SME MVP-1.

This module deliberately keeps the first production path rule-based and
auditable. Index moves are only one input; the main judgement comes from SW L2
orderflow, diffusion, state, breadth, and trading-day rolling windows. External
variables are accepted as a supplied summary because live web/LLM retrieval is
not a deterministic data dependency of the SME data layer.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from ifa.core.report.disclaimer import (
    DISCLAIMER_PARAGRAPHS_EN,
    DISCLAIMER_PARAGRAPHS_ZH,
    FOOTER_SHORT_EN,
    FOOTER_SHORT_ZH,
    SHORT_HEADER_EN,
    SHORT_HEADER_ZH,
)
from ifa.families.sme.data.calendar import latest_trade_date, previous_trade_date
from ifa.families.sme.params.store import load_market_structure_params
from ifa.families.sme.versions import SME_MARKET_STRUCTURE_LOGIC_VERSION


log = logging.getLogger(__name__)


MAIN_INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
}

DEFENSIVE_KEYWORDS = (
    "银行",
    "保险",
    "电力",
    "公用",
    "煤炭",
    "石油",
    "燃气",
    "贵金属",
    "黄金",
    "农业",
    "食品",
    "医药",
)

EVENT_KEYWORDS = (
    "军工",
    "半导体",
    "芯片",
    "算力",
    "机器人",
    "低空",
    "卫星",
    "航天",
    "能源金属",
)

STATE_LABELS = {
    "risk_appetite_up": "风险偏好上升",
    "risk_appetite_down": "风险偏好下降",
    "defensive_switch": "防御切换",
    "event_trade": "事件博弈",
    "high_low_switch": "高低切换",
    "mainline_repricing": "主线重估",
    "mixed_rotation": "混合轮动",
}

BJT = ZoneInfo("Asia/Shanghai")


def _f(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _i(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    return int(value)


def _bn(value: Any) -> float:
    return round(_f(value) / 100_000_000, 2)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _json_text(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False)


def _pct(value: Any) -> float:
    return round(_f(value), 3)


def _zh_date(value: Any) -> str:
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return f"{value.year}年{value.month}月{value.day}日"
    if value:
        parsed = dt.date.fromisoformat(str(value))
        return f"{parsed.year}年{parsed.month}月{parsed.day}日"
    return "未知日期"


def _contains(name: str | None, keywords: Iterable[str]) -> bool:
    haystack = name or ""
    return any(k in haystack for k in keywords)


def classify_outflow(row: dict[str, Any], params: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    """Classify a negative-flow sector without relying on price alone."""
    p = params or load_market_structure_params()
    outflow_p = p.get("outflow") or {}
    ret = _f(row.get("sector_return_sw_index"))
    main_ratio = _f(row.get("main_net_ratio"))
    current_state = row.get("current_state") or ""
    diffusion_phase = row.get("diffusion_phase") or ""
    recent_return = _f(row.get("return_5d"))
    prev_main = row.get("prev_main_net_yuan")
    main_net = _f(row.get("main_net_yuan"))
    reasons: list[str] = []

    if main_ratio <= _f(outflow_p.get("panic_main_net_ratio_max"), -0.015) and ret <= _f(outflow_p.get("panic_return_max"), -1.0) and (
        current_state == "cooldown" or diffusion_phase == "diffusion_breakdown"
    ):
        reasons.append("主力流出强、价格下跌且扩散破坏")
        return "panic_sell", reasons
    if recent_return >= _f(outflow_p.get("high_low_recent_return_min"), 3.0) and main_net < 0:
        reasons.append("近5个交易日涨幅较高后流出，疑似高低切换")
        return "high_low_switch", reasons
    if ret >= 0 and main_net < 0:
        reasons.append("价格仍强但主力净流出，偏主动兑现/降仓")
        return "active_de_risk", reasons
    if prev_main is not None and main_net < 0 and main_net > _f(prev_main):
        reasons.append("仍流出但较上一交易日收敛")
        return "controlled_outflow", reasons
    reasons.append("主力净流出，方向以降仓观察为主")
    return "active_de_risk", reasons


def classify_inflow(row: dict[str, Any], params: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    """Classify a positive-flow sector using return, diffusion, and concentration."""
    p = params or load_market_structure_params()
    inflow_p = p.get("inflow") or {}
    name = row.get("l2_name") or ""
    ret = _f(row.get("sector_return_sw_index"))
    main_ratio = _f(row.get("main_net_ratio"))
    top5_share = _f(row.get("top5_main_net_share"))
    flow_breadth_5d = _f(row.get("flow_breadth_5d"))
    inst_net = _f(row.get("inst_net_buy_yuan"))
    current_state = row.get("current_state") or ""
    reasons: list[str] = []

    if inst_net > 0 and ret <= 0 and main_ratio > 0:
        reasons.append("龙虎榜机构席位代理与大单在弱价格中承接")
        return "institutional_absorption", reasons
    if (
        ret >= _f(inflow_p.get("chase_return_min"), 2.0)
        and main_ratio >= _f(inflow_p.get("chase_main_net_ratio_min"), 0.01)
        and top5_share >= _f(inflow_p.get("chase_top5_share_min"), 0.60)
    ):
        reasons.append("涨幅、主力净流入和头部集中度同时较高")
        return "chase_high", reasons
    if ret <= 0 and main_ratio > 0:
        reasons.append("价格未充分反映但主力承接为正")
        return "institutional_absorption", reasons
    if _contains(name, DEFENSIVE_KEYWORDS):
        reasons.append("防御/低敏感属性板块获得资金")
        return "defensive", reasons
    if flow_breadth_5d >= _f(inflow_p.get("long_config_flow_breadth_5d_min"), 0.55) and current_state in {"ignition", "diffusion", "acceleration"}:
        reasons.append("5日资金扩散较广且状态处于升温区")
        return "long_config", reasons
    if _contains(name, EVENT_KEYWORDS):
        reasons.append("事件/产业主题属性较强")
        return "event_trade", reasons
    reasons.append("当日主力净流入，暂定战术性流入")
    return "tactical_inflow", reasons


def assess_capital_state(
    *,
    breadth: dict[str, Any],
    inflows: list[dict[str, Any]],
    outflows: list[dict[str, Any]],
    strong_return_weak_flow: list[dict[str, Any]],
    suppressed_repair: list[dict[str, Any]],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize market risk preference from flow distribution."""
    p = params or load_market_structure_params()
    state_p = p.get("capital_state") or {}
    up = _i(breadth.get("up_count"))
    down = _i(breadth.get("down_count"))
    up_ratio = up / max(up + down, 1)
    defensive_inflows = [r for r in inflows if r.get("inflow_type") in {"defensive", "institutional_absorption"}]
    chase_inflows = [r for r in inflows if r.get("inflow_type") == "chase_high"]
    high_low_outflows = [r for r in outflows if r.get("outflow_type") == "high_low_switch"]

    tags: list[str] = []
    if up_ratio >= _f(state_p.get("risk_up_breadth_min"), 0.58) and len(inflows) >= len(outflows) * _f(state_p.get("inflow_outflow_count_ratio_min"), 0.80):
        tags.append("risk_appetite_up")
    if up_ratio <= _f(state_p.get("risk_down_breadth_max"), 0.42) and len(outflows) > len(inflows):
        tags.append("risk_appetite_down")
    if inflows and len(defensive_inflows) / max(len(inflows), 1) >= _f(state_p.get("defensive_share_min"), 0.33):
        tags.append("defensive_switch")
    if high_low_outflows and suppressed_repair:
        tags.append("high_low_switch")
    if chase_inflows and strong_return_weak_flow:
        tags.append("event_trade")
    if len([r for r in inflows if r.get("current_state") in {"diffusion", "acceleration"}]) >= _f(state_p.get("mainline_inflow_count_min"), 3):
        tags.append("mainline_repricing")
    if not tags:
        tags.append("mixed_rotation")

    primary = tags[0]
    return {
        "primary_state": primary,
        "state_tags": tags,
        "market_up_ratio": round(up_ratio, 3),
        "evidence": {
            "defensive_inflow_count": len(defensive_inflows),
            "chase_high_count": len(chase_inflows),
            "high_low_outflow_count": len(high_low_outflows),
            "strong_return_weak_flow_count": len(strong_return_weak_flow),
            "suppressed_repair_count": len(suppressed_repair),
        },
    }


def _load_market_overview(engine, trade_date: dt.date, params: dict[str, Any] | None = None) -> dict[str, Any]:
    p = params or load_market_structure_params()
    overview_p = p.get("market_overview") or {}
    prev = previous_trade_date(engine, trade_date)
    with engine.connect() as conn:
        index_rows = conn.execute(text("""
            SELECT ts_code, close, pct_chg, amount
            FROM smartmoney.raw_index_daily
            WHERE trade_date = :d
              AND ts_code = ANY(:codes)
            ORDER BY array_position(:codes, ts_code)
        """), {"d": trade_date, "codes": list(MAIN_INDEX_NAMES)}).mappings().all()
        breadth = conn.execute(text("""
            SELECT
                COUNT(*) AS total_count,
                COUNT(*) FILTER (WHERE pct_chg > 0) AS up_count,
                COUNT(*) FILTER (WHERE pct_chg < 0) AS down_count,
                COUNT(*) FILTER (WHERE pct_chg = 0) AS flat_count,
                COUNT(*) FILTER (WHERE pct_chg >= 9.8) AS limit_up_like_count,
                COUNT(*) FILTER (WHERE pct_chg <= -9.8) AS limit_down_like_count,
                SUM(amount * 1000)::bigint AS amount_yuan
            FROM smartmoney.raw_daily
            WHERE trade_date = :d
        """), {"d": trade_date}).mappings().one()
        prev_amount = None
        if prev:
            prev_amount = conn.execute(text("""
                SELECT SUM(amount * 1000)::bigint
                FROM smartmoney.raw_daily
                WHERE trade_date = :d
            """), {"d": prev}).scalar_one()

    amount_yuan = _f(breadth["amount_yuan"])
    turnover_change = None
    if prev_amount and _f(prev_amount) > 0:
        turnover_change = round(amount_yuan / _f(prev_amount) - 1.0, 4)
    index_payload = [
        {
            "ts_code": r["ts_code"],
            "name": MAIN_INDEX_NAMES.get(r["ts_code"], r["ts_code"]),
            "close": _f(r["close"]),
            "pct_chg": _pct(r["pct_chg"]),
            "amount_bn_yuan": _bn(_f(r["amount"]) * 1000),
        }
        for r in index_rows
    ]
    up = _i(breadth["up_count"])
    down = _i(breadth["down_count"])
    up_ratio = up / max(up + down, 1)
    if up_ratio >= _f(overview_p.get("risk_up_breadth_min"), 0.60) and (turnover_change or 0) > _f(overview_p.get("volume_confirm_min"), 0.05):
        read = "放量普涨，风险偏好至少在日频层面扩张；后续要看主力流是否扩散到更多 SW L2，而不是只看指数。"
    elif up_ratio <= _f(overview_p.get("risk_down_breadth_max"), 0.40) and (turnover_change or 0) > _f(overview_p.get("volume_confirm_min"), 0.05):
        read = "放量下跌，说明抛压主动；需要优先识别流出是否恐慌扩散。"
    elif up_ratio >= _f(overview_p.get("structure_repair_breadth_min"), 0.55):
        read = "涨多跌少但量能确认有限，属于结构性修复，需要看主力流入是否集中在少数方向。"
    else:
        read = "指数和个股结构分化，市场强弱不能由指数单独定性。"
    return {
        "trade_date": trade_date,
        "previous_trade_date": prev,
        "indices": index_payload,
        "breadth": {
            "total_count": _i(breadth["total_count"]),
            "up_count": _i(breadth["up_count"]),
            "down_count": _i(breadth["down_count"]),
            "flat_count": _i(breadth["flat_count"]),
            "limit_up_like_count": _i(breadth["limit_up_like_count"]),
            "limit_down_like_count": _i(breadth["limit_down_like_count"]),
            "amount_bn_yuan": _bn(amount_yuan),
            "amount_change_vs_prev": turnover_change,
        },
        "intraday": {
            "status": "not_persisted_in_sme_mvp1",
            "interpretation": "SME MVP1 当前用日频指数/OHLC、成交额和全市场涨跌家数判断结构；真实分时走势需接入分钟/实时快照后再入库。",
        },
        "interpretation": read,
    }


def _load_sector_rows(engine, trade_date: dt.date) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            WITH dates AS (
                SELECT trade_date,
                       ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
                FROM (
                    SELECT DISTINCT trade_date
                    FROM sme.sme_sector_orderflow_daily
                    WHERE trade_date <= :d
                ) x
            ),
            target AS (
                SELECT rn
                FROM dates
                WHERE trade_date = :d
            ),
            hist AS (
                SELECT d.trade_date, d.rn, so.*
                FROM target t
                JOIN dates d ON d.rn BETWEEN t.rn - 9 AND t.rn
                JOIN sme.sme_sector_orderflow_daily so ON so.trade_date = d.trade_date
            ),
            h AS (
                SELECT
                    l2_code,
                    SUM(main_net_yuan) FILTER (WHERE rn = (SELECT rn FROM target) - 1) AS prev_main_net_yuan,
                    SUM(main_net_yuan) FILTER (WHERE rn BETWEEN (SELECT rn FROM target) - 2 AND (SELECT rn FROM target)) AS main_net_3d_yuan,
                    SUM(main_net_yuan) FILTER (WHERE rn BETWEEN (SELECT rn FROM target) - 4 AND (SELECT rn FROM target)) AS main_net_5d_yuan,
                    SUM(main_net_yuan) FILTER (WHERE rn BETWEEN (SELECT rn FROM target) - 9 AND (SELECT rn FROM target)) AS main_net_10d_yuan,
                    AVG(main_net_ratio) FILTER (WHERE rn BETWEEN (SELECT rn FROM target) - 4 AND (SELECT rn FROM target)) AS avg_main_net_ratio_5d,
                    (EXP(SUM(LN(GREATEST(0.0001, 1 + COALESCE(sector_return_sw_index, 0) / 100.0))) FILTER (WHERE rn BETWEEN (SELECT rn FROM target) - 2 AND (SELECT rn FROM target))) - 1) * 100.0 AS return_3d,
                    (EXP(SUM(LN(GREATEST(0.0001, 1 + COALESCE(sector_return_sw_index, 0) / 100.0))) FILTER (WHERE rn BETWEEN (SELECT rn FROM target) - 4 AND (SELECT rn FROM target))) - 1) * 100.0 AS return_5d,
                    (EXP(SUM(LN(GREATEST(0.0001, 1 + COALESCE(sector_return_sw_index, 0) / 100.0))) FILTER (WHERE rn BETWEEN (SELECT rn FROM target) - 9 AND (SELECT rn FROM target))) - 1) * 100.0 AS return_10d
                FROM hist
                GROUP BY l2_code
            )
            SELECT
                so.trade_date, so.l1_code, so.l1_name, so.l2_code, so.l2_name,
                so.member_count, so.matched_stock_count, so.coverage_ratio,
                so.sector_amount_yuan, so.sector_return_sw_index,
                so.sm_net_yuan, so.md_net_yuan, so.lg_net_yuan, so.elg_net_yuan,
                so.main_net_yuan, so.retail_net_yuan, so.net_mf_amount_yuan,
                so.main_net_ratio, so.retail_net_ratio, so.elg_net_ratio,
                so.flow_breadth, so.main_positive_breadth, so.retail_positive_breadth,
                so.price_positive_breadth, so.top5_main_net_share,
                so.leader_ts_code, so.leader_name, so.leader_main_net_yuan,
                d.flow_breadth_1d, d.flow_breadth_3d, d.flow_breadth_5d,
                d.flow_breadth_10d, d.diffusion_slope_5_10, d.diffusion_phase,
                d.diffusion_score, d.leader_return_5d, d.median_member_return_5d,
                d.tail_member_return_5d, d.top_members_json,
                st.current_state, st.state_score, st.state_confidence,
                st.transition_hint, st.risk_flags_json,
                h.prev_main_net_yuan, h.main_net_3d_yuan, h.main_net_5d_yuan,
                h.main_net_10d_yuan, h.avg_main_net_ratio_5d, h.return_3d,
                h.return_5d, h.return_10d,
                inst.inst_buy_yuan, inst.inst_sell_yuan, inst.inst_net_buy_yuan,
                inst.inst_stock_count, lhb.lhb_net_amount_yuan, lhb.lhb_stock_count
            FROM sme.sme_sector_orderflow_daily so
            LEFT JOIN sme.sme_sector_diffusion_daily d
              ON d.trade_date = so.trade_date AND d.l2_code = so.l2_code
            LEFT JOIN sme.sme_sector_state_daily st
              ON st.trade_date = so.trade_date AND st.l2_code = so.l2_code
            LEFT JOIN h ON h.l2_code = so.l2_code
            LEFT JOIN (
                /*
                `raw_top_inst` only covers 龙虎榜/异动披露股票. `exalter='机构专用'`
                is therefore an institution-seat proxy, not a complete
                institution ownership flow. Amounts are already yuan in the
                local SmartMoney table.
                */
                SELECT
                    m.l2_code,
                    SUM(ti.buy) FILTER (WHERE ti.exalter = '机构专用')::bigint AS inst_buy_yuan,
                    SUM(ti.sell) FILTER (WHERE ti.exalter = '机构专用')::bigint AS inst_sell_yuan,
                    SUM(ti.net_buy) FILTER (WHERE ti.exalter = '机构专用')::bigint AS inst_net_buy_yuan,
                    COUNT(DISTINCT ti.ts_code) FILTER (WHERE ti.exalter = '机构专用')::int AS inst_stock_count
                FROM smartmoney.raw_top_inst ti
                JOIN sme.sme_sw_member_daily m
                  ON m.trade_date = ti.trade_date AND m.ts_code = ti.ts_code
                WHERE ti.trade_date = :d
                GROUP BY m.l2_code
            ) inst ON inst.l2_code = so.l2_code
            LEFT JOIN (
                /*
                `raw_top_list` is event-driven 龙虎榜 flow. It is useful for
                hot-money/event pressure, but should not be treated as whole-
                market institutional flow.
                */
                SELECT
                    m.l2_code,
                    SUM(tl.net_amount)::bigint AS lhb_net_amount_yuan,
                    COUNT(DISTINCT tl.ts_code)::int AS lhb_stock_count
                FROM smartmoney.raw_top_list tl
                JOIN sme.sme_sw_member_daily m
                  ON m.trade_date = tl.trade_date AND m.ts_code = tl.ts_code
                WHERE tl.trade_date = :d
                GROUP BY m.l2_code
            ) lhb ON lhb.l2_code = so.l2_code
            WHERE so.trade_date = :d
        """), {"d": trade_date}).mappings().all()
    return [dict(r) for r in rows]


def _actor_verdict(row: dict[str, Any]) -> str:
    main = _f(row.get("main_net_yuan"))
    retail = _f(row.get("retail_net_yuan"))
    inst = _f(row.get("inst_net_buy_yuan"))
    lhb = _f(row.get("lhb_net_amount_yuan"))
    if inst > 0 and main > 0:
        return "机构席位代理和大单共振承接"
    if inst > 0 and main <= 0:
        return "机构席位代理逆势承接，但板块大单未共振"
    if retail > 0 and main < 0:
        return "小中单接力，大单兑现"
    if retail < 0 and main > 0:
        return "大单吸筹，小中单退出"
    if main > 0 and lhb > 0:
        return "大单流入，并有龙虎榜事件资金配合"
    if main > 0:
        return "大单主导流入"
    if main < 0:
        return "大单主导流出"
    return "资金主体不清晰"


def _sector_summary(row: dict[str, Any]) -> dict[str, Any]:
    top5_share_raw = _f(row.get("top5_main_net_share"))
    top5_share_capped = max(0.0, min(1.0, top5_share_raw))
    return {
        "l1_name": row.get("l1_name"),
        "l2_code": row.get("l2_code"),
        "l2_name": row.get("l2_name"),
        "return_1d": _pct(row.get("sector_return_sw_index")),
        "return_5d": _pct(row.get("return_5d")),
        "main_net_bn_yuan": _bn(row.get("main_net_yuan")),
        "main_net_ratio": round(_f(row.get("main_net_ratio")), 4),
        "main_positive_breadth": round(_f(row.get("main_positive_breadth")), 3),
        "price_positive_breadth": round(_f(row.get("price_positive_breadth")), 3),
        "flow_breadth_5d": round(_f(row.get("flow_breadth_5d")), 3),
        "top5_main_net_share": round(top5_share_capped, 3),
        "top5_main_net_share_raw": round(top5_share_raw, 3),
        "actor_profile": {
            "verdict": _actor_verdict(row),
            "extra_large_net_bn_yuan": _bn(row.get("elg_net_yuan")),
            "large_net_bn_yuan": _bn(row.get("lg_net_yuan")),
            "retail_net_bn_yuan": _bn(row.get("retail_net_yuan")),
            "retail_net_ratio": round(_f(row.get("retail_net_ratio")), 4),
            "institution_lhb_proxy_net_bn_yuan": _bn(row.get("inst_net_buy_yuan")),
            "institution_lhb_proxy_stock_count": _i(row.get("inst_stock_count")),
            "lhb_event_net_bn_yuan": _bn(row.get("lhb_net_amount_yuan")),
            "lhb_event_stock_count": _i(row.get("lhb_stock_count")),
        },
        "current_state": row.get("current_state"),
        "diffusion_phase": row.get("diffusion_phase"),
        "leader": {
            "ts_code": row.get("leader_ts_code"),
            "name": row.get("leader_name"),
            "main_net_bn_yuan": _bn(row.get("leader_main_net_yuan")),
        },
    }


def _with_reason(row: dict[str, Any], kind: str, reasons: list[str]) -> dict[str, Any]:
    item = _sector_summary(row)
    item[f"{kind}_type"] = row[f"{kind}_type"]
    item["reasons"] = reasons
    item["rank_score"] = row.get("rank_score")
    return item


def _rank_inflow(row: dict[str, Any], params: dict[str, Any]) -> float:
    ranking = params.get("ranking") or {}
    main_bn = _f(row.get("main_net_yuan")) / 100_000_000.0
    breadth = _f(row.get("flow_breadth_5d"))
    ret_1d = _f(row.get("sector_return_sw_index"))
    top5 = max(0.0, min(1.5, _f(row.get("top5_main_net_share"))))
    return (
        main_bn * _f(ranking.get("main_net_bn_weight"), 1.0)
        + breadth * _f(ranking.get("flow_breadth_5d_weight"), 0.0)
        + ret_1d * _f(ranking.get("return_1d_weight"), 0.0)
        - top5 * _f(ranking.get("top5_share_penalty"), 0.0)
    )


def _is_primary_candidate(row: dict[str, Any], params: dict[str, Any]) -> bool:
    primary = params.get("primary") or {}
    mode = primary.get("mode") or "strict_state_diffusion"
    if _f(row.get("main_net_ratio")) < _f(primary.get("min_main_net_ratio"), 0.0):
        return False
    if _f(row.get("flow_breadth_5d")) < _f(primary.get("min_flow_breadth_5d"), 0.45):
        return False
    allowed_types = set(primary.get("allowed_inflow_types") or [])
    if allowed_types and row.get("inflow_type") not in allowed_types:
        return False
    allowed_states = set(primary.get("allowed_states") or [])
    if mode == "strict_state_diffusion" and allowed_states:
        return row.get("current_state") in allowed_states
    if mode == "broad_positive_flow":
        return _f(row.get("main_net_yuan")) > 0
    return row.get("current_state") in allowed_states if allowed_states else True


def _scenario(primary: list[dict[str, Any]], suppressed: list[dict[str, Any]], defensive: list[dict[str, Any]]) -> dict[str, Any]:
    primary_names = [r["l2_name"] for r in primary[:5]]
    suppressed_names = [r["l2_name"] for r in suppressed[:5]]
    defensive_names = [r["l2_name"] for r in defensive[:5]]
    return {
        "risk_escalates": {
            "preferred": defensive_names or primary_names[:3],
            "logic": "风险继续升级时，优先保留主力承接明确、回撤敏感度低或防御属性强的方向，回避涨幅强但资金不足的拥挤交易。",
        },
        "risk_eases": {
            "preferred": suppressed_names or primary_names[:3],
            "logic": "风险缓和时，优先看流出收敛、压制时间较长、扩散未继续破坏的修复弹性方向。",
        },
        "event_drags": {
            "preferred": primary_names[:5],
            "logic": "事件拖延时，资金大概率在一级受益方向内部做强弱切换，并向二级受益或低位承接方向扩散。",
        },
    }


def _names(items: list[dict[str, Any]], limit: int = 5) -> list[str]:
    return [str(r["l2_name"]) for r in items[:limit] if r.get("l2_name")]


def _actor_lines(items: list[dict[str, Any]], limit: int = 3) -> list[str]:
    lines = []
    for item in items[:limit]:
        profile = item.get("actor_profile") or {}
        verdict = profile.get("verdict")
        if item.get("l2_name") and verdict:
            lines.append(f"{item['l2_name']}：{verdict}")
    return lines


def _state_text(tags: list[str]) -> str:
    labels = [STATE_LABELS.get(t, t) for t in tags]
    if not labels:
        return "结构不清晰"
    return "，同时伴随".join([labels[0], " / ".join(labels[1:])]) if len(labels) > 1 else labels[0]


def build_client_conclusion(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Compress an auditable snapshot into client-facing conclusions only."""
    if snapshot.get("status") != "ok":
        return {
            "date": snapshot.get("trade_date"),
            "title": "资金结构结论",
            "bottom_line": "今天本地 SME 数据不足，暂不生成结论。",
            "status": snapshot.get("status", "blocked"),
        }

    capital = snapshot["capital_state"]
    tags = capital.get("state_tags", [])
    primary = snapshot["beneficiary_buckets"]["primary_beneficiaries"]
    secondary = snapshot["beneficiary_buckets"]["secondary_beneficiaries"]
    defensive = snapshot["beneficiary_buckets"]["desensitized_assets"]
    repair = snapshot["beneficiary_buckets"]["suppressed_repair_candidates"]
    crowded = snapshot["strong_return_weak_flow"]
    inflows = snapshot["flow_inflows"]
    outflows = snapshot["flow_outflows"]

    primary_names = _names(primary) or _names(inflows, 5)
    secondary_names = [n for n in _names(secondary, 5) if n not in set(primary_names)]
    defensive_names = _names(defensive, 5)
    repair_names = _names(repair, 5)
    crowded_names = _names(crowded, 5)
    outflow_names = _names(outflows, 5)

    if "risk_appetite_up" in tags:
        stance = "今天资金偏进攻，但不是无脑普涨，仍要防范涨幅强、资金跟不上的尾端交易。"
    elif "risk_appetite_down" in tags:
        stance = "今天资金偏谨慎，先看防御承接和流出收敛，进攻方向只适合小仓位观察。"
    elif "defensive_switch" in tags:
        stance = "今天资金有明显防御切换，低敏感资产优先级高于高弹性主题。"
    else:
        stance = "今天是结构性轮动，方向选择比判断指数更重要。"

    return {
        "date": snapshot["trade_date"],
        "title": "资金结构结论",
        "bottom_line": stance,
        "market_read": snapshot["market_overview"].get("interpretation"),
        "capital_state": _state_text(tags),
        "focus_now": primary_names[:5],
        "secondary_watch": secondary_names[:5],
        "defensive_or_desensitized": defensive_names[:5],
        "repair_candidates": repair_names[:5],
        "avoid_or_reduce": outflow_names[:5],
        "crowding_risk": crowded_names[:5],
        "who_is_buying": _actor_lines(inflows, 3),
        "who_is_selling": _actor_lines(outflows, 3),
        "scenario": {
            "风险继续升级": f"优先看{('、'.join(defensive_names[:3]) or '防御承接方向')}，回避{('、'.join(crowded_names[:3]) or '涨幅强但资金不足的方向')}。",
            "风险缓和": f"优先看{('、'.join(repair_names[:3]) or '流出收敛后的低位修复方向')}。",
            "事件拖延": f"资金大概率在{('、'.join(primary_names[:3]) or '当前主线')}内部轮动，并向{('、'.join(secondary_names[:3]) or '二级受益方向')}扩散。",
        },
        "external_variable_note": snapshot["external_variables"].get("summary") or "外部变量未注入，本结论只基于本地资金流和市场结构。",
    }


def build_client_brief(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build a plain-language brief for end users.

    The brief intentionally hides formulas, model names, thresholds, and raw
    evidence arrays. It only exposes decision-useful conclusions.
    """
    conclusion = snapshot.get("client_conclusion") or build_client_conclusion(snapshot)
    disclaimer = {
        "short_header_zh": SHORT_HEADER_ZH,
        "short_header_en": SHORT_HEADER_EN,
        "footer_short_zh": FOOTER_SHORT_ZH,
        "footer_short_en": FOOTER_SHORT_EN,
        "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH,
        "paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
    }
    if snapshot.get("status") != "ok":
        return {
            "date": snapshot.get("trade_date"),
            "title": "资金结构简报",
            "status": "blocked",
            "summary": "本地数据不足，暂不生成昨日资金结构简报。",
            "disclaimer": disclaimer,
        }

    generated_at_bjt = dt.datetime.now(tz=BJT)
    overview = snapshot["market_overview"]
    breadth = overview.get("breadth") or {}
    inflows = snapshot.get("flow_inflows") or []
    outflows = snapshot.get("flow_outflows") or []
    crowded = snapshot.get("strong_return_weak_flow") or []
    repair = snapshot.get("suppressed_repair") or []
    external = snapshot.get("external_variables") or {}

    def _support_text(item: dict[str, Any]) -> str:
        profile = item.get("actor_profile") or {}
        parts = [
            f"主力 {item.get('main_net_bn_yuan', 0):+.2f} 亿",
            f"超大单 {profile.get('extra_large_net_bn_yuan', 0):+.2f} 亿",
            f"大单 {profile.get('large_net_bn_yuan', 0):+.2f} 亿",
            f"小中单 {profile.get('retail_net_bn_yuan', 0):+.2f} 亿",
        ]
        inst = _f(profile.get("institution_lhb_proxy_net_bn_yuan"))
        inst_count = _i(profile.get("institution_lhb_proxy_stock_count"))
        if inst_count:
            parts.append(f"机构席位代理 {inst:+.2f} 亿/{inst_count} 股")
        event_net = _f(profile.get("lhb_event_net_bn_yuan"))
        event_count = _i(profile.get("lhb_event_stock_count"))
        if event_count:
            parts.append(f"龙虎榜事件 {event_net:+.2f} 亿/{event_count} 股")
        return "；".join(parts)

    def _support_metrics(item: dict[str, Any]) -> dict[str, Any]:
        profile = item.get("actor_profile") or {}
        return {
            "主力": item.get("main_net_bn_yuan", 0),
            "超大单": profile.get("extra_large_net_bn_yuan", 0),
            "大单": profile.get("large_net_bn_yuan", 0),
            "小中单": profile.get("retail_net_bn_yuan", 0),
            "机构席位": profile.get("institution_lhb_proxy_net_bn_yuan", 0),
            "龙虎榜": profile.get("lhb_event_net_bn_yuan", 0),
        }

    def _brief_items(items: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
        lines = []
        for item in items[:5]:
            name = item.get("l2_name")
            if not name:
                continue
            if kind == "inflow":
                typ = item.get("inflow_type")
                if typ == "chase_high":
                    reason = "偏追高，注意别追在情绪末端"
                elif typ == "institutional_absorption":
                    reason = "偏机构承接，价格还没完全反映"
                elif typ == "defensive":
                    reason = "偏防御承接"
                elif typ == "long_config":
                    reason = "偏中期配置"
                elif typ == "event_trade":
                    reason = "偏事件博弈"
                else:
                    reason = "偏战术流入"
            elif kind == "outflow":
                typ = item.get("outflow_type")
                if typ == "panic_sell":
                    reason = "偏恐慌性卖出"
                elif typ == "high_low_switch":
                    reason = "偏高低切换"
                elif typ == "controlled_outflow":
                    reason = "流出已经收敛"
                else:
                    reason = "偏主动降仓"
            else:
                reason = ""
            lines.append({
                "name": name,
                "conclusion": reason,
                "support": _support_text(item),
                "metrics": _support_metrics(item),
                "actor": (item.get("actor_profile") or {}).get("verdict") or "资金主体不清晰",
                "return_1d": item.get("return_1d"),
                "return_5d": item.get("return_5d"),
                "main_positive_breadth": item.get("main_positive_breadth"),
            })
        return lines

    observation_date = snapshot["trade_date"]
    return {
        "date": observation_date,
        "observation_date": observation_date,
        "observation_date_zh": _zh_date(observation_date),
        "generated_at_bjt": generated_at_bjt.strftime("%Y-%m-%d %H:%M:%S"),
        "generated_at_bjt_zh": generated_at_bjt.strftime("%Y年%m月%d日 %H:%M:%S"),
        "title": f"{_zh_date(observation_date)}资金结构简报",
        "status": "ok",
        "one_line": conclusion["bottom_line"],
        "market_temperature": {
            "conclusion": overview.get("interpretation"),
            "breadth": f"上涨 {breadth.get('up_count')} 家，下跌 {breadth.get('down_count')} 家，成交额约 {breadth.get('amount_bn_yuan')} 亿元。",
            "intraday_note": "真实分时暂未入库，本版以日频结构判断为主。",
        },
        "main_outflows": _brief_items(outflows, "outflow"),
        "main_inflows": _brief_items(inflows, "inflow"),
        "crowding_or_tail_risk": [
            {
                "name": x.get("l2_name"),
                "conclusion": "涨幅强但资金跟随不足，谨防拥挤或尾端波动",
                "support": _support_text(x),
                "metrics": _support_metrics(x),
            }
            for x in crowded[:5]
            if x.get("l2_name")
        ],
        "repair_elasticity": [
            {
                "name": x.get("l2_name"),
                "conclusion": "被压制较久或流出收敛，风险缓和时有修复弹性",
                "support": _support_text(x),
                "metrics": _support_metrics(x),
            }
            for x in repair[:5]
            if x.get("l2_name")
        ],
        "directions": {
            "一级受益方向": conclusion.get("focus_now") or [],
            "二级受益方向": conclusion.get("secondary_watch") or [],
            "脱敏资产方向": conclusion.get("defensive_or_desensitized") or [],
            "被压制但具备反弹弹性的方向": conclusion.get("repair_candidates") or [],
            "需要回避或减仓的方向": conclusion.get("avoid_or_reduce") or [],
        },
        "capital_state": conclusion.get("capital_state"),
        "flow_basis": "主要流入/流出按主力资金排序。主力=超大单+大单净额；小中单作为散户代理；机构席位和龙虎榜只覆盖披露样本，作为辅助验证，不代表全市场机构全量持仓。",
        "section_definitions": {
            "拥挤或尾端风险": "价格已经很强，但主力资金和扩散没有跟上，后续更容易出现冲高回落或剧烈波动。",
            "修复弹性": "前期被压制、下跌或流出已开始收敛，一旦外部风险缓和，反弹弹性可能更好。",
            "方向划分": "这是本简报最重要的结论区：优先看一级/二级受益，防守看脱敏资产，风险缓和看修复弹性，回避资金明显流出的方向。",
        },
        "external_variable": external.get("summary") or "外部变量未注入。生产环境建议由联网 LLM/新闻源先生成摘要，再传入本简报。",
        "scenario_1_3_days": conclusion.get("scenario") or {},
        "what_to_do": [
            "先看二级受益和确认承接方向，不盲目追高。",
            "对拥挤风险方向降低追涨冲动，等回踩或资金再确认。",
            "若外部风险升温，优先看脱敏资产；若风险缓和，再看修复弹性方向。",
        ],
        "disclaimer": disclaimer,
    }


def render_client_brief_markdown(brief: dict[str, Any]) -> str:
    """Render the client brief as process-light Markdown."""
    if brief.get("status") != "ok":
        return f"# {brief.get('title', '资金结构简报')}\n\n{brief.get('summary', '暂无结论。')}\n"

    def lines(items: list[Any]) -> str:
        if not items:
            return "- 暂无明确方向"
        out = []
        for x in items:
            if isinstance(x, dict):
                out.append(f"- {x.get('name')}：{x.get('conclusion')}（{x.get('support')}）")
            else:
                out.append(f"- {x}")
        return "\n".join(out)

    directions = brief["directions"]
    scenarios = brief["scenario_1_3_days"]
    return "\n".join([
        f"# {brief['title']}",
        "",
        f"观察日期：{brief['observation_date_zh']} / 报告日期：北京时间 {brief['generated_at_bjt']}",
        "",
        f"**一句话结论**：{brief['one_line']}",
        "",
        "## 最重要的方向",
        f"- 一级受益方向：{'、'.join(directions['一级受益方向']) or '暂无'}",
        f"- 二级受益方向：{'、'.join(directions['二级受益方向']) or '暂无'}",
        f"- 脱敏资产方向：{'、'.join(directions['脱敏资产方向']) or '暂无'}",
        f"- 被压制但具备反弹弹性的方向：{'、'.join(directions['被压制但具备反弹弹性的方向']) or '暂无'}",
        f"- 需要回避或减仓的方向：{'、'.join(directions['需要回避或减仓的方向']) or '暂无'}",
        brief["section_definitions"]["方向划分"],
        "",
        "## 市场温度",
        brief["market_temperature"]["conclusion"] or "结构不清晰。",
        brief["market_temperature"]["breadth"],
        brief["market_temperature"]["intraday_note"],
        "",
        "## 资金流向",
        brief["flow_basis"],
        "",
        "**主要流入**",
        lines(brief["main_inflows"]),
        "",
        "**主要流出**",
        lines(brief["main_outflows"]),
        "",
        "## 风险和弹性",
        "**拥挤或尾端风险**",
        brief["section_definitions"]["拥挤或尾端风险"],
        "",
        lines(brief["crowding_or_tail_risk"]),
        "",
        "**修复弹性**",
        brief["section_definitions"]["修复弹性"],
        "",
        lines(brief["repair_elasticity"]),
        "",
        "## 当前资金状态",
        brief["capital_state"] or "结构不清晰",
        "",
        "## 外部变量",
        brief["external_variable"],
        "",
        "## 未来 1-3 个交易日情景",
        f"- 风险继续升级：{scenarios.get('风险继续升级', '暂无')}",
        f"- 风险缓和：{scenarios.get('风险缓和', '暂无')}",
        f"- 事件拖延：{scenarios.get('事件拖延', '暂无')}",
        "",
        "## 简单决策",
        lines(brief["what_to_do"]),
        "",
    ])


def render_client_brief_html(brief: dict[str, Any]) -> str:
    """Render the SME client brief with a standalone HTML template.

    The template intentionally lives under ``ifa/families/sme/templates`` and
    does not include/import the legacy SmartMoney report templates. This keeps
    SME's customer-facing output insulated from old-template edits while still
    borrowing the same high-level "market pulse + direction cards" information
    hierarchy.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    template_dir = Path(__file__).resolve().parents[1] / "templates"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["signed_bn"] = lambda value: f"{_f(value):+.2f}"
    env.filters["flow_class"] = lambda value: "pos" if _f(value) > 0 else ("neg" if _f(value) < 0 else "zero")
    return env.get_template("brief.html").render(brief=brief)


def add_llm_narrative(conclusion: dict[str, Any], *, llm_client: Any | None = None) -> dict[str, Any]:
    """Optionally compress deterministic conclusions into a human narrative.

    LLM is only allowed to rewrite the already-decided fields. It must not add
    sectors, numbers, events, or change the deterministic stance. This keeps
    the investment decision reproducible while allowing a friendlier final
    report style.
    """
    if conclusion.get("status") == "blocked":
        return conclusion
    try:
        from ifa.core.llm import LLMClient

        client = llm_client or LLMClient(request_timeout=45.0)
        resp = client.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是A股资金流投顾报告编辑。只基于用户给定JSON改写，"
                        "不得新增板块、公司、数字、事件或交易建议。输出严格JSON："
                        "{\"narrative\": \"不超过180字，通俗、结论优先，不解释模型过程\"}。"
                    ),
                },
                {"role": "user", "content": json.dumps(conclusion, ensure_ascii=False, default=str)},
            ],
            max_tokens=320,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        data = resp.parse_json()
        narrative = str(data.get("narrative") or "").strip()
        if narrative:
            enriched = dict(conclusion)
            enriched["llm_narrative"] = {
                "status": "ok",
                "text": narrative,
                "model": resp.model,
                "endpoint": resp.endpoint,
            }
            return enriched
    except Exception as exc:  # noqa: BLE001
        log.warning("SME client narrative LLM failed: %s", exc)
    enriched = dict(conclusion)
    enriched["llm_narrative"] = {"status": "degraded", "text": ""}
    return enriched


def build_market_structure_snapshot(
    engine,
    *,
    trade_date: dt.date | None = None,
    top_n: int = 8,
    external_summary: str | None = None,
    params: dict[str, Any] | None = None,
    params_profile: str | None = None,
    params_path: str | None = None,
) -> dict[str, Any]:
    """Build the deterministic SME MVP1 market-structure strategy snapshot."""
    p = params or load_market_structure_params(profile=params_profile, path=params_path)
    p_meta = p.get("_meta") or {}
    d = trade_date or latest_trade_date(engine)
    overview = _load_market_overview(engine, d, p)
    rows = _load_sector_rows(engine, d)
    data_gaps = [
        "SME MVP1 尚未持久化真实分时走势；当前用日频 OHLC/成交额/涨跌家数做结构解释。",
        "外部变量需要联网 LLM/新闻源；本地 CLI 只接收 external_summary，不把不可复现网页内容写入核心数据层。",
    ]
    if not rows:
        return {
            "status": "blocked",
            "trade_date": d,
            "message": "No SME sector rows found for trade date",
            "data_gaps": data_gaps,
        }

    inflow_items: list[dict[str, Any]] = []
    outflow_items: list[dict[str, Any]] = []
    for row in rows:
        main_net = _f(row.get("main_net_yuan"))
        if main_net > 0:
            typ, reasons = classify_inflow(row, p)
            row["inflow_type"] = typ
            row["rank_score"] = _rank_inflow(row, p)
            inflow_items.append(_with_reason(row, "inflow", reasons))
        elif main_net < 0:
            typ, reasons = classify_outflow(row, p)
            row["outflow_type"] = typ
            outflow_items.append(_with_reason(row, "outflow", reasons))

    inflow_items.sort(key=lambda r: (r.get("rank_score") or 0.0, r["main_net_bn_yuan"]), reverse=True)
    outflow_items.sort(key=lambda r: r["main_net_bn_yuan"])

    weak_p = p.get("strong_return_weak_flow") or {}
    strong_return_weak_flow = [
        _sector_summary(r)
        for r in rows
        if _f(r.get("sector_return_sw_index")) >= _f(weak_p.get("return_min"), 2.0)
        and _f(r.get("main_net_ratio")) < _f(weak_p.get("main_net_ratio_max"), 0.003)
        and _f(r.get("main_positive_breadth")) < _f(weak_p.get("main_positive_breadth_max"), 0.48)
    ]
    strong_return_weak_flow.sort(key=lambda r: r["return_1d"], reverse=True)

    repair_p = p.get("repair") or {}
    suppressed_repair = [
        _sector_summary(r)
        for r in rows
        if (
            _f(r.get("sector_return_sw_index")) <= _f(repair_p.get("weak_return_max"), -1.0)
            and _f(r.get("main_net_yuan")) < 0
            and r.get("prev_main_net_yuan") is not None
            and _f(r.get("main_net_yuan")) > _f(r.get("prev_main_net_yuan"))
        )
        or (
            _f(r.get("return_10d")) <= _f(repair_p.get("compressed_return_10d_max"), -3.0)
            and _f(r.get("main_net_yuan")) > 0
            and r.get("current_state") in {"rebound", "dormant", "ignition"}
        )
    ]
    suppressed_repair.sort(key=lambda r: (r["main_net_bn_yuan"], -abs(r["return_1d"])), reverse=True)

    primary = [
        r
        for r in inflow_items
        if _is_primary_candidate(r, p)
    ][:top_n]
    secondary = [
        r
        for r in inflow_items
        if r["l2_code"] not in {x["l2_code"] for x in primary}
        and r["main_net_ratio"] > 0
    ][:top_n]
    defensive = [
        r
        for r in inflow_items
        if r.get("inflow_type") in {"defensive", "institutional_absorption"}
        or _contains(r.get("l2_name"), DEFENSIVE_KEYWORDS)
    ][:top_n]

    capital_state = assess_capital_state(
        breadth=overview["breadth"],
        inflows=inflow_items,
        outflows=outflow_items,
        strong_return_weak_flow=strong_return_weak_flow,
        suppressed_repair=suppressed_repair,
        params=p,
    )

    payload = {
        "status": "ok",
        "trade_date": d,
        "logic_version": f"{SME_MARKET_STRUCTURE_LOGIC_VERSION}/{p_meta.get('profile')}/{p_meta.get('hash')}",
        "params_profile": p_meta.get("profile"),
        "params_hash": p_meta.get("hash"),
        "market_overview": overview,
        "flow_outflows": outflow_items[:top_n],
        "flow_inflows": inflow_items[:top_n],
        "strong_return_weak_flow": strong_return_weak_flow[:top_n],
        "suppressed_repair": suppressed_repair[:top_n],
        "beneficiary_buckets": {
            "primary_beneficiaries": primary,
            "secondary_beneficiaries": secondary,
            "desensitized_assets": defensive,
            "suppressed_repair_candidates": suppressed_repair[:top_n],
        },
        "capital_state": capital_state,
        "scenario_1_3_trade_days": _scenario(primary, suppressed_repair, defensive),
        "external_variables": {
            "status": "supplied" if external_summary else "not_supplied",
            "summary": external_summary,
            "integration_note": "生产版可由 ifa LLM/web tool 先生成 external_summary，再传入本解释器；核心 SME 结果不依赖网页内容。",
        },
        "data_gaps": data_gaps,
    }
    payload["client_conclusion"] = build_client_conclusion(payload)
    return payload


def _direction_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"l2_code": r.get("l2_code"), "l2_name": r.get("l2_name")} for r in items if r.get("l2_code")]


def persist_market_structure_snapshot(engine, snapshot: dict[str, Any]) -> int:
    """Persist one daily market-structure snapshot for tuning/backtests.

    This is an intermediate strategy result. Persisting it is intentional: once
    a rule/model portfolio uses these classifications, we need stable historical
    records for OOC/OOS validation instead of regenerating evolving conclusions
    at report time.
    """
    if snapshot.get("status") != "ok":
        return 0
    buckets = snapshot["beneficiary_buckets"]
    params = {
        "trade_date": snapshot["trade_date"],
        "logic_version": snapshot.get("logic_version") or SME_MARKET_STRUCTURE_LOGIC_VERSION,
        "capital_state": snapshot["capital_state"].get("primary_state"),
        "state_tags_json": _json_text(snapshot["capital_state"].get("state_tags") or []),
        "primary_directions_json": _json_text(_direction_rows(buckets.get("primary_beneficiaries") or [])),
        "secondary_directions_json": _json_text(_direction_rows(buckets.get("secondary_beneficiaries") or [])),
        "defensive_directions_json": _json_text(_direction_rows(buckets.get("desensitized_assets") or [])),
        "repair_directions_json": _json_text(_direction_rows(buckets.get("suppressed_repair_candidates") or [])),
        "avoid_directions_json": _json_text(_direction_rows(snapshot.get("flow_outflows") or [])),
        "crowding_risk_json": _json_text(_direction_rows(snapshot.get("strong_return_weak_flow") or [])),
        "snapshot_json": _json_text(snapshot),
        "client_conclusion_json": _json_text(snapshot.get("client_conclusion") or {}),
        "external_summary": (snapshot.get("external_variables") or {}).get("summary"),
        "quality_flag": "ok",
    }
    sql = text("""
        INSERT INTO sme.sme_market_structure_daily (
            trade_date, logic_version, capital_state, state_tags_json,
            primary_directions_json, secondary_directions_json,
            defensive_directions_json, repair_directions_json,
            avoid_directions_json, crowding_risk_json,
            snapshot_json, client_conclusion_json, external_summary,
            quality_flag, computed_at
        )
        VALUES (
            :trade_date, :logic_version, :capital_state, CAST(:state_tags_json AS jsonb),
            CAST(:primary_directions_json AS jsonb), CAST(:secondary_directions_json AS jsonb),
            CAST(:defensive_directions_json AS jsonb), CAST(:repair_directions_json AS jsonb),
            CAST(:avoid_directions_json AS jsonb), CAST(:crowding_risk_json AS jsonb),
            CAST(:snapshot_json AS jsonb), CAST(:client_conclusion_json AS jsonb), :external_summary,
            :quality_flag, now()
        )
        ON CONFLICT (trade_date) DO UPDATE SET
            logic_version = EXCLUDED.logic_version,
            capital_state = EXCLUDED.capital_state,
            state_tags_json = EXCLUDED.state_tags_json,
            primary_directions_json = EXCLUDED.primary_directions_json,
            secondary_directions_json = EXCLUDED.secondary_directions_json,
            defensive_directions_json = EXCLUDED.defensive_directions_json,
            repair_directions_json = EXCLUDED.repair_directions_json,
            avoid_directions_json = EXCLUDED.avoid_directions_json,
            crowding_risk_json = EXCLUDED.crowding_risk_json,
            snapshot_json = EXCLUDED.snapshot_json,
            client_conclusion_json = EXCLUDED.client_conclusion_json,
            external_summary = EXCLUDED.external_summary,
            quality_flag = EXCLUDED.quality_flag,
            computed_at = now()
    """)
    with engine.begin() as conn:
        result = conn.execute(sql, params)
    return int(result.rowcount or 0)
