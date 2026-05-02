"""Macro evening report — orchestrator + 11 section builders.

Sections per ifa-macro-v1 §5:
  S0  [implicit in template]    Header / fixed disclaimer
  S1  commentary                今日宏观一句话复盘
  S2  review_table              早盘假设 Review
  S3  news_list                 今日宏观数据与政策事件复盘
  S4  data_panel                核心宏观数据状态更新
  S5  liquidity_grid            流动性、资金与汇率复盘
  S6  cross_asset_grid          港股、商品与跨资产复盘
  S7  attribution               今日 A 股驱动归因
  S8  watchlist                 明日宏观观察清单
  S9  hypotheses_list           可沉淀的宏观判断资产
  S10 indicator_capture_table   新闻源抽取宏观数据更新
  S11 disclaimer                完整免责声明
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

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
    insert_model_output,
    insert_report_run,
    insert_section,
)
from ifa.core.tushare import TuShareClient
from ifa.families.macro import data, prompts
from ifa.families.macro.data import MarketDay
from ifa.families.macro.morning import (
    RuntimeCtx as MorningCtx,
    _build_s2_panel,
    _build_s3_liquidity,
    _build_s5_policy,
    _build_s6_cross_asset,
    _build_s10_capture,
    _build_s11_disclaimer,
    _persist_model_output,
    _safe_chat_json,
)

TEMPLATE_VERSION = "macro_evening_v2.1.0"
REPORT_FAMILY = "macro"
REPORT_TYPE = "evening_long"
SLOT = "evening"
MARKET = "china_a"


@dataclass
class EveningCtx(MorningCtx):
    market: MarketDay | None = None
    morning_hypotheses: list[dict[str, Any]] | None = None


def _load_morning_hypotheses(engine: Engine, *, report_date: dt.date) -> list[dict[str, Any]]:
    """Read this morning's hypothesis judgments to review tonight."""
    sql = text("""
        SELECT j.judgment_id, j.judgment_text, j.target, j.horizon, j.validation_method, j.confidence
          FROM report_judgments j
          JOIN report_runs r ON r.report_run_id = j.report_run_id
         WHERE r.report_family = 'macro'
           AND r.report_type = 'morning_long'
           AND r.report_date = :rd
           AND j.judgment_type = 'hypothesis'
           AND r.status = 'succeeded'
         ORDER BY r.completed_at DESC, j.created_at
         LIMIT 8
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"rd": report_date}).all()
    return [
        {
            "judgment_id": str(r.judgment_id),
            "hypothesis": r.judgment_text,
            "related": r.target,
            "validation_method": r.validation_method,
            "review_rule": r.validation_method,
            "confidence": r.confidence,
        }
        for r in rows
    ]


# ─── S1: evening one-paragraph commentary ──────────────────────────────────

def _build_e1_headline(ctx: EveningCtx) -> dict:
    md = ctx.market
    market_blob = "市场数据缺失"
    if md:
        market_blob = (
            f"上证 {md.sh_close} ({md.sh_pct:+.2f}%)；深成 {md.sz_close} ({md.sz_pct:+.2f}%)；"
            f"创业板 {md.cyb_close} ({md.cyb_pct:+.2f}%)；沪深300 {md.hs300_close} ({md.hs300_pct:+.2f}%)；"
            f"全 A 成交 {md.total_amount} 万亿元（前日 {md.total_amount_prev}）；"
            f"上涨/下跌/平 = {md.up_count} / {md.down_count} / {md.flat_count}"
        )
    cross = "; ".join(
        f"{a.name} {a.pct_change:+.2f}%" if a.pct_change is not None else f"{a.name} 数据缺失"
        for a in ctx.cross_asset[:6]
    )
    user = f"""
=== 今日 A 股市场 ===
{market_blob}

=== 跨资产 ===
{cross}

=== 活跃政策事件（最近 3 天） ===
{json.dumps([{"dim":p.policy_dimension,"sig":p.policy_signal,"title":p.event_title} for p in ctx.policy_events[:8]], ensure_ascii=False)}

=== 任务 ===
{prompts.EVENING_HEADLINE_INSTRUCTIONS}

=== 输出 schema ===
{prompts.EVENING_HEADLINE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1400,
    )
    moid = _persist_model_output(ctx, section_key="macro_evening.s1_headline",
                                 prompt_name="macro_evening.s1_headline",
                                 parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {"label": "晚盘综述", "text": "今日市场数据未能完整生成晚盘综述，请参考下文各分项更新。"}
        ctx.run.fallback_used = True
    return {
        "key": "macro_evening.s1_headline",
        "title": "今日宏观一句话复盘",
        "order": 1,
        "type": "commentary",
        "content_json": parsed,
        "prompt_name": "macro_evening.s1_headline",
        "model_output_id": moid,
    }


# ─── S2: morning hypothesis review ─────────────────────────────────────────

def _build_e2_review(ctx: EveningCtx) -> dict:
    morning = ctx.morning_hypotheses or []
    if not morning:
        return {
            "key": "macro_evening.s2_review",
            "title": "早盘假设 Review",
            "order": 2,
            "type": "review_table",
            "content_json": {
                "rows": [],
                "fallback_text": "今日未找到当天早盘报告的待验证假设（可能早报未生成或未成功）。",
            },
        }
    md = ctx.market
    market_blob = "市场数据缺失"
    if md:
        market_blob = (
            f"上证 {md.sh_close} ({md.sh_pct:+.2f}%)；深成 {md.sz_close} ({md.sz_pct:+.2f}%)；"
            f"创业板 {md.cyb_close} ({md.cyb_pct:+.2f}%)；沪深300 {md.hs300_close} ({md.hs300_pct:+.2f}%)；"
            f"全 A 成交 {md.total_amount} 万亿元（前日 {md.total_amount_prev}）；"
            f"上涨/下跌/平 = {md.up_count} / {md.down_count} / {md.flat_count}"
        )
    cross = "; ".join(
        f"{a.name} {a.pct_change:+.2f}%" if a.pct_change is not None else f"{a.name} 数据缺失"
        for a in ctx.cross_asset[:6]
    )
    candidates_text = "\n".join(
        f"[{i}] {h['hypothesis']}  · 验证规则: {h.get('review_rule') or '—'}  · 关联: {h.get('related') or '—'}"
        for i, h in enumerate(morning)
    )
    user = f"""
=== 今日 A 股市场快照 ===
{market_blob}

=== 跨资产快照 ===
{cross}

=== 早盘假设（按 candidate_index 排列） ===
{candidates_text}

=== 任务 ===
{prompts.REVIEW_INSTRUCTIONS}

=== 输出 schema ===
{prompts.REVIEW_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="macro_evening.s2_review",
                                 prompt_name="macro_evening.s2_review",
                                 parsed=parsed, resp=resp, status=status)
    rows: list[dict[str, Any]] = []
    if isinstance(parsed, dict):
        results = parsed.get("results") or []
        for entry in results:
            idx = entry.get("candidate_index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(morning):
                continue
            h = morning[idx]
            rows.append({
                "hypothesis": h["hypothesis"],
                "review_result": entry.get("review_result"),
                "review_result_display": entry.get("review_result_display") or entry.get("review_result"),
                "evidence_text": entry.get("evidence_text"),
                "lesson": entry.get("lesson"),
            })
            # Also persist a review row + update judgments review_status
            try:
                from ifa.core.report.run import text as _t
                with ctx.engine.begin() as conn:
                    conn.execute(_t("""
                        UPDATE report_judgments SET review_status = :rs
                         WHERE judgment_id = CAST(:jid AS UUID)
                    """), {"rs": (entry.get("review_result") or "pending"), "jid": h.get("judgment_id")})
                    conn.execute(_t("""
                        INSERT INTO report_reviews
                            (judgment_id, review_report_run_id, review_result, evidence_text, lesson)
                        VALUES
                            (CAST(:jid AS UUID), CAST(:rid AS UUID), :rr, :ev, :lz)
                    """), {
                        "jid": h["judgment_id"], "rid": str(ctx.run.report_run_id),
                        "rr": entry.get("review_result") or "not_applicable",
                        "ev": (entry.get("evidence_text") or "")[:500],
                        "lz": (entry.get("lesson") or "")[:500],
                    })
            except Exception:
                pass

    return {
        "key": "macro_evening.s2_review",
        "title": "早盘假设 Review",
        "order": 2,
        "type": "review_table",
        "content_json": {
            "rows": rows,
            "fallback_text": "今日早盘报告暂无可复盘判断。",
        },
        "prompt_name": "macro_evening.s2_review",
        "model_output_id": moid,
    }


# ─── S7: A-share attribution ───────────────────────────────────────────────

def _build_e7_attribution(ctx: EveningCtx) -> dict:
    md = ctx.market
    market_blob = "无数据"
    if md:
        market_blob = json.dumps({
            "trade_date": str(md.trade_date),
            "上证综指": {"close": md.sh_close, "pct": md.sh_pct},
            "深证成指": {"close": md.sz_close, "pct": md.sz_pct},
            "创业板": {"close": md.cyb_close, "pct": md.cyb_pct},
            "沪深300": {"close": md.hs300_close, "pct": md.hs300_pct},
            "全A成交（万亿元）": md.total_amount,
            "前日成交": md.total_amount_prev,
            "涨家数": md.up_count, "跌家数": md.down_count, "平家数": md.flat_count,
        }, ensure_ascii=False)
    cross_blob = json.dumps([
        {"name": a.name, "code": a.code, "latest": a.latest, "pct_change": a.pct_change}
        for a in ctx.cross_asset
    ], ensure_ascii=False)
    policy_blob = json.dumps([
        {"dim": p.policy_dimension, "sig": p.policy_signal, "title": p.event_title}
        for p in ctx.policy_events[:6]
    ], ensure_ascii=False)
    user = f"""
=== 今日 A 股 ===
{market_blob}

=== 跨资产 ===
{cross_blob}

=== 政策事件 ===
{policy_blob}

=== 任务 ===
{prompts.ATTRIBUTION_INSTRUCTIONS}

=== 输出 schema ===
{prompts.ATTRIBUTION_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="macro_evening.s7_attribution",
                                 prompt_name="macro_evening.s7_attribution",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"driver": "弱驱动", "cells": [], "commentary": ""}
    return {
        "key": "macro_evening.s7_attribution",
        "title": "今日 A 股驱动归因",
        "order": 7,
        "type": "attribution",
        "content_json": content,
        "prompt_name": "macro_evening.s7_attribution",
        "model_output_id": moid,
    }


# ─── S8: tomorrow's watchlist ─────────────────────────────────────────────

def _build_e8_watchlist(ctx: EveningCtx, prior_sections: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior_sections if s["key"].startswith("macro_evening.s")}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5000]}

=== 任务 ===
{prompts.WATCHLIST_INSTRUCTIONS}

=== 输出 schema ===
{prompts.WATCHLIST_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="macro_evening.s8_watchlist",
                                 prompt_name="macro_evening.s8_watchlist",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"items": []}
    return {
        "key": "macro_evening.s8_watchlist",
        "title": "明日宏观观察清单",
        "order": 8,
        "type": "watchlist",
        "content_json": content,
        "prompt_name": "macro_evening.s8_watchlist",
        "model_output_id": moid,
    }


# ─── S9: reviewable judgments produced tonight ─────────────────────────────

def _build_e9_reviewable(ctx: EveningCtx, prior_sections: list[dict]) -> dict:
    """Use the same hypothesis-generator prompt but framed for "next session(s)"."""
    ctx_blob = {s["key"]: s["content_json"] for s in prior_sections if s["key"].startswith("macro_evening.s")}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5000]}

=== 任务 ===
基于今日 A 股表现与跨资产/政策上下文，输出 3-5 条"可在下个交易日或多日内验证的宏观判断"，作为明日早报的待验证候选。
{prompts.HYPOTHESES_INSTRUCTIONS}

=== 输出 schema ===
{prompts.HYPOTHESES_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="macro_evening.s9_reviewable",
                                 prompt_name="macro_evening.s9_reviewable",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"hypotheses": []}
    for h in content.get("hypotheses", []) or []:
        try:
            insert_judgment(
                ctx.engine,
                report_run_id=ctx.run.report_run_id,
                section_key="macro_evening.s9_reviewable",
                judgment_type="hypothesis",
                judgment_text=h.get("hypothesis", ""),
                target=", ".join(h.get("related_markets_or_sectors") or [])[:300],
                horizon=h.get("observation_window") or "tomorrow",
                confidence=(h.get("confidence") or "medium").lower(),
                validation_method=h.get("review_rule"),
            )
        except Exception:
            pass
    return {
        "key": "macro_evening.s9_reviewable",
        "title": "可沉淀的宏观判断资产（明日复盘）",
        "order": 9,
        "type": "hypotheses_list",
        "content_json": content,
        "prompt_name": "macro_evening.s9_reviewable",
        "model_output_id": moid,
    }


# ─── Orchestrator ──────────────────────────────────────────────────────────

def run_macro_evening(
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
    on_log(f"[run {str(run.report_run_id)[:8]}] starting evening report for {report_date} cutoff {fmt_bjt(data_cutoff_at)} BJT")

    try:
        on_log("fetching macro panel…")
        panel = data.fetch_macro_panel(tushare)
        on_log("fetching liquidity (post-close best-effort)…")
        liquidity = data.fetch_liquidity_snapshot(tushare, ref_date=report_date)
        on_log("fetching cross-asset (today best-effort)…")
        cross_asset = data.fetch_cross_asset(tushare, ref_date=report_date)
        on_log("fetching today's A-share market state…")
        market = data.fetch_market_day(tushare, on_date=report_date)
        on_log("reading text-derived + active policy events…")
        text_derived = data.fetch_text_derived(engine, since_days=120)
        policy_events = data.fetch_active_policy_events(engine, since_days=14)
        on_log("loading this morning's hypotheses for review…")
        morning_hypotheses = _load_morning_hypotheses(engine, report_date=report_date)

        ctx = EveningCtx(
            engine=engine, llm=llm, tushare=tushare, run=run,
            panel=panel, liquidity=liquidity, cross_asset=cross_asset,
            text_derived=text_derived, policy_events=policy_events, on_log=on_log,
            market=market, morning_hypotheses=morning_hypotheses,
        )

        sections: list[dict] = []
        # Build via reusing morning builders where structure is identical.
        for label, builder in [
            ("E1 headline",      lambda: _build_e1_headline(ctx)),
            ("E2 review",        lambda: _build_e2_review(ctx)),
            ("E3 news",          lambda: _build_e3_news(ctx)),
            ("E4 panel",         lambda: _build_e4_panel(ctx)),
            ("E5 liquidity",     lambda: _build_e5_liquidity(ctx)),
            ("E6 cross asset",   lambda: _build_e6_cross_asset(ctx)),
            ("E7 attribution",   lambda: _build_e7_attribution(ctx)),
            ("E8 watchlist",     lambda: _build_e8_watchlist(ctx, sections)),
            ("E9 reviewable",    lambda: _build_e9_reviewable(ctx, sections)),
            ("E10 capture",      lambda: _build_e10_capture(ctx)),
            ("E11 disclaimer",   _build_s11_disclaimer),
        ]:
            t0 = time.monotonic()
            on_log(f"building {label}…")
            sec = builder()
            sections.append(sec)
            insert_section(
                engine,
                report_run_id=run.report_run_id,
                section_key=sec["key"], section_title=sec["title"], section_order=sec["order"],
                content_json=sec["content_json"],
                prompt_name=sec.get("prompt_name"),
                prompt_version=prompts.PROMPT_BUNDLE_VERSION,
                model_output_id=sec.get("model_output_id"),
                fallback_used=sec.get("fallback_used", False),
            )
            on_log(f"  {label} done in {time.monotonic()-t0:.1f}s")

        out_path = _render_and_save_evening(run, sections, settings)
        finalize_report_run(engine, run, status="succeeded", output_html_path=out_path)
        on_log(f"saved → {out_path}")
        return out_path

    except Exception as exc:
        finalize_report_run(engine, run, status="failed",
                            error_summary=f"{type(exc).__name__}: {exc}")
        raise


# Reuse morning builders, but rewrite section_key prefix to macro_evening.*
def _retag(sec: dict, new_key: str, new_title: str, new_order: int) -> dict:
    sec = dict(sec)
    sec["key"] = new_key
    sec["title"] = new_title
    sec["order"] = new_order
    sec["prompt_name"] = new_key
    return sec


def _build_e3_news(ctx: EveningCtx) -> dict:
    from ifa.families.macro.morning import _build_s4_news
    sec = _build_s4_news(ctx)
    return _retag(sec, "macro_evening.s3_news", "今日宏观数据与政策事件复盘", 3)


def _build_e4_panel(ctx: EveningCtx) -> dict:
    sec = _build_s2_panel(ctx)
    return _retag(sec, "macro_evening.s4_panel", "核心宏观数据状态更新", 4)


def _build_e5_liquidity(ctx: EveningCtx) -> dict:
    sec = _build_s3_liquidity(ctx)
    return _retag(sec, "macro_evening.s5_liquidity", "流动性、资金与汇率复盘", 5)


def _build_e6_cross_asset(ctx: EveningCtx) -> dict:
    sec = _build_s6_cross_asset(ctx)
    return _retag(sec, "macro_evening.s6_cross_asset", "港股、商品与跨资产复盘", 6)


def _build_e10_capture(ctx: EveningCtx) -> dict:
    sec = _build_s10_capture(ctx)
    return _retag(sec, "macro_evening.s10_capture", "新闻源抽取宏观数据更新", 10)


def _render_and_save_evening(run: ReportRun, sections: list[dict], settings) -> Path:
    renderer = HtmlRenderer()
    cutoff_bjt_str = fmt_bjt(run.data_cutoff_at)
    generated_bjt_str = fmt_bjt(utc_now(), "%Y-%m-%d %H:%M")
    report = {
        "title": f"中国宏观晚盘报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": "China Macro Post-Close Briefing — Lindenwood Management LLC",
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
    fname = f"CN_macro_evening_{run.report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
