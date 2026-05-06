"""Shared building blocks for market morning / noon / evening reports."""
from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy.engine import Engine

from ifa.core.llm import LLMClient
from ifa.core.render import sparkline_svg
from ifa.core.report.run import (
    insert_judgment,
    insert_model_output,
)
from ifa.core.report.timezones import BJT, fmt_bjt, to_bjt
from ifa.core.tushare import TuShareClient
from ifa.families.macro.morning import _safe_chat_json
from ifa.families.tech.focus import (
    DEFAULT_IMPORTANT,
    DEFAULT_REGULAR,
    FocusStock,
)

from . import data as mdata
from . import prompts


@dataclass
class MarketCtx:
    engine: Engine
    llm: LLMClient
    tushare: TuShareClient
    run: Any                   # ReportRun
    user: str
    indices: list[mdata.IndexSnap]
    breadth: mdata.BreadthSnap
    flows: mdata.FlowsSnap
    sw_rotation: list[mdata.SectorBar]
    main_lines: list[mdata.SectorBar]
    fund_top: list[mdata.StockSnap]
    dragon_tiger: list[mdata.StockSnap]
    news_df: Any
    aux_summaries: dict[str, mdata.AuxReportSummary]
    important_focus: list[FocusStock]
    regular_focus: list[FocusStock]
    important_focus_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    regular_focus_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    morning_hypotheses: list[dict] = field(default_factory=list)
    noon_hypotheses: list[dict] = field(default_factory=list)
    on_log: Callable[[str], None] = lambda m: None


# ─── helpers ──────────────────────────────────────────────────────────────

def _fmt_pct(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:+.{decimals}f}%"


def _direction(v: float | None, threshold: float = 0.05) -> str:
    if v is None:
        return "flat"
    if v > threshold: return "up"
    if v < -threshold: return "down"
    return "flat"


def _fmt_amount_yi(amount_yuan: float | None) -> str:
    """Format amount in 元 → '亿' or '万亿' for display."""
    if amount_yuan is None:
        return "—"
    if amount_yuan >= 1e12:
        return f"{amount_yuan / 1e12:.2f} 万亿"
    if amount_yuan >= 1e8:
        return f"{amount_yuan / 1e8:.0f} 亿"
    return f"{amount_yuan / 1e4:.0f} 万"


def _fmt_count(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1e8:
        return f"{v/1e8:.2f}亿"
    if v >= 1e4:
        return f"{v/1e4:.1f}万"
    return f"{v:,.0f}"


def _persist_model_output(ctx: MarketCtx, *, section_key: str, prompt_name: str,
                           parsed: Any, resp: Any, status: str):
    if resp is None:
        return None
    return insert_model_output(
        ctx.engine,
        report_run_id=ctx.run.report_run_id,
        section_key=section_key,
        prompt_name=prompt_name,
        prompt_version=prompts.PROMPT_BUNDLE_VERSION,
        model_name=resp.model,
        endpoint=resp.endpoint,
        parsed_json=parsed if isinstance(parsed, (dict, list)) else None,
        status=status,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        latency_seconds=resp.latency_seconds,
    )


# ─── Pre-fetch ─────────────────────────────────────────────────────────────

def prefetch_market_data(
    *,
    tushare: TuShareClient,
    engine: Engine,
    on_date: dt.date,
    aux_report_type: str = "morning_long",
    end_bjt: dt.datetime,
    on_log: Callable[[str], None],
) -> dict[str, Any]:
    on_log("fetching index family + history…")
    indices = mdata.fetch_index_family(tushare, on_date=on_date, history_days=10)
    on_log("computing whole-A breadth + 涨跌停 + 连板高度…")
    breadth = mdata.fetch_breadth(tushare, on_date=on_date)
    on_log("fetching SW industry rotation (sw_daily)…")
    sw_rotation = mdata.fetch_sw_rotation(tushare, on_date=on_date)
    on_log("fetching main-line candidates (SW L2 dynamic)…")
    main_lines = mdata.fetch_main_lines(engine, on_date=on_date)
    on_log("fetching top fund-flow stocks…")
    fund_top = mdata.fetch_fund_flow_top(tushare, on_date=on_date, top_n=20)
    on_log("fetching dragon-tiger list…")
    dragon_tiger = mdata.fetch_dragon_tiger(tushare, on_date=on_date, top_n=15)
    mdata.enrich_stocks(tushare, on_date=on_date, stocks=fund_top + dragon_tiger)
    on_log("fetching north/south + margin flows…")
    flows = mdata.fetch_flows(tushare, on_date=on_date)
    on_log(f"reading three-aux summary for {on_date} ({aux_report_type})…")
    aux_summaries = mdata.fetch_three_aux_summaries(engine, report_date=on_date,
                                                      report_type=aux_report_type)
    on_log("filtering market news (last 24h)…")
    news_df = mdata.fetch_market_news(tushare, end_bjt=end_bjt, lookback_hours=24, max_keep=30)
    return {
        "indices": indices, "breadth": breadth, "flows": flows,
        "sw_rotation": sw_rotation, "main_lines": main_lines,
        "fund_top": fund_top, "dragon_tiger": dragon_tiger,
        "news_df": news_df, "aux_summaries": aux_summaries,
        "important_focus": list(DEFAULT_IMPORTANT),
        "regular_focus": list(DEFAULT_REGULAR),
    }


def enrich_market_focus(
    *, tushare: TuShareClient, on_date: dt.date,
    important: list[FocusStock], regular: list[FocusStock],
    history_days: int = 10,
    slot: str = "morning",
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Pull daily/daily_basic/moneyflow + slot-appropriate sparkline data.

    Sparkline 口径（per slot）:
      - morning: 过去 N 个交易日 EOD 收盘 (`daily`)
      - noon:    今日 09:30..11:30 上午分时 5MIN K (`rt_min_daily`)
      - evening: 今日 09:30..15:00 全天分时 5MIN K (`rt_min_daily`)

    Each row's `history_close` holds the close-series, `history_caption` holds the
    label ("近 10 日趋势 / 今日上午分时 / 今日全天分时") for UI.
    """
    end = on_date.strftime("%Y%m%d")
    start_h = (on_date - dt.timedelta(days=history_days * 2 + 5)).strftime("%Y%m%d")
    all_codes = list({s.ts_code for s in important + regular})
    if not all_codes:
        return {}, {}

    try:
        df_d = tushare.call("daily", trade_date=end)
    except Exception:
        df_d = pd.DataFrame()
    try:
        df_db = tushare.call("daily_basic", trade_date=end)
    except Exception:
        df_db = pd.DataFrame()
    try:
        df_mf = tushare.call("moneyflow", trade_date=end)
    except Exception:
        df_mf = pd.DataFrame()

    by_d = {r.ts_code: r for r in df_d.itertuples()} if not df_d.empty else {}
    by_db = {r.ts_code: r for r in df_db.itertuples()} if not df_db.empty else {}
    by_mf = (
        {r.ts_code: float(r.net_mf_amount) for r in df_mf.itertuples() if r.net_mf_amount is not None}
        if not df_mf.empty else {}
    )

    # Slot-aware sparkline caption
    if slot == "noon":
        spark_caption = "今日上午分时（5MIN）"
    elif slot == "evening":
        spark_caption = "今日全天分时（5MIN）"
    else:
        spark_caption = f"近 {history_days} 日趋势（日 K 收盘）"

    def _fetch_intraday_5min(ts_code: str, *, until_hhmm: str | None = None) -> tuple[list[float], list[str]]:
        """Use `pro.rt_min_daily` for today's 5-min bars from open. until_hhmm e.g. '11:30'."""
        try:
            df = tushare.call("rt_min_daily", ts_code=ts_code, freq="5MIN")
            if df is None or df.empty:
                return [], []
            df = df.sort_values("time").reset_index(drop=True)
            if until_hhmm:
                cutoff = f"{on_date.strftime('%Y-%m-%d')} {until_hhmm}:00"
                df = df[df["time"] <= cutoff]
            closes = [float(v) if pd.notna(v) else None for v in df["close"]]
            times = df["time"].astype(str).tolist()
            return closes, times
        except Exception:
            return [], []

    def _build(spec: FocusStock) -> dict[str, Any]:
        d = by_d.get(spec.ts_code)
        db = by_db.get(spec.ts_code)
        out: dict[str, Any] = {
            "ts_code": spec.ts_code,
            "name": spec.display_name,
            "layer": spec.layer,
            "sub_theme": spec.sub_theme,
            "close": float(getattr(d, "close")) if d and getattr(d, "close", None) is not None else None,
            "pct_change": float(getattr(d, "pct_chg")) if d and getattr(d, "pct_chg", None) is not None else None,
            "amount": float(getattr(d, "amount")) if d and getattr(d, "amount", None) is not None else None,
            "turnover_rate": float(getattr(db, "turnover_rate")) if db and getattr(db, "turnover_rate", None) is not None else None,
            "pe": float(getattr(db, "pe_ttm")) if db and getattr(db, "pe_ttm", None) is not None else None,
            "moneyflow_net": by_mf.get(spec.ts_code),
            "history_close": [],
            "history_dates": [],
            "history_caption": spark_caption,
        }
        if slot == "noon":
            closes, times = _fetch_intraday_5min(spec.ts_code, until_hhmm="11:30")
            out["history_close"] = closes
            out["history_dates"] = times
        elif slot == "evening":
            closes, times = _fetch_intraday_5min(spec.ts_code, until_hhmm=None)
            out["history_close"] = closes
            out["history_dates"] = times
        else:
            try:
                hd = tushare.call("daily", ts_code=spec.ts_code,
                                   start_date=start_h, end_date=end)
                if hd is not None and not hd.empty:
                    hd = hd.sort_values("trade_date").tail(history_days)
                    out["history_close"] = [float(v) if pd.notna(v) else None for v in hd["close"]]
                    out["history_dates"] = hd["trade_date"].astype(str).tolist()
            except Exception:
                pass
        # If intraday fetch returned empty (data not yet available), fallback to EOD daily
        if not out["history_close"] and slot in ("noon", "evening"):
            try:
                hd = tushare.call("daily", ts_code=spec.ts_code,
                                   start_date=start_h, end_date=end)
                if hd is not None and not hd.empty:
                    hd = hd.sort_values("trade_date").tail(history_days)
                    out["history_close"] = [float(v) if pd.notna(v) else None for v in hd["close"]]
                    out["history_dates"] = hd["trade_date"].astype(str).tolist()
                    out["history_caption"] = f"近 {history_days} 日趋势（日 K，分时数据未到）"
            except Exception:
                pass
        return out

    imp_data = {s.ts_code: _build(s) for s in important}
    reg_data = {s.ts_code: _build(s) for s in regular}
    return imp_data, reg_data


# ─── Section builders shared across morning/noon/evening ──────────────────

FAMILY_DISPLAY = {"macro": "宏观", "asset": "Asset / 跨资产", "tech": "Tech / AI 五层"}


def build_index_panel_section(ctx: MarketCtx, *, order: int, title: str, key: str) -> dict:
    rows = []
    for snap in ctx.indices:
        spark = sparkline_svg(snap.history_close, width=130, height=28)
        rows.append({
            "ts_code": snap.ts_code, "name": snap.name, "role": snap.role,
            "close_display": f"{snap.close:,.2f}" if snap.close is not None else "—",
            "pct_display": _fmt_pct(snap.pct_change),
            "pct_dir": _direction(snap.pct_change),
            "amount_display": _fmt_amount_yi(snap.amount * 1000 if snap.amount else None),
            "spark_svg": spark, "commentary": "",
        })

    b = ctx.breadth
    breadth_cells = []
    if b.total_amount is not None:
        delta = None
        delta_dir = "flat"
        if b.total_amount_prev is not None:
            d = b.total_amount - b.total_amount_prev
            delta = f"{d:+.2f} 万亿"
            delta_dir = "up" if d > 0 else "down" if d < 0 else "flat"
        breadth_cells.append({
            "label": "全 A 成交额",
            "value": f"{b.total_amount:.2f}",
            "unit": "万亿元",
            "delta": delta, "delta_dir": delta_dir,
            "note": "风险偏好与行情级别的核心衡量",
        })
    if b.up_count is not None:
        breadth_cells.append({
            "label": "上涨家数 / 下跌 / 平",
            "value": f"{b.up_count} / {b.down_count} / {b.flat_count}",
            "unit": "家",
            "note": f"全 A 平均涨跌 {b.avg_pct_change:+.2f}%" if b.avg_pct_change is not None else "",
        })
    if b.limit_up_count is not None:
        breadth_cells.append({
            "label": "涨停 / 跌停",
            "value": f"{b.limit_up_count} / {b.limit_down_count or 0}",
            "unit": "家",
            "note": f"炸板率 {b.broke_limit_pct*100:.0f}%" if b.broke_limit_pct is not None else "",
        })
    if b.max_consec_streak is not None:
        breadth_cells.append({
            "label": "连板高度",
            "value": f"{b.max_consec_streak}",
            "unit": "连板",
            "note": "短线情绪强度",
        })
    if ctx.flows.north_money is not None:
        breadth_cells.append({
            "label": "上一交易日北向",
            "value": f"{ctx.flows.north_money:+.1f}",
            "unit": "亿",
            "delta_dir": _direction(ctx.flows.north_money),
            "note": "外资态度",
        })
    if ctx.flows.margin_total is not None:
        breadth_cells.append({
            "label": "两融余额",
            "value": f"{ctx.flows.margin_total:.2f}",
            "unit": "万亿元",
            "delta": f"Δ {ctx.flows.margin_change:+.3f}" if ctx.flows.margin_change is not None else "",
            "delta_dir": _direction(ctx.flows.margin_change),
            "note": "杠杆资金 / 活跃资金",
        })
    return {
        "key": key, "title": title, "order": order, "type": "index_panel",
        "content_json": {"indices": rows, "breadth": breadth_cells},
    }


def build_three_aux_section(ctx: MarketCtx, *, order: int, title: str, key: str) -> dict:
    aux = ctx.aux_summaries
    aux_blob = []
    for f in ("macro", "asset", "tech"):
        s = aux.get(f)
        if s:
            aux_blob.append({
                "family": f,
                "headline": s.headline, "tone_or_state": s.tone_or_state,
                "summary": s.summary, "bullets": s.bullets,
                "template_version": s.template_version,
            })
        else:
            aux_blob.append({"family": f, "headline": None, "summary": None})
    user = f"""
=== 三辅报告头部摘要 ===
{json.dumps(aux_blob, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.THREE_AUX_INSTRUCTIONS}

=== 输出 schema ===
{prompts.THREE_AUX_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key=key,
                                  prompt_name=key, parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"integrated_summary": "", "rows": []}
    # Ensure rows have family_display + present even when LLM didn't include
    rows = content.get("rows") or []
    if not rows:
        rows = [
            {"family": f, "today_conclusion": (aux.get(f).headline if aux.get(f) else "数据缺失"),
             "impact_level": "—", "a_share_focus": ""}
            for f in ("macro", "asset", "tech")
        ]
    for r in rows:
        r["family_display"] = FAMILY_DISPLAY.get(r.get("family"), r.get("family"))
    content["rows"] = rows
    return {
        "key": key, "title": title, "order": order, "type": "three_aux_summary",
        "content_json": content, "prompt_name": key, "model_output_id": moid,
    }


def build_rotation_section(ctx: MarketCtx, *, order: int, title: str, key: str) -> dict:
    valid_sw = [s for s in ctx.sw_rotation if s.pct_change is not None]
    valid_sw.sort(key=lambda s: s.pct_change or 0, reverse=True)
    valid_main = [s for s in ctx.main_lines if s.pct_change is not None]
    valid_main.sort(key=lambda s: s.pct_change or 0, reverse=True)
    items = valid_sw[:6] + valid_sw[-3:] + valid_main[:6]   # 强 + 弱 + 主线候选
    bulk = []
    for i, s in enumerate(items):
        bulk.append({
            "candidate_index": i, "name": s.name, "code": s.code,
            "pct_change": s.pct_change, "rank": s.rank,
        })
    user = f"""
=== 板块清单 (申万一级 + 主线候选 THS 概念) ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.ROTATION_INSTRUCTIONS}

=== 输出 schema ===
{prompts.ROTATION_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key=key, prompt_name=key,
                                  parsed=parsed, resp=resp, status=status)
    by_idx: dict[int, dict] = {}
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                by_idx[idx] = entry
    rows = []
    for i, s in enumerate(items):
        info = by_idx.get(i, {})
        is_main_line = s in valid_main[:6]
        rows.append({
            "category": s.name + ("（主线候选）" if is_main_line else f"（申万 #{s.rank}）"),
            "strength_label": info.get("strength_label") or "—",
            "avg_pct_display": _fmt_pct(s.pct_change),
            "avg_dir": _direction(s.pct_change),
            "up_share_display": "",
            "leader": info.get("rotation_role") or "",
            "leader_pct": "",
            "laggard": "", "laggard_pct": "",
            "commentary": info.get("commentary") or "—",
            "a_share_focus": "",
        })
    return {
        "key": key, "title": title, "order": order, "type": "category_strength",
        "content_json": {"rows": rows},
        "prompt_name": key, "model_output_id": moid,
    }


def build_sentiment_section(ctx: MarketCtx, *, order: int, title: str, key: str) -> dict:
    b = ctx.breadth
    cells = []
    if b.limit_up_count is not None:
        cells.append({"label": "涨停家数", "value": f"{b.limit_up_count}", "unit": "家",
                       "note": "做多情绪"})
    if b.limit_down_count is not None:
        cells.append({"label": "跌停家数", "value": f"{b.limit_down_count}", "unit": "家",
                       "note": "风险释放"})
    if b.broke_limit_pct is not None:
        cells.append({"label": "炸板率", "value": f"{b.broke_limit_pct*100:.0f}", "unit": "%",
                       "note": "分歧风险"})
    if b.max_consec_streak is not None:
        cells.append({"label": "连板高度", "value": f"{b.max_consec_streak}", "unit": "连板",
                       "note": "接力强度"})
    if b.up_count is not None and (b.up_count + (b.down_count or 0)) > 0:
        share = b.up_count / (b.up_count + (b.down_count or 0)) * 100
        cells.append({"label": "上涨占比", "value": f"{share:.0f}", "unit": "%",
                       "note": "市场广度"})
    if b.avg_pct_change is not None:
        cells.append({"label": "全 A 平均涨跌", "value": f"{b.avg_pct_change:+.2f}", "unit": "%",
                       "delta_dir": _direction(b.avg_pct_change),
                       "note": "整体赚钱效应"})

    user = f"""
=== 短线情绪指标 ===
{json.dumps([{"label": c["label"], "value": c["value"], "unit": c.get("unit", "")} for c in cells], ensure_ascii=False)}

=== 任务 ===
{prompts.SENTIMENT_INSTRUCTIONS}

=== 输出 schema ===
{prompts.SENTIMENT_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1400,
    )
    moid = _persist_model_output(ctx, section_key=key, prompt_name=key,
                                  parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"cycle_phase": "—", "ladder_health": "—",
                                                         "commentary": "", "risk_note": ""}
    content["cells"] = cells
    return {
        "key": key, "title": title, "order": order, "type": "sentiment_grid",
        "content_json": content, "prompt_name": key, "model_output_id": moid,
    }


def build_dragon_tiger_section(ctx: MarketCtx, *, order: int, title: str, key: str) -> dict:
    if not ctx.dragon_tiger:
        return {"key": key, "title": title, "order": order, "type": "dragon_tiger",
                "content_json": {"rows": [], "fallback_text": "今日无龙虎榜数据。"}}
    bulk = []
    for i, st in enumerate(ctx.dragon_tiger[:12]):
        bulk.append({
            "candidate_index": i,
            "ts_code": st.ts_code, "name": st.name,
            "pct_change": st.pct_change, "amount": st.amount,
            "net_amount": st.moneyflow_net,
            "reason": st.extra.get("reason"),
            "turnover_rate": st.extra.get("turnover_rate"),
        })
    user = f"""
=== 龙虎榜 ({len(bulk)} 只) ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.DRAGON_TIGER_INSTRUCTIONS}

=== 输出 schema ===
{prompts.DRAGON_TIGER_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key=key, prompt_name=key,
                                  parsed=parsed, resp=resp, status=status)
    by_idx: dict[int, dict] = {}
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                by_idx[idx] = entry
    rows = []
    for i, st in enumerate(ctx.dragon_tiger[:12]):
        info = by_idx.get(i, {})
        rows.append({
            "ts_code": st.ts_code, "name": st.name or "—",
            "pct_display": _fmt_pct(st.pct_change),
            "pct_dir": _direction(st.pct_change),
            "net_display": _fmt_count(st.moneyflow_net) + " 元" if st.moneyflow_net is not None else "—",
            "actor_type": info.get("actor_type") or "待确认",
            "intent": info.get("intent") or "—",
            "reason": st.extra.get("reason") or "—",
            "commentary": info.get("commentary") or "",
        })
    return {
        "key": key, "title": title, "order": order, "type": "dragon_tiger",
        "content_json": {"rows": rows},
        "prompt_name": key, "model_output_id": moid,
    }


def build_news_section(ctx: MarketCtx, *, order: int, title: str, key: str) -> dict:
    from ifa.families._shared.news import post_process_news_events
    if ctx.news_df is None or (hasattr(ctx.news_df, "empty") and ctx.news_df.empty):
        return {"key": key, "title": title, "order": order, "type": "news_list",
                "content_json": {"events": [],
                                  "fallback_text": "近 24 小时未捕获显著的市场新闻。"}}
    candidates = []
    for _, row in ctx.news_df.head(20).iterrows():
        dt_v = row.get("datetime")
        if hasattr(dt_v, "tz_localize") and getattr(dt_v, "tzinfo", None) is None:
            try:
                dt_v = dt_v.tz_localize(BJT)
            except Exception:
                pass
        elif hasattr(dt_v, "replace") and getattr(dt_v, "tzinfo", None) is None:
            dt_v = dt_v.replace(tzinfo=BJT)
        candidates.append({
            "title": row.get("title"),
            "source_name": row.get("src_label") or row.get("src"),
            "publish_time": dt_v.isoformat() if hasattr(dt_v, "isoformat") else str(dt_v),
            "content_snippet": (str(row.get("content") or "") if row.get("content") == row.get("content") else "")[:600],
        })
    user = f"""
=== 候选新闻 ({len(candidates)}) ===
{json.dumps(candidates, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.NEWS_INSTRUCTIONS}

=== 输出 schema ===
{prompts.NEWS_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key=key, prompt_name=key,
                                  parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"events": [], "fallback_text": ""}
    content["events"] = post_process_news_events(content.get("events") or [], candidates)
    return {
        "key": key, "title": title, "order": order, "type": "news_list",
        "content_json": content, "prompt_name": key, "model_output_id": moid,
    }


def build_focus_deep_section(ctx: MarketCtx, *, order: int, title: str, key: str) -> dict:
    items = ctx.important_focus[:10]
    if not items:
        return {"key": key, "title": title, "order": order, "type": "focus_deep",
                "content_json": {"rows": [], "fallback_text": "重点关注池为空。"}}
    bulk = []
    for i, spec in enumerate(items):
        d = ctx.important_focus_data.get(spec.ts_code, {})
        bulk.append({
            "candidate_index": i, "stock_code": spec.ts_code, "stock_name": spec.display_name,
            "layer": spec.layer, "sub_theme": spec.sub_theme,
            "close": d.get("close"), "pct_change": d.get("pct_change"),
            "moneyflow_net": d.get("moneyflow_net"),
            "history_close": (d.get("history_close") or [])[-5:],
        })
    user = f"""
=== 用户重点关注 ({len(bulk)} 只，全市场 — 不限于 Tech) ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.FOCUS_DEEP_INSTRUCTIONS}

=== 输出 schema ===
{prompts.FOCUS_DEEP_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=3200,
    )
    moid = _persist_model_output(ctx, section_key=key, prompt_name=key,
                                  parsed=parsed, resp=resp, status=status)
    by_idx: dict[int, dict] = {}
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                by_idx[idx] = entry
    rows = []
    for i, spec in enumerate(items):
        info = by_idx.get(i, {})
        d = ctx.important_focus_data.get(spec.ts_code, {})
        spark = sparkline_svg(d.get("history_close", []), width=180, height=32) if d.get("history_close") else ""
        rows.append({
            "stock_code": spec.ts_code, "stock_name": spec.display_name,
            "layer_id": spec.layer, "sub_theme": spec.sub_theme,
            # None (not "—") when missing — template hides empty fields
            "close_display": f"{d['close']:,.2f}" if d.get("close") is not None else None,
            "pct_display": _fmt_pct(d.get("pct_change")) if d.get("pct_change") is not None else None,
            "pct_dir": _direction(d.get("pct_change")),
            "mf_display": (_fmt_count(d.get("moneyflow_net")) + " 元") if d.get("moneyflow_net") is not None else None,
            "spark_svg": spark,
            "spark_caption": d.get("history_caption") or "",
            "status": info.get("status") or None,
            "today_observation": info.get("today_observation") or None,
            "scenario_plans": info.get("scenario_plans") or [],
            "risk_note": info.get("risk_note") or "",
        })
    return {
        "key": key, "title": f"{title} · @{ctx.user}",
        "order": order, "type": "focus_deep",
        "content_json": {"rows": rows},
        "prompt_name": key, "model_output_id": moid,
    }


def build_focus_brief_section(ctx: MarketCtx, *, order: int, title: str, key: str) -> dict:
    items = ctx.regular_focus[:20]
    if not items:
        return {"key": key, "title": title, "order": order, "type": "focus_brief",
                "content_json": {"rows": [], "fallback_text": "普通关注池为空。"}}
    bulk = []
    for i, spec in enumerate(items):
        d = ctx.regular_focus_data.get(spec.ts_code, {})
        bulk.append({
            "candidate_index": i, "stock_code": spec.ts_code, "stock_name": spec.display_name,
            "layer": spec.layer, "sub_theme": spec.sub_theme,
            "pct_change": d.get("pct_change"),
            "moneyflow_net": d.get("moneyflow_net"),
        })
    user = f"""
=== 用户普通关注 ({len(bulk)} 只) ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.FOCUS_BRIEF_INSTRUCTIONS}

=== 输出 schema ===
{prompts.FOCUS_BRIEF_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key=key, prompt_name=key,
                                  parsed=parsed, resp=resp, status=status)
    by_idx: dict[int, dict] = {}
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                by_idx[idx] = entry
    rows = []
    for i, spec in enumerate(items):
        info = by_idx.get(i, {})
        d = ctx.regular_focus_data.get(spec.ts_code, {})
        spark = sparkline_svg(d.get("history_close", []), width=130, height=28) if d.get("history_close") else ""
        rows.append({
            "stock_code": spec.ts_code, "stock_name": spec.display_name,
            "layer_id": spec.layer, "sub_theme": spec.sub_theme,
            "close_display": f"{d['close']:,.2f}" if d.get("close") is not None else None,
            "pct_display": _fmt_pct(d.get("pct_change")) if d.get("pct_change") is not None else None,
            "pct_dir": _direction(d.get("pct_change")),
            "spark_svg": spark,
            "spark_caption": d.get("history_caption") or "",
            "state": info.get("state") or None,
            "today_hint": info.get("today_hint") or None,
        })
    return {
        "key": key, "title": f"{title} · @{ctx.user}",
        "order": order, "type": "focus_brief",
        "content_json": {"rows": rows},
        "prompt_name": key, "model_output_id": moid,
    }
