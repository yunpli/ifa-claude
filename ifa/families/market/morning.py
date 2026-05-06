"""A-share Main morning report — orchestrator.

Sections (matches mainreport.txt §4.2):
  S1  tone (market_state_card)         今日 A 股总判断
  S2  three_aux_summary                三辅报告摘要
  S3  index_panel                      昨日市场结构 (index family + breadth)
  S4  category_strength                板块轮动与主线候选 (申万 + THS 主线)
  S5  sentiment_grid                   市场情绪与短线生态
  S6  dragon_tiger                     龙虎榜与机构 / 游资
  S7  news_list                        新闻 / 公告 / 政策
  S8  category_strength (main_line)    今日重点关注方向
  S9  focus_deep                       重点关注股票 (10)
  S10 focus_brief                      普通关注股票 (20)
  S11 risk_list                        今日风险清单
  S12 hypotheses_list                  今日待验证主报告假设
  S13 disclaimer
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.core.llm import LLMClient
from ifa.core.render import HtmlRenderer
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
from ifa.families.macro.morning import _safe_chat_json

from . import prompts
from ._common import (
    MarketCtx,
    _persist_model_output,
    build_dragon_tiger_section,
    build_focus_brief_section,
    build_focus_deep_section,
    build_index_panel_section,
    build_news_section,
    build_rotation_section,
    build_sentiment_section,
    build_three_aux_section,
    enrich_market_focus,
    prefetch_market_data,
)

TEMPLATE_VERSION = "market_morning_v2.1.0"
REPORT_FAMILY = "main"
REPORT_TYPE = "morning_long"
SLOT = "morning"
MARKET = "china_a"


# ─── S1 tone ──────────────────────────────────────────────────────────────

def _build_s1_tone(ctx: MarketCtx) -> dict:
    aux_blob = {
        f: {"headline": ctx.aux_summaries.get(f).headline if ctx.aux_summaries.get(f) else None,
            "tone_or_state": ctx.aux_summaries.get(f).tone_or_state if ctx.aux_summaries.get(f) else None}
        for f in ("macro", "asset", "tech")
    }
    indices_blob = "; ".join(
        f"{s.name} {s.pct_change:+.2f}%" for s in ctx.indices if s.pct_change is not None
    )
    breadth = ctx.breadth
    breadth_blob = (
        f"全 A 成交 {breadth.total_amount} 万亿 (前日 {breadth.total_amount_prev}); "
        f"涨/跌/平={breadth.up_count}/{breadth.down_count}/{breadth.flat_count}; "
        f"涨停 {breadth.limit_up_count}/跌停 {breadth.limit_down_count}; "
        f"炸板率 {f'{breadth.broke_limit_pct*100:.0f}%' if breadth.broke_limit_pct is not None else 'N/A'}; "
        f"连板高度 {breadth.max_consec_streak}"
    )
    flows = ctx.flows
    flows_blob = (
        f"上一交易日北向 {flows.north_money:+.1f} 亿; "
        f"两融 {flows.margin_total} 万亿 (Δ {flows.margin_change}); "
    )
    main_line_top = ", ".join(
        f"{s.name} {s.pct_change:+.2f}%" for s in ctx.main_lines[:6] if s.pct_change is not None
    )
    user = f"""
=== 报告时点 ===
报告日期 (北京时间): {ctx.run.report_date}
数据截止: {fmt_bjt(ctx.run.data_cutoff_at)} 北京时间
slot: morning

=== 指数 ===
{indices_blob}

=== 全 A 广度 / 情绪 / 流动性 ===
{breadth_blob}
{flows_blob}

=== 主线候选板块 (THS 概念，按昨日涨幅) ===
{main_line_top}

=== 三辅报告 (摘要) ===
{json.dumps(aux_blob, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.MORNING_TONE_INSTRUCTIONS}

=== 输出 schema ===
{prompts.MORNING_TONE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="market_morning.s1_tone",
                                  prompt_name="market_morning.s1_tone",
                                  parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {"market_state": "震荡", "market_state_short": "震荡",
                  "main_line_state": "待确立", "risk_appetite": "中", "risk_level": "low",
                  "headline": "今日市场缺乏明确合力，等待主线确认。",
                  "summary": "数据不足以形成强结论。",
                  "validation_points": []}
        ctx.run.fallback_used = True
    insert_judgment(
        ctx.engine, report_run_id=ctx.run.report_run_id,
        section_key="market_morning.s1_tone", judgment_type="market_state",
        judgment_text=parsed.get("headline", ""),
        target=parsed.get("main_line_state"),
        horizon="today_full_day", confidence="medium",
        validation_method="evening review of A-share market state vs. headline",
    )
    # Re-shape tone payload to feed _tech_tone partial (we reuse it for unified visual)
    pay = {
        "tech_state": parsed.get("market_state"),
        "tech_state_short": parsed.get("market_state_short"),
        "strongest_layer": parsed.get("main_line_state"),
        "risk_level": parsed.get("risk_level"),
        "headline": parsed.get("headline"),
        "summary": parsed.get("summary"),
        "validation_points": parsed.get("validation_points") or [],
    }
    return {
        "key": "market_morning.s1_tone", "title": "今日 A 股总判断",
        "order": 1, "type": "tech_tone", "content_json": pay,
        "prompt_name": "market_morning.s1_tone", "model_output_id": moid,
    }


# ─── S11 risk list ────────────────────────────────────────────────────────

def _build_s11_risk(ctx: MarketCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5500]}

=== 任务 ===
{prompts.RISK_INSTRUCTIONS}

=== 输出 schema ===
{prompts.RISK_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="market_morning.s11_risk",
                                  prompt_name="market_morning.s11_risk",
                                  parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"risk_level": "low", "risks": []}
    for r in content.get("risks", []) or []:
        c = (r.get("confidence") or "low").lower()
        r["confidence_class"] = {"high": "high", "medium": "med", "low": "low"}.get(c, "med")
    return {
        "key": "market_morning.s11_risk", "title": "今日风险清单",
        "order": 11, "type": "risk_list", "content_json": content,
        "prompt_name": "market_morning.s11_risk", "model_output_id": moid,
    }


# ─── S12 hypotheses ──────────────────────────────────────────────────────

def _build_s12_hypotheses(ctx: MarketCtx, prior: list[dict]) -> dict:
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
    moid = _persist_model_output(ctx, section_key="market_morning.s12_hypotheses",
                                  prompt_name="market_morning.s12_hypotheses",
                                  parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"hypotheses": []}
    for h in content.get("hypotheses", []) or []:
        try:
            insert_judgment(
                ctx.engine, report_run_id=ctx.run.report_run_id,
                section_key="market_morning.s12_hypotheses", judgment_type="hypothesis",
                judgment_text=h.get("hypothesis", ""),
                target=", ".join(h.get("related_markets_or_sectors") or [])[:300],
                horizon=h.get("observation_window") or "today_full_day",
                confidence=(h.get("confidence") or "medium").lower(),
                validation_method=h.get("review_rule"),
            )
        except Exception:
            pass
    return {
        "key": "market_morning.s12_hypotheses", "title": "今日需要验证的主报告假设",
        "order": 12, "type": "hypotheses_list", "content_json": content,
        "prompt_name": "market_morning.s12_hypotheses", "model_output_id": moid,
    }


def _build_s13_disclaimer() -> dict:
    return {
        "key": "market_morning.s13_disclaimer", "title": "免责声明",
        "order": 13, "type": "disclaimer",
        "content_json": {"paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
                         "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH},
    }


# ─── Main-line dedicated section using directions_list (just sector_strength variant) ─

def _build_s8_main_line(ctx: MarketCtx) -> dict:
    valid = [s for s in ctx.main_lines if s.pct_change is not None]
    valid.sort(key=lambda s: s.pct_change or 0, reverse=True)
    items = valid[:6]
    bulk = []
    for i, s in enumerate(items):
        bulk.append({
            "candidate_index": i, "name": s.name, "code": s.code,
            "pct_change": s.pct_change,
        })
    aux_blob = {f: {"headline": ctx.aux_summaries.get(f).headline if ctx.aux_summaries.get(f) else None}
                for f in ("macro", "asset", "tech")}
    user = f"""
=== 主线候选 (THS 热门概念) ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 三辅报告 ===
{json.dumps(aux_blob, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.MAIN_LINE_INSTRUCTIONS}

=== 输出 schema ===
{prompts.MAIN_LINE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="market_morning.s8_main_line",
                                  prompt_name="market_morning.s8_main_line",
                                  parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"directions": []}
    # Adapt to directions_list partial shape
    payload = {"directions": []}
    for d in content.get("directions") or []:
        payload["directions"].append({
            "direction": d.get("direction"),
            "layer_id": "main",   # not a Tech layer; rendered as MAIN
            "trigger": " · ".join(d.get("trigger_factors") or []) or d.get("logic", ""),
            "watch_point_today": d.get("validation_today") or d.get("logic"),
            "rotation_phase": d.get("signal_strength") or "—",
            "signal_strength": d.get("signal_strength"),
        })
    return {
        "key": "market_morning.s8_main_line",
        "title": "今日重点关注板块与交易假设", "order": 8, "type": "directions_list",
        "content_json": payload,
        "prompt_name": "market_morning.s8_main_line", "model_output_id": moid,
    }


# ─── Orchestrator ────────────────────────────────────────────────────────

def run_market_morning(
    *,
    report_date: dt.date,
    data_cutoff_at: dt.datetime,
    user: str = "default",
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
    on_log(f"[run {str(run.report_run_id)[:8]}] starting Market morning report for {report_date} user={user}")

    try:
        prev = report_date - dt.timedelta(days=1)
        from ifa.core.report.freshness import preflight_freshness_check
        for line in preflight_freshness_check(engine, family="market", expected_date=prev):
            on_log(f"[freshness] ⚠ {line}")
        prefetched = prefetch_market_data(
            tushare=tushare, engine=engine, on_date=prev,
            aux_report_type="morning_long",
            end_bjt=to_bjt(data_cutoff_at),
            on_log=on_log,
            slot="morning",
        )
        on_log("enriching focus stocks (10 + 20)…")
        imp_data, reg_data = enrich_market_focus(
            tushare=tushare, on_date=prev,
            important=prefetched["important_focus"], regular=prefetched["regular_focus"],
            slot="morning",
        )
        ctx = MarketCtx(
            engine=engine, llm=llm, tushare=tushare, run=run, user=user,
            on_log=on_log, important_focus_data=imp_data, regular_focus_data=reg_data,
            **prefetched,
        )

        sections: list[dict] = []
        for label, builder in [
            ("S1 tone",          lambda: _build_s1_tone(ctx)),
            ("S2 three-aux",     lambda: build_three_aux_section(ctx, order=2,
                                       title="三辅报告（宏观 / Asset / Tech）摘要汇总",
                                       key="market_morning.s2_three_aux")),
            ("S3 index panel",   lambda: build_index_panel_section(ctx, order=3,
                                       title="昨日市场结构 · 指数与全 A 广度",
                                       key="market_morning.s3_index_panel")),
            ("S4 rotation",      lambda: build_rotation_section(ctx, order=4,
                                       title="板块轮动与主线候选",
                                       key="market_morning.s4_rotation")),
            ("S5 sentiment",     lambda: build_sentiment_section(ctx, order=5,
                                       title="市场情绪与短线生态",
                                       key="market_morning.s5_sentiment")),
            ("S6 dragon-tiger",  lambda: build_dragon_tiger_section(ctx, order=6,
                                       title="龙虎榜与机构 / 游资动向",
                                       key="market_morning.s6_dragon_tiger")),
            ("S7 news",          lambda: build_news_section(ctx, order=7,
                                       title="关键新闻、公告与政策事件摘要",
                                       key="market_morning.s7_news")),
            ("S8 main-line",     lambda: _build_s8_main_line(ctx)),
            ("S9 focus deep",    lambda: build_focus_deep_section(ctx, order=9,
                                       title="重点关注股票深度观察 (10)",
                                       key="market_morning.s9_focus_deep")),
            ("S10 focus brief",  lambda: build_focus_brief_section(ctx, order=10,
                                       title="普通关注股票简要观察 (20)",
                                       key="market_morning.s10_focus_brief")),
            ("S11 risks",        lambda: _build_s11_risk(ctx, sections)),
            ("S12 hypotheses",   lambda: _build_s12_hypotheses(ctx, sections)),
            ("S13 disclaimer",   _build_s13_disclaimer),
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

        from ifa.core.render.staleness import compute_staleness_warning
        # Morning data is for T-1 (the previous trading day, `prev` above).
        # Compare snap.trade_date against that, not run.report_date.
        staleness = compute_staleness_warning(
            report_date=prev,
            dated_objects=[ctx.breadth, *ctx.indices, *ctx.sw_rotation],
        )
        out_path = _render_and_save(run, sections, settings, user=user,
                                      staleness_warning=staleness)
        finalize_report_run(engine, run, status="succeeded", output_html_path=out_path)
        on_log(f"saved → {out_path}")
        return out_path
    except Exception as exc:
        finalize_report_run(engine, run, status="failed",
                            error_summary=f"{type(exc).__name__}: {exc}")
        raise


def _render_and_save(run: ReportRun, sections: list[dict], settings, *, user: str,
                      staleness_warning: str | None = None) -> Path:
    renderer = HtmlRenderer()
    cutoff_bjt_str = fmt_bjt(run.data_cutoff_at)
    generated_bjt_str = fmt_bjt(utc_now(), "%Y-%m-%d %H:%M")
    report = {
        "title": f"中国 A 股早盘报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": f"China A-Share Market Morning Report — Lindenwood Management LLC · @{user}",
        "report_date_bjt": run.report_date.strftime("%Y-%m-%d"),
        "data_cutoff_bjt": cutoff_bjt_str,
        "generated_at_bjt": generated_bjt_str,
        "template_version": TEMPLATE_VERSION,
        "run_mode": run.run_mode.value,
        "report_run_id_short": str(run.report_run_id)[:8],
        "sections": sections,
        "staleness_warning": staleness_warning,
    }
    html = renderer.render(report=report)
    from ifa.core.report.output import output_dir_for_run
    out_root = output_dir_for_run(settings, run)
    bjt_now = to_bjt(utc_now())
    fname = f"CN_market_morning_{run.report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
