"""Asset morning report — orchestrator + 10 section builders.

Sections per asset.txt §七:
  S0  [implicit in template]    Header / fixed disclaimer
  S1  tone_card                 今日 Asset 总体结论
  S2  commodity_dashboard       商品与期货核心看板（按大类分组）
  S3  category_strength         大类商品强弱排序
  S4  risk_list                 异常波动与关键品种提醒
  S5  mapping_table             商品价格对 A 股板块映射
  S6  chain_transmission        产业链成本与利润传导
  S7  news_list                 Asset 相关新闻与事件摘要
  S8  hypotheses_list           今日需要验证的 Asset 假设
  S9  disclaimer                完整免责声明
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.core.llm import LLMClient
from ifa.core.render import HtmlRenderer, sparkline_svg
from ifa.core.report import (
    DISCLAIMER_PARAGRAPHS_EN,
    DISCLAIMER_PARAGRAPHS_ZH,
    fmt_bjt,
    to_bjt,
    utc_now,
)
from ifa.core.report.run import (
    ReportRun,
    finalize_report_run,
    insert_judgment,
    insert_report_run,
    insert_section,
)
from ifa.core.tushare import TuShareClient
from ifa.families.macro.morning import _persist_model_output, _safe_chat_json

from . import data, prompts
from .data import (
    AnomalyFlag,
    CategoryStrength,
    CommoditySnapshot,
)
from .universe import CHINA_ASSET_UNIVERSE, INDUSTRY_CHAINS

TEMPLATE_VERSION = "asset_morning_v0.1"
REPORT_FAMILY = "asset"
REPORT_TYPE = "morning_long"
SLOT = "morning"
MARKET = "cross_asset"


@dataclass
class AssetCtx:
    engine: Engine
    llm: LLMClient
    tushare: TuShareClient
    run: ReportRun
    snapshots: dict[str, CommoditySnapshot]
    strengths: list[CategoryStrength]
    anomalies: list[AnomalyFlag]
    news_df: object  # pandas DataFrame
    used_trade_date: dt.date | None
    on_log: Callable[[str], None]


# ─── helpers ────────────────────────────────────────────────────────────────

def _fmt_pct(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:+.{decimals}f}%"


def _direction(v: float | None, threshold: float = 0.05) -> str:
    if v is None:
        return "flat"
    if v > threshold:
        return "up"
    if v < -threshold:
        return "down"
    return "flat"


def _fmt_count(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1e8:
        return f"{v/1e8:.2f}亿"
    if v >= 1e4:
        return f"{v/1e4:.1f}万"
    return f"{v:,.0f}"


# ─── S1: tone card ──────────────────────────────────────────────────────────

def _build_s1_tone(ctx: AssetCtx) -> dict:
    by_cat: dict[str, list[CommoditySnapshot]] = {}
    for snap in ctx.snapshots.values():
        by_cat.setdefault(snap.spec.category, []).append(snap)

    cat_summary = []
    for cat, items in by_cat.items():
        with_data = [i for i in items if i.pct_change is not None]
        if with_data:
            avg = sum(i.pct_change for i in with_data) / len(with_data)
            cat_summary.append(f"  {cat}({len(with_data)}/{len(items)} 有数据): 均 {avg:+.2f}%；" +
                               ", ".join(f"{i.spec.display_name} {i.pct_change:+.2f}%" for i in with_data[:5]))
        else:
            cat_summary.append(f"  {cat}: 全部数据缺失或不可用")

    user = f"""
=== 报告时点 ===
报告日期 (北京时间): {ctx.run.report_date}
数据截止: {fmt_bjt(ctx.run.data_cutoff_at)} 北京时间
最近可用期货交易日: {ctx.used_trade_date or '—'}

=== 商品大类快照 ===
{chr(10).join(cat_summary)}

=== 今日异常波动品种数 ===
{len(ctx.anomalies)}

=== 任务 ===
{prompts.TONE_INSTRUCTIONS}

=== 输出 schema ===
{prompts.TONE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="asset_morning.s1_tone",
                                 prompt_name="asset_morning.s1_tone",
                                 parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {"tone": "信号不强", "tone_short": "信号弱",
                  "headline": "商品端今日整体信号不强；以多日观察为主。",
                  "summary": "本报告窗口内商品涨跌幅有限或数据不足以形成强结论。",
                  "bullets": []}
        ctx.run.fallback_used = True

    insert_judgment(
        ctx.engine, report_run_id=ctx.run.report_run_id,
        section_key="asset_morning.s1_tone",
        judgment_type="asset_tone",
        judgment_text=parsed.get("headline", ""),
        target="A股周期/资源/制造链",
        horizon="today_full_day",
        confidence="medium",
        validation_method="evening review of A-share sector reaction vs. commodity moves",
    )

    return {
        "key": "asset_morning.s1_tone",
        "title": "今日 Asset 总体结论",
        "order": 1,
        "type": "tone_card",
        "content_json": parsed,
        "prompt_name": "asset_morning.s1_tone",
        "model_output_id": moid,
    }


# ─── S2: commodity dashboard ───────────────────────────────────────────────

def _build_s2_dashboard(ctx: AssetCtx) -> dict:
    # group snapshots by category, build candidates for LLM commentary
    by_cat: dict[str, list[CommoditySnapshot]] = {}
    candidates: list[dict] = []
    for snap in ctx.snapshots.values():
        if snap.data_status != "ok":
            continue
        by_cat.setdefault(snap.spec.category, []).append(snap)

    # Build LLM input only for OK rows, batched
    flat: list[CommoditySnapshot] = []
    for cat in ["能源", "贵金属", "有色", "黑色", "化工", "农产品"]:
        for snap in by_cat.get(cat, []):
            flat.append(snap)
            history = ", ".join(f"{d}={v}" for d, v in zip(snap.history_dates[-5:], snap.history_close[-5:]) if v is not None)
            candidates.append({
                "name": snap.spec.display_name,
                "summary": (f"{snap.spec.category} | {snap.actual_contract} | 收盘 {snap.close} "
                            f"涨跌 {snap.pct_change:+.2f}% | 成交 {snap.volume} 持仓 {snap.open_interest} | "
                            f"近5日收盘: {history}"),
            })

    bulk_text = "\n".join(f"[{i}] {c['name']} — {c['summary']}" for i, c in enumerate(candidates))
    user = f"""
=== 任务 ===
{prompts.PANEL_INSTRUCTIONS}

输入品种数（按 candidate_index 0..{len(candidates)-1}）：
{bulk_text}

=== 输出 schema ===
{prompts.PANEL_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=3500,
    )
    moid = _persist_model_output(ctx, section_key="asset_morning.s2_dashboard",
                                 prompt_name="asset_morning.s2_dashboard",
                                 parsed=parsed, resp=resp, status=status)
    commentary_by_idx: dict[int, str] = {}
    overall = ""
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                commentary_by_idx[idx] = entry.get("commentary") or ""
        overall = parsed.get("overall_commentary") or ""

    # Build category groups for the renderer — only include rows with usable data;
    # CZCE-unavailable / no-data items are silently dropped per ops feedback.
    categories_payload = []
    flat_idx = 0
    for cat in ["能源", "贵金属", "有色", "黑色", "化工", "农产品"]:
        items = [s for s in by_cat.get(cat, []) if s.data_status == "ok"]
        if not items:
            continue
        rows = []
        for snap in items:
            spark = sparkline_svg(snap.history_close, width=130, height=28)
            rows.append({
                "logical_symbol": snap.spec.logical_symbol,
                "display_name": snap.spec.display_name,
                "actual_contract": snap.actual_contract,
                "close_display": f"{snap.close:,.2f}" if snap.close is not None else "—",
                "pct_display": _fmt_pct(snap.pct_change),
                "pct_dir": _direction(snap.pct_change),
                "vol_display": _fmt_count(snap.volume),
                "oi_display": _fmt_count(snap.open_interest),
                "spark_svg": spark,
                "commentary": commentary_by_idx.get(flat_idx, "—"),
            })
            flat_idx += 1
        with_data = [s for s in items if s.pct_change is not None]
        avg_label = _fmt_pct(sum(i.pct_change for i in with_data) / len(with_data)) if with_data else "—"
        categories_payload.append({
            "name": cat,
            "n_components": len(items),
            "n_with_data": len(with_data),
            "avg_label": avg_label,
            "rows": rows,
        })

    return {
        "key": "asset_morning.s2_dashboard",
        "title": "商品与期货核心看板",
        "order": 2,
        "type": "commodity_dashboard",
        "content_json": {
            "intro": (
                "主力合约由当日成交量第一位推断；近 5 日趋势为 sparkline。"
                f"最近可用期货交易日：{ctx.used_trade_date or '—'}。"
                " CZCE 接口在当前账号 fut_daily 不可用，相关品种以「数据未启用」标注。"
            ),
            "categories": categories_payload,
            "commentary": overall,
        },
        "prompt_name": "asset_morning.s2_dashboard",
        "model_output_id": moid,
    }


# ─── S3: category strength ────────────────────────────────────────────────

def _build_s3_strength(ctx: AssetCtx) -> dict:
    # Send strengths to LLM for commentary
    bulk = []
    for s in ctx.strengths:
        bulk.append({
            "category": s.category,
            "n_components": s.n_components,
            "n_with_data": s.n_with_data,
            "avg_pct_change": s.avg_pct_change,
            "up_share": s.up_share,
            "leader": s.leader,
            "leader_pct": s.leader_pct,
            "laggard": s.laggard,
            "laggard_pct": s.laggard_pct,
        })
    user = f"""
=== 大类强弱量化 ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.STRENGTH_INSTRUCTIONS}

=== 输出 schema ===
{prompts.STRENGTH_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="asset_morning.s3_strength",
                                 prompt_name="asset_morning.s3_strength",
                                 parsed=parsed, resp=resp, status=status)
    by_idx: dict[int, dict] = {}
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                by_idx[idx] = entry

    rows = []
    for i, s in enumerate(ctx.strengths):
        commentary_obj = by_idx.get(i, {})
        rows.append({
            "category": s.category,
            "strength_label": commentary_obj.get("strength_label") or "—",
            "avg_pct_display": _fmt_pct(s.avg_pct_change),
            "avg_dir": _direction(s.avg_pct_change),
            "up_share_display": f"{s.up_share*100:.0f}%" if s.up_share is not None else "—",
            "leader": s.leader,
            "leader_pct": _fmt_pct(s.leader_pct),
            "laggard": s.laggard,
            "laggard_pct": _fmt_pct(s.laggard_pct),
            "commentary": commentary_obj.get("commentary") or "—",
            "a_share_focus": commentary_obj.get("a_share_focus") or "",
        })
    return {
        "key": "asset_morning.s3_strength",
        "title": "大类商品强弱排序",
        "order": 3,
        "type": "category_strength",
        "content_json": {"rows": rows},
        "prompt_name": "asset_morning.s3_strength",
        "model_output_id": moid,
    }


# ─── S4: anomaly / risk list ──────────────────────────────────────────────

def _build_s4_anomalies(ctx: AssetCtx) -> dict:
    if not ctx.anomalies:
        return {
            "key": "asset_morning.s4_anomalies",
            "title": "异常波动与关键品种提醒",
            "order": 4,
            "type": "risk_list",
            "content_json": {
                "risk_level": "low",
                "risks": [],
                "summary": "今日商品端未捕获显著的异常波动；以稳态信号为主。",
            },
        }
    flag_summary = []
    for a in ctx.anomalies[:15]:
        flag_summary.append({
            "name": a.spec.display_name,
            "category": a.spec.category,
            "flag_type": a.flag_type,
            "detail": a.detail,
            "actual_contract": a.snapshot.actual_contract,
            "pct_change": a.snapshot.pct_change,
        })
    news_titles = []
    if hasattr(ctx.news_df, "empty") and not ctx.news_df.empty:
        for _, row in ctx.news_df.head(15).iterrows():
            news_titles.append(f"  · [{row.get('src_label', '')}] {row.get('title', '')}")
    user = f"""
=== 异常品种 ===
{json.dumps(flag_summary, ensure_ascii=False, indent=2)}

=== 商品相关新闻（最多 15 条） ===
{chr(10).join(news_titles) or '  (无)'}

=== 任务 ===
{prompts.ANOMALY_INSTRUCTIONS}

=== 输出 schema ===
{prompts.ANOMALY_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="asset_morning.s4_anomalies",
                                 prompt_name="asset_morning.s4_anomalies",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"risk_level": "low", "risks": [], "summary": ""}
    for r in content.get("risks", []) or []:
        c = (r.get("confidence") or "low").lower()
        r["confidence_class"] = {"high": "high", "medium": "med", "low": "low"}.get(c, "med")
    return {
        "key": "asset_morning.s4_anomalies",
        "title": "异常波动与关键品种提醒",
        "order": 4,
        "type": "risk_list",
        "content_json": content,
        "prompt_name": "asset_morning.s4_anomalies",
        "model_output_id": moid,
    }


# ─── S5: commodity → A-share mapping ──────────────────────────────────────

def _build_s5_mapping(ctx: AssetCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5500]}

=== 任务 ===
{prompts.MAPPING_INSTRUCTIONS}

=== 输出 schema ===
{prompts.MAPPING_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="asset_morning.s5_mapping",
                                 prompt_name="asset_morning.s5_mapping",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"rows": []}
    return {
        "key": "asset_morning.s5_mapping",
        "title": "商品价格对 A 股板块映射",
        "order": 5,
        "type": "mapping_table",
        "content_json": content,
        "prompt_name": "asset_morning.s5_mapping",
        "model_output_id": moid,
    }


# ─── S6: industry chain transmission ──────────────────────────────────────

def _build_s6_chain(ctx: AssetCtx) -> dict:
    chains_input = []
    for ch in INDUSTRY_CHAINS:
        upstream_data = []
        for sym in ch.upstream_symbols:
            snap = ctx.snapshots.get(sym)
            if snap and snap.data_status == "ok":
                upstream_data.append(f"{snap.spec.display_name}({sym}) {snap.pct_change:+.2f}%")
            elif snap:
                upstream_data.append(f"{snap.spec.display_name}({sym}) 数据未启用")
        mid_data = []
        for sym in ch.midstream_symbols:
            snap = ctx.snapshots.get(sym)
            if snap and snap.data_status == "ok":
                mid_data.append(f"{snap.spec.display_name} {snap.pct_change:+.2f}%")
            elif snap:
                mid_data.append(f"{snap.spec.display_name} 数据未启用")
        chains_input.append({
            "name": ch.name,
            "upstream_data": upstream_data,
            "midstream_data": mid_data,
            "downstream_a_share": ch.downstream_a_share,
            "narrative": ch.narrative,
        })
    user = f"""
=== 6 条产业链定义 + 今日数据 ===
{json.dumps(chains_input, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.CHAIN_INSTRUCTIONS}

=== 输出 schema ===
{prompts.CHAIN_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="asset_morning.s6_chain",
                                 prompt_name="asset_morning.s6_chain",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"chains": []}
    return {
        "key": "asset_morning.s6_chain",
        "title": "产业链成本与利润传导",
        "order": 6,
        "type": "chain_transmission",
        "content_json": content,
        "prompt_name": "asset_morning.s6_chain",
        "model_output_id": moid,
    }


# ─── S7: news list ─────────────────────────────────────────────────────────

def _build_s7_news(ctx: AssetCtx) -> dict:
    if ctx.news_df is None or (hasattr(ctx.news_df, "empty") and ctx.news_df.empty):
        return {
            "key": "asset_morning.s7_news",
            "title": "Asset 相关新闻与事件摘要",
            "order": 7,
            "type": "news_list",
            "content_json": {"events": [], "fallback_text": "近 36 小时未捕获显著的商品/能源/金属/农产品相关新闻。"},
        }
    from ifa.core.report.timezones import BJT
    candidates = []
    for _, row in ctx.news_df.head(20).iterrows():
        dt_v = row.get("datetime")
        # TuShare returns naive Beijing wall-clock; tag with BJT so the LLM
        # receives an unambiguous tz-aware timestamp.
        if hasattr(dt_v, "tz_localize") and dt_v.tzinfo is None:
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
=== 候选新闻（{len(candidates)} 条，按时间倒序） ===
{json.dumps(candidates, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.NEWS_INSTRUCTIONS}

=== 输出 schema ===
{prompts.NEWS_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="asset_morning.s7_news",
                                 prompt_name="asset_morning.s7_news",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"events": [], "fallback_text": ""}
    from ifa.families._shared.news import post_process_news_events
    content["events"] = post_process_news_events(content.get("events") or [], candidates)
    return {
        "key": "asset_morning.s7_news",
        "title": "Asset 相关新闻与事件摘要",
        "order": 7,
        "type": "news_list",
        "content_json": content,
        "prompt_name": "asset_morning.s7_news",
        "model_output_id": moid,
    }


# ─── S8: hypotheses ────────────────────────────────────────────────────────

def _build_s8_hypotheses(ctx: AssetCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5500]}

=== 任务 ===
{prompts.HYPOTHESES_INSTRUCTIONS}

=== 输出 schema ===
{prompts.HYPOTHESES_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="asset_morning.s8_hypotheses",
                                 prompt_name="asset_morning.s8_hypotheses",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"hypotheses": []}
    for h in content.get("hypotheses", []) or []:
        try:
            insert_judgment(
                ctx.engine, report_run_id=ctx.run.report_run_id,
                section_key="asset_morning.s8_hypotheses",
                judgment_type="hypothesis",
                judgment_text=h.get("hypothesis", ""),
                target=", ".join(h.get("related_markets_or_sectors") or [])[:300],
                horizon=h.get("observation_window") or "today_full_day",
                confidence=(h.get("confidence") or "medium").lower(),
                validation_method=h.get("review_rule"),
            )
        except Exception:
            pass
    return {
        "key": "asset_morning.s8_hypotheses",
        "title": "今日需要验证的 Asset 假设",
        "order": 8,
        "type": "hypotheses_list",
        "content_json": content,
        "prompt_name": "asset_morning.s8_hypotheses",
        "model_output_id": moid,
    }


# ─── S9: disclaimer ───────────────────────────────────────────────────────

def _build_s9_disclaimer() -> dict:
    return {
        "key": "asset_morning.s9_disclaimer",
        "title": "免责声明",
        "order": 9,
        "type": "disclaimer",
        "content_json": {
            "paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
            "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH,
        },
    }


# ─── Orchestrator ─────────────────────────────────────────────────────────

def run_asset_morning(
    *,
    report_date: dt.date,
    data_cutoff_at: dt.datetime,
    triggered_by: str | None = None,
    on_log: Callable[[str], None] = lambda m: None,
) -> Path:
    settings = get_settings()
    engine = get_engine(settings)
    llm = LLMClient(settings)
    tushare = TuShareClient(settings)

    run = ReportRun(
        report_run_id=uuid.uuid4(),
        market=MARKET, report_family=REPORT_FAMILY, report_type=REPORT_TYPE,
        report_date=report_date, slot=SLOT, timezone_name="Asia/Shanghai",
        data_cutoff_at=data_cutoff_at,
        run_mode=settings.run_mode,
        template_version=TEMPLATE_VERSION,
        prompt_version=prompts.PROMPT_BUNDLE_VERSION,
        triggered_by=triggered_by or settings.run_mode.value,
    )
    insert_report_run(engine, run)
    on_log(f"[run {str(run.report_run_id)[:8]}] starting Asset morning report for {report_date}")

    try:
        on_log("resolving main contracts via fut_daily(trade_date=…) volume rank…")
        snapshots, used_date = data.resolve_main_contracts(tushare, on_date=report_date - dt.timedelta(days=1))
        n_ok = sum(1 for s in snapshots.values() if s.data_status == "ok")
        on_log(f"  main contracts resolved: {n_ok}/{len(snapshots)} on {used_date}")
        on_log("attaching 10-day price histories per contract…")
        data.attach_histories(tushare, snapshots, end_date=report_date - dt.timedelta(days=1), days=10)
        strengths = data.category_strengths(snapshots)
        anomalies = data.detect_anomalies(snapshots)
        on_log(f"  category strengths computed for {len(strengths)} categories; {len(anomalies)} anomaly flags")
        on_log("filtering commodity-related news (last 36h)…")
        bjt_cutoff = to_bjt(data_cutoff_at)
        news_df = data.fetch_commodity_news(tushare, end_bjt=bjt_cutoff, lookback_hours=36, max_keep=30)
        on_log(f"  {len(news_df)} commodity news after filter")

        ctx = AssetCtx(
            engine=engine, llm=llm, tushare=tushare, run=run,
            snapshots=snapshots, strengths=strengths, anomalies=anomalies,
            news_df=news_df, used_trade_date=used_date, on_log=on_log,
        )

        sections: list[dict] = []
        for label, builder in [
            ("S1 tone",       lambda: _build_s1_tone(ctx)),
            ("S2 dashboard",  lambda: _build_s2_dashboard(ctx)),
            ("S3 strength",   lambda: _build_s3_strength(ctx)),
            ("S4 anomalies",  lambda: _build_s4_anomalies(ctx)),
            ("S5 mapping",    lambda: _build_s5_mapping(ctx, sections)),
            ("S6 chain",      lambda: _build_s6_chain(ctx)),
            ("S7 news",       lambda: _build_s7_news(ctx)),
            ("S8 hypotheses", lambda: _build_s8_hypotheses(ctx, sections)),
            ("S9 disclaimer", _build_s9_disclaimer),
        ]:
            t0 = time.monotonic()
            on_log(f"building {label}…")
            sec = builder()
            sections.append(sec)
            insert_section(
                engine, report_run_id=run.report_run_id,
                section_key=sec["key"], section_title=sec["title"], section_order=sec["order"],
                content_json=sec["content_json"],
                prompt_name=sec.get("prompt_name"),
                prompt_version=prompts.PROMPT_BUNDLE_VERSION,
                model_output_id=sec.get("model_output_id"),
                fallback_used=sec.get("fallback_used", False),
            )
            on_log(f"  {label} done in {time.monotonic()-t0:.1f}s")

        out_path = _render_and_save(run, sections, settings)
        finalize_report_run(engine, run, status="succeeded", output_html_path=out_path)
        on_log(f"saved → {out_path}")
        return out_path

    except Exception as exc:
        finalize_report_run(engine, run, status="failed",
                            error_summary=f"{type(exc).__name__}: {exc}")
        raise


def _render_and_save(run: ReportRun, sections: list[dict], settings) -> Path:
    renderer = HtmlRenderer()
    cutoff_bjt_str = fmt_bjt(run.data_cutoff_at)
    generated_bjt_str = fmt_bjt(utc_now(), "%Y-%m-%d %H:%M")
    report = {
        "title": f"中国 Asset 早盘报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": "China Asset Pre-Open Briefing — Lindenwood Management LLC",
        "report_date_bjt": run.report_date.strftime("%Y-%m-%d"),
        "data_cutoff_bjt": cutoff_bjt_str,
        "generated_at_bjt": generated_bjt_str,
        "template_version": TEMPLATE_VERSION,
        "run_mode": run.run_mode.value,
        "report_run_id_short": str(run.report_run_id)[:8],
        "sections": sections,
    }
    html = renderer.render(report=report)
    out_root = settings.output_root / run.run_mode.value
    out_root.mkdir(parents=True, exist_ok=True)
    bjt_now = to_bjt(utc_now())
    fname = f"CN_asset_morning_{run.report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
