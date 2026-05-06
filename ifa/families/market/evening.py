"""A-share Main evening report — orchestrator.

Sections (matches mainreport.txt §6.2):
  S1  commentary               今日 A 股复盘一句话
  S2  index_panel              指数与市场结构复盘
  S3  category_strength        板块轮动与主线复盘
  S4  sentiment_grid           市场情绪与短线生态复盘
  S5  dragon_tiger             龙虎榜与机构 / 游资复盘
  S6  three_aux_summary        三辅报告验证汇总
  S7  review_table             早报主报告假设 Review
  S8  review_table             中报判断 Review
  S9  focus_deep               重点关注股票复盘 (10)
  S10 focus_brief              普通关注股票复盘 (20)
  S11 attribution              今日 A 股驱动归因
  S12 hypotheses_list          今日可沉淀判断资产 / 明日待验证
  S13 watchlist                明日市场观察清单
  S14 disclaimer
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import text

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
    build_rotation_section,
    build_sentiment_section,
    build_three_aux_section,
    enrich_market_focus,
    prefetch_market_data,
)

TEMPLATE_VERSION = "market_evening_v2.1.0"
REPORT_FAMILY = "main"
REPORT_TYPE = "evening_long"
SLOT = "evening"
MARKET = "china_a"


def _load_hypotheses(engine, *, report_date: dt.date, slot_report_type: str) -> list[dict]:
    sql = text("""
        SELECT j.judgment_id, j.judgment_text, j.target, j.horizon,
               j.validation_method, j.confidence
          FROM report_judgments j
          JOIN report_runs r ON r.report_run_id = j.report_run_id
         WHERE r.report_family = 'main'
           AND r.report_type = :rt
           AND r.report_date = :rd
           AND j.judgment_type = 'hypothesis'
           AND r.status = 'succeeded'
         ORDER BY r.completed_at DESC, j.created_at
         LIMIT 8
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"rt": slot_report_type, "rd": report_date}).all()
    return [
        {"judgment_id": str(r.judgment_id), "hypothesis": r.judgment_text,
         "related": r.target, "review_rule": r.validation_method,
         "confidence": r.confidence}
        for r in rows
    ]


# ─── E1 evening headline ──────────────────────────────────────────────────

def _build_e1_headline(ctx: MarketCtx, morning_hyps: list[dict], noon_hyps: list[dict]) -> dict:
    breadth = ctx.breadth
    indices = "; ".join(f"{s.name} {s.pct_change:+.2f}%"
                          for s in ctx.indices if s.pct_change is not None)
    main_top = ", ".join(f"{s.name} {s.pct_change:+.2f}%"
                          for s in ctx.main_lines[:6] if s.pct_change is not None)
    aux_blob = {f: {"headline": ctx.aux_summaries.get(f).headline if ctx.aux_summaries.get(f) else None,
                     "tone_or_state": ctx.aux_summaries.get(f).tone_or_state if ctx.aux_summaries.get(f) else None}
                for f in ("macro", "asset", "tech")}
    morn_block = (
        "\n".join(f"[{i+1}] {h.get('hypothesis','')}" for i, h in enumerate(morning_hyps[:6]))
        if morning_hyps else "(无 — 早报未生成或假设为空)"
    )
    noon_block = (
        "\n".join(f"[{i+1}] {h.get('hypothesis','')}" for i, h in enumerate(noon_hyps[:6]))
        if noon_hyps else "(无 — 中报未生成或假设为空)"
    )
    user = f"""
=== 今日 A 股市场（收盘） ===
指数: {indices}
全 A 成交 {breadth.total_amount} 万亿；前日 {breadth.total_amount_prev}
涨/跌/平 = {breadth.up_count}/{breadth.down_count}/{breadth.flat_count}
涨停 {breadth.limit_up_count}, 跌停 {breadth.limit_down_count}, 连板 {breadth.max_consec_streak}, 炸板率 {breadth.broke_limit_pct}

=== 主线候选板块 ===
{main_top}

=== 三辅报告 ===
{json.dumps(aux_blob, ensure_ascii=False, indent=2)}

=== 早报假设（{len(morning_hyps)} 条；E7 单独逐条复盘）===
{morn_block}

=== 中报假设（{len(noon_hyps)} 条；E8 单独逐条复盘）===
{noon_block}

注意：本节是收盘 headline，需基于今日实际收盘对早/中报方向做总结，不要写"早报假设未提供"——上面已给。

=== 任务 ===
{prompts.EVENING_HEADLINE_INSTRUCTIONS}

=== 输出 schema ===
{prompts.EVENING_HEADLINE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="market_evening.s1_headline",
                                  prompt_name="market_evening.s1_headline",
                                  parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {"label": "今日 A 股复盘",
                  "text": "今日数据未能形成合力；建议参考下文各分项复盘。"}
        ctx.run.fallback_used = True
    return {
        "key": "market_evening.s1_headline", "title": "今日 A 股复盘",
        "order": 1, "type": "commentary", "content_json": parsed,
        "prompt_name": "market_evening.s1_headline", "model_output_id": moid,
    }


# ─── Generic review builder ──────────────────────────────────────────────

def _build_review(ctx: MarketCtx, *, hyps: list[dict], order: int, title: str, key: str) -> dict:
    if not hyps:
        return {"key": key, "title": title, "order": order, "type": "review_table",
                "content_json": {"rows": [],
                                  "fallback_text": "未找到相应的待 review 假设；可能上一份报告未生成。"}}
    breadth = ctx.breadth
    indices = "; ".join(f"{s.name} {s.pct_change:+.2f}%"
                          for s in ctx.indices if s.pct_change is not None)
    main_top = "; ".join(f"{s.name} {s.pct_change:+.2f}%"
                          for s in ctx.main_lines[:8] if s.pct_change is not None)
    sw_top = "; ".join(f"{s.name} {s.pct_change:+.2f}%"
                        for s in sorted(ctx.sw_rotation, key=lambda x: x.pct_change or 0,
                                         reverse=True)[:6] if s.pct_change is not None)
    cands = "\n".join(
        f"[{i}] {h['hypothesis']}  · 验证规则: {h.get('review_rule') or '—'}  · 关联: {h.get('related') or '—'}"
        for i, h in enumerate(hyps)
    )
    user = f"""
=== 今日全天市场 ===
{indices}
全 A 成交 {breadth.total_amount} 万亿；涨/跌 {breadth.up_count}/{breadth.down_count}；
涨停 {breadth.limit_up_count}, 跌停 {breadth.limit_down_count}, 连板 {breadth.max_consec_streak}
申万领涨: {sw_top}
主线候选: {main_top}

=== 假设列表 ===
{cands}

=== 任务 ===
{prompts.REVIEW_INSTRUCTIONS}

=== 输出 schema ===
{prompts.REVIEW_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key=key, prompt_name=key,
                                  parsed=parsed, resp=resp, status=status)
    rows = []
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(hyps):
                continue
            h = hyps[idx]
            rows.append({
                "hypothesis": h["hypothesis"],
                "review_result": entry.get("review_result"),
                "review_result_display": entry.get("review_result_display") or entry.get("review_result"),
                "evidence_text": entry.get("evidence_text"),
                "lesson": entry.get("lesson"),
            })
            try:
                with ctx.engine.begin() as conn:
                    conn.execute(text("""
                        UPDATE report_judgments SET review_status=:rs
                         WHERE judgment_id = CAST(:jid AS UUID)
                    """), {"rs": entry.get("review_result") or "pending", "jid": h["judgment_id"]})
                    conn.execute(text("""
                        INSERT INTO report_reviews
                            (judgment_id, review_report_run_id, review_result, evidence_text, lesson)
                        VALUES (CAST(:jid AS UUID), CAST(:rid AS UUID), :rr, :ev, :lz)
                    """), {"jid": h["judgment_id"], "rid": str(ctx.run.report_run_id),
                            "rr": entry.get("review_result") or "not_applicable",
                            "ev": (entry.get("evidence_text") or "")[:500],
                            "lz": (entry.get("lesson") or "")[:500]})
            except Exception:
                pass
    return {
        "key": key, "title": title, "order": order, "type": "review_table",
        "content_json": {"rows": rows},
        "prompt_name": key, "model_output_id": moid,
    }


# ─── E11 attribution ─────────────────────────────────────────────────────

def _build_e11_attribution(ctx: MarketCtx) -> dict:
    market_data = {
        "trade_date": str(ctx.indices[0].trade_date) if ctx.indices and ctx.indices[0].trade_date else None,
        "indices": [{"name": s.name, "close": s.close, "pct": s.pct_change} for s in ctx.indices],
        "全A成交（万亿元）": ctx.breadth.total_amount,
        "前日成交": ctx.breadth.total_amount_prev,
        "涨家数": ctx.breadth.up_count, "跌家数": ctx.breadth.down_count,
        "涨停": ctx.breadth.limit_up_count, "跌停": ctx.breadth.limit_down_count,
        "连板高度": ctx.breadth.max_consec_streak,
    }
    sw_top = [{"name": s.name, "pct": s.pct_change} for s in
              sorted(ctx.sw_rotation, key=lambda x: x.pct_change or 0, reverse=True)[:8]
              if s.pct_change is not None]
    main_top = [{"name": s.name, "pct": s.pct_change} for s in ctx.main_lines[:8]
                 if s.pct_change is not None]
    user = f"""
=== 今日市场 ===
{json.dumps(market_data, ensure_ascii=False, indent=2)}

=== 申万一级领涨 ===
{json.dumps(sw_top, ensure_ascii=False)}

=== 主线候选 ===
{json.dumps(main_top, ensure_ascii=False)}

=== 任务 ===
{prompts.ATTRIBUTION_INSTRUCTIONS}

=== 输出 schema ===
{prompts.ATTRIBUTION_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="market_evening.s11_attribution",
                                  prompt_name="market_evening.s11_attribution",
                                  parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"driver": "弱驱动", "cells": [], "commentary": ""}
    return {
        "key": "market_evening.s11_attribution",
        "title": "今日 A 股驱动归因",
        "order": 11, "type": "attribution",
        "content_json": content,
        "prompt_name": "market_evening.s11_attribution", "model_output_id": moid,
    }


# ─── E12 sticky judgments + tomorrow watchlist ────────────────────────────

def _build_e12_sticky(ctx: MarketCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5000]}

=== 任务 ===
{prompts.STICKY_JUDGMENTS_INSTRUCTIONS}

也额外输出 3-5 条"明日待验证 Tech / Asset / Macro 主线相关假设"作为 hypotheses 数组（保持 hypotheses_list 兼容字段）。

=== 输出 schema ===
{{
  "judgments":[{{"judgment":"...","result":"验证","next_step":"保留短期框架"}}],
  "hypotheses":[{{"hypothesis":"...","validation_method":"...","observation_window":"明日开盘后","related_markets_or_sectors":["..."],"review_rule":"...","confidence":"medium"}}]
}}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="market_evening.s12_sticky",
                                  prompt_name="market_evening.s12_sticky",
                                  parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {"judgments": [], "hypotheses": []}
    # Persist tomorrow's hypotheses
    for h in parsed.get("hypotheses", []) or []:
        try:
            insert_judgment(
                ctx.engine, report_run_id=ctx.run.report_run_id,
                section_key="market_evening.s12_sticky", judgment_type="hypothesis",
                judgment_text=h.get("hypothesis", ""),
                target=", ".join(h.get("related_markets_or_sectors") or [])[:300],
                horizon=h.get("observation_window") or "tomorrow",
                confidence=(h.get("confidence") or "medium").lower(),
                validation_method=h.get("review_rule"),
            )
        except Exception:
            pass
    # Render via hypotheses_list partial
    return {
        "key": "market_evening.s12_sticky",
        "title": "今日可沉淀判断资产 / 明日待验证候选",
        "order": 12, "type": "hypotheses_list",
        "content_json": {"hypotheses": parsed.get("hypotheses", []) or []},
        "prompt_name": "market_evening.s12_sticky", "model_output_id": moid,
    }


def _build_e13_watchlist(ctx: MarketCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:4500]}

=== 任务 ===
{prompts.WATCHLIST_INSTRUCTIONS}

=== 输出 schema ===
{prompts.WATCHLIST_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="market_evening.s13_watchlist",
                                  prompt_name="market_evening.s13_watchlist",
                                  parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"items": []}
    return {
        "key": "market_evening.s13_watchlist", "title": "明日市场观察清单",
        "order": 13, "type": "watchlist", "content_json": content,
        "prompt_name": "market_evening.s13_watchlist", "model_output_id": moid,
    }


def _build_e14_disclaimer() -> dict:
    return {"key": "market_evening.s14_disclaimer", "title": "免责声明",
            "order": 14, "type": "disclaimer",
            "content_json": {"paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
                             "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH}}


# ─── Orchestrator ────────────────────────────────────────────────────────

def run_market_evening(
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
    on_log(f"[run {str(run.report_run_id)[:8]}] starting Market evening report for {report_date} user={user}")

    try:
        prefetched = prefetch_market_data(
            tushare=tushare, engine=engine, on_date=report_date,
            aux_report_type="evening_long",   # use today's evening aux for cross-validation
            end_bjt=to_bjt(data_cutoff_at),
            on_log=on_log,
            slot="evening",
        )
        on_log("enriching focus stocks (10 + 20)…")
        imp_data, reg_data = enrich_market_focus(
            tushare=tushare, on_date=report_date,
            important=prefetched["important_focus"], regular=prefetched["regular_focus"],
            slot="evening",
        )
        on_log("loading morning + noon hypotheses…")
        morning_hyps = _load_hypotheses(engine, report_date=report_date, slot_report_type="morning_long")
        noon_hyps = _load_hypotheses(engine, report_date=report_date, slot_report_type="midday_long")
        ctx = MarketCtx(
            engine=engine, llm=llm, tushare=tushare, run=run, user=user,
            on_log=on_log, important_focus_data=imp_data, regular_focus_data=reg_data,
            morning_hypotheses=morning_hyps, noon_hypotheses=noon_hyps,
            **prefetched,
        )

        sections: list[dict] = []
        for label, builder in [
            ("E1 headline",     lambda: _build_e1_headline(ctx, morning_hyps, noon_hyps)),
            ("E2 index panel",  lambda: build_index_panel_section(ctx, order=2,
                                       title="指数与市场结构复盘",
                                       key="market_evening.s2_index_panel")),
            ("E3 rotation",     lambda: build_rotation_section(ctx, order=3,
                                       title="板块轮动与主线复盘",
                                       key="market_evening.s3_rotation")),
            ("E4 sentiment",    lambda: build_sentiment_section(ctx, order=4,
                                       title="市场情绪与短线生态复盘",
                                       key="market_evening.s4_sentiment")),
            ("E5 dragon-tiger", lambda: build_dragon_tiger_section(ctx, order=5,
                                       title="龙虎榜与机构 / 游资复盘",
                                       key="market_evening.s5_dragon_tiger")),
            ("E6 three-aux",    lambda: build_three_aux_section(ctx, order=6,
                                       title="三辅报告（今日晚报）验证汇总",
                                       key="market_evening.s6_three_aux")),
            ("E7 morning rev",  lambda: _build_review(ctx, hyps=morning_hyps,
                                       order=7, title="早报主报告假设 Review",
                                       key="market_evening.s7_morning_review")),
            ("E8 noon rev",     lambda: _build_review(ctx, hyps=noon_hyps,
                                       order=8, title="中报判断 Review",
                                       key="market_evening.s8_noon_review")),
            ("E9 focus deep",   lambda: build_focus_deep_section(ctx, order=9,
                                       title="重点关注股票复盘 (10)",
                                       key="market_evening.s9_focus_deep")),
            ("E10 focus brief", lambda: build_focus_brief_section(ctx, order=10,
                                       title="普通关注股票复盘 (20)",
                                       key="market_evening.s10_focus_brief")),
            ("E11 attribution", lambda: _build_e11_attribution(ctx)),
            ("E12 sticky",      lambda: _build_e12_sticky(ctx, sections)),
            ("E13 watchlist",   lambda: _build_e13_watchlist(ctx, sections)),
            ("E14 disclaimer",  _build_e14_disclaimer),
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

        out_path = _render_and_save(run, sections, settings, user=user)
        finalize_report_run(engine, run, status="succeeded", output_html_path=out_path)
        on_log(f"saved → {out_path}")
        return out_path
    except Exception as exc:
        finalize_report_run(engine, run, status="failed",
                            error_summary=f"{type(exc).__name__}: {exc}")
        raise


def _render_and_save(run: ReportRun, sections: list[dict], settings, *, user: str) -> Path:
    renderer = HtmlRenderer()
    cutoff_bjt_str = fmt_bjt(run.data_cutoff_at)
    generated_bjt_str = fmt_bjt(utc_now(), "%Y-%m-%d %H:%M")
    report = {
        "title": f"中国 A 股晚盘报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": f"China A-Share Market Evening Report — Lindenwood Management LLC · @{user}",
        "report_date_bjt": run.report_date.strftime("%Y-%m-%d"),
        "data_cutoff_bjt": cutoff_bjt_str,
        "generated_at_bjt": generated_bjt_str,
        "template_version": TEMPLATE_VERSION,
        "run_mode": run.run_mode.value,
        "report_run_id_short": str(run.report_run_id)[:8],
        "sections": sections,
    }
    html = renderer.render(report=report)
    from ifa.core.report.output import output_dir_for_run
    out_root = output_dir_for_run(settings, run)
    bjt_now = to_bjt(utc_now())
    fname = f"CN_market_evening_{run.report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
