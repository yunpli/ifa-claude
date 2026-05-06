"""A-share Main noon (midday) report — orchestrator.

Sections (matches mainreport.txt §5.2):
  S1  tech_tone (noon variant)        午间总判断
  S2  index_panel                     上午指数与市场结构
  S3  review_table                    早报假设初步验证
  S4  category_strength               上午板块轮动与主线状态
  S5  index_panel (flows-only)        资金结构与实时流动性
  S6  sentiment_grid                  市场情绪 · 午间状态
  S7  category_strength (main_line)   重点关注板块午间更新
  S8  focus_deep                      重点关注股票午间更新 (10)
  S9  focus_brief                     普通关注股票午间简表 (20)
  S10 scenario_plans                  午后情景计划
  S11 commentary (review hooks)       晚报需要重点 Review 的问题
  S12 disclaimer
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
    insert_report_run,
    insert_section,
)
from ifa.core.tushare import TuShareClient
from ifa.families.macro.morning import _safe_chat_json

from . import prompts
from ._common import (
    MarketCtx,
    _persist_model_output,
    build_focus_brief_section,
    build_focus_deep_section,
    build_index_panel_section,
    build_rotation_section,
    build_sentiment_section,
    enrich_market_focus,
    prefetch_market_data,
)

TEMPLATE_VERSION = "market_noon_v2.1.0"
REPORT_FAMILY = "main"
REPORT_TYPE = "midday_long"
SLOT = "noon"
MARKET = "china_a"


def _load_morning_hypotheses(engine, *, report_date: dt.date) -> list[dict]:
    sql = text("""
        SELECT j.judgment_id, j.judgment_text, j.target, j.horizon,
               j.validation_method, j.confidence
          FROM report_judgments j
          JOIN report_runs r ON r.report_run_id = j.report_run_id
         WHERE r.report_family = 'main'
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
        {"judgment_id": str(r.judgment_id), "hypothesis": r.judgment_text,
         "related": r.target, "review_rule": r.validation_method,
         "confidence": r.confidence}
        for r in rows
    ]


# ─── S1 noon tone ─────────────────────────────────────────────────────────

def _build_n1_tone(ctx: MarketCtx, morning_hyps: list[dict]) -> dict:
    breadth = ctx.breadth
    indices = "; ".join(f"{s.name} {s.pct_change:+.2f}%"
                          for s in ctx.indices if s.pct_change is not None)
    breadth_blob = (
        f"全 A 成交 {breadth.total_amount} 万亿 (前日 {breadth.total_amount_prev}); "
        f"涨/跌={breadth.up_count}/{breadth.down_count}; "
        f"涨停 {breadth.limit_up_count}/跌停 {breadth.limit_down_count}; "
        f"连板高度 {breadth.max_consec_streak}"
    )
    main_top = ", ".join(
        f"{s.name} {s.pct_change:+.2f}%" for s in ctx.main_lines[:6] if s.pct_change is not None
    )
    if morning_hyps:
        hyp_lines = "\n".join(
            f"[{i+1}] {h.get('hypothesis', '')}" for i, h in enumerate(morning_hyps[:8])
        )
        hyp_block = (
            f"=== 早报假设（{len(morning_hyps)} 条，已在 S3 单独逐条验证）===\n"
            f"{hyp_lines}\n\n"
            "注意：午间总判断需结合早报假设的整体方向，不要写『早报假设未提供』——本节已传入。\n"
        )
    else:
        hyp_block = "=== 早报假设 ===\n(无 — 早报未生成或未成功)\n"
    user = f"""
=== 报告时点 ===
{ctx.run.report_date} 午间 cutoff {fmt_bjt(ctx.run.data_cutoff_at)} 北京时间

=== 上午指数 ===
{indices}

=== 全 A 广度 / 情绪 ===
{breadth_blob}

=== 主线候选 (THS 概念) ===
{main_top}

{hyp_block}

=== 任务 ===
{prompts.NOON_TONE_INSTRUCTIONS}

=== 输出 schema ===
{prompts.NOON_TONE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="market_noon.s1_tone",
                                  prompt_name="market_noon.s1_tone",
                                  parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {"market_state": "震荡", "market_state_short": "震荡",
                  "main_line_state": "待确立", "risk_appetite": "中",
                  "afternoon_basis": "数据不足",
                  "headline": "上午盘信号不强；下午等待量能确认。",
                  "summary": "数据不足以形成强结论。",
                  "validation_points": []}
        ctx.run.fallback_used = True
    pay = {
        "tech_state": parsed.get("market_state"),
        "tech_state_short": parsed.get("market_state_short"),
        "strongest_layer": parsed.get("afternoon_basis"),
        "risk_level": parsed.get("risk_appetite"),
        "headline": parsed.get("headline"),
        "summary": parsed.get("summary"),
        "validation_points": parsed.get("validation_points") or [],
    }
    return {
        "key": "market_noon.s1_tone", "title": "午间总判断",
        "order": 1, "type": "tech_tone", "content_json": pay,
        "prompt_name": "market_noon.s1_tone", "model_output_id": moid,
    }


# ─── S3 morning hypothesis review ─────────────────────────────────────────

def _build_n3_review(ctx: MarketCtx, hyps: list[dict]) -> dict:
    if not hyps:
        return {"key": "market_noon.s3_review", "title": "早报假设初步验证",
                "order": 3, "type": "review_table",
                "content_json": {"rows": [],
                                  "fallback_text": "今日未找到早报主报告假设。"}}
    breadth = ctx.breadth
    indices = "; ".join(f"{s.name} {s.pct_change:+.2f}%"
                          for s in ctx.indices if s.pct_change is not None)
    main_top = "; ".join(f"{s.name} {s.pct_change:+.2f}%"
                          for s in ctx.main_lines[:8] if s.pct_change is not None)
    cands = "\n".join(
        f"[{i}] {h['hypothesis']}  · 验证规则: {h.get('review_rule') or '—'}  · 关联: {h.get('related') or '—'}"
        for i, h in enumerate(hyps)
    )
    user = f"""
=== 上午盘市场快照 ===
{indices}
全 A 成交 {breadth.total_amount} 万亿；涨/跌 {breadth.up_count}/{breadth.down_count}；
涨停 {breadth.limit_up_count}, 跌停 {breadth.limit_down_count}, 连板 {breadth.max_consec_streak}
主线候选: {main_top}

=== 早报假设 ===
{cands}

=== 任务 ===
{prompts.REVIEW_INSTRUCTIONS}

=== 输出 schema ===
{prompts.REVIEW_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="market_noon.s3_review",
                                  prompt_name="market_noon.s3_review",
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
    return {
        "key": "market_noon.s3_review", "title": "早报假设初步验证 (上午盘)",
        "order": 3, "type": "review_table",
        "content_json": {"rows": rows},
        "prompt_name": "market_noon.s3_review", "model_output_id": moid,
    }


# ─── S10 scenario plans ──────────────────────────────────────────────────

def _build_n10_scenarios(ctx: MarketCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5000]}

=== 任务 ===
{prompts.NOON_SCENARIO_INSTRUCTIONS}

=== 输出 schema ===
{prompts.NOON_SCENARIO_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="market_noon.s10_scenarios",
                                  prompt_name="market_noon.s10_scenarios",
                                  parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"scenarios": []}
    return {
        "key": "market_noon.s10_scenarios", "title": "午后情景计划",
        "order": 10, "type": "scenario_plans", "content_json": content,
        "prompt_name": "market_noon.s10_scenarios", "model_output_id": moid,
    }


def _build_n11_review_hooks(ctx: MarketCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:4500]}

=== 任务 ===
{prompts.NOON_REVIEW_HOOKS_INSTRUCTIONS}

=== 输出 schema ===
{prompts.NOON_REVIEW_HOOKS_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1400,
    )
    moid = _persist_model_output(ctx, section_key="market_noon.s11_review_hooks",
                                  prompt_name="market_noon.s11_review_hooks",
                                  parsed=parsed, resp=resp, status=status)
    hooks = (parsed.get("review_hooks") if isinstance(parsed, dict) else None) or []
    text_lines = [f"<strong>{h.get('question')}</strong>—{h.get('why_it_matters')}" for h in hooks]
    body = "<br><br>".join(text_lines) if text_lines else "今日午后无明显需要晚报重点 review 的问题。"
    return {
        "key": "market_noon.s11_review_hooks",
        "title": "晚报需要重点 Review 的问题",
        "order": 11, "type": "commentary",
        "content_json": {"label": "午报埋钩", "text": body},
        "prompt_name": "market_noon.s11_review_hooks", "model_output_id": moid,
    }


def _build_n12_disclaimer() -> dict:
    return {"key": "market_noon.s12_disclaimer", "title": "免责声明",
            "order": 12, "type": "disclaimer",
            "content_json": {"paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
                             "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH}}


# ─── Orchestrator ────────────────────────────────────────────────────────

def run_market_noon(
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
    on_log(f"[run {str(run.report_run_id)[:8]}] starting Market noon report for {report_date} user={user}")

    # Noon's "today" data comes from realtime APIs (rt_k / rt_min_daily / stk_limit),
    # NOT from raw_daily/sw_daily etc. — those are EOD endpoints TuShare publishes
    # ~17:00, hours after this report runs. The DB freshness check therefore
    # validates *historical context* (sparkline + ranking) is up-to-date through
    # the previous trading day, not today.
    from ifa.core.calendar import prev_trading_day
    from ifa.core.report.freshness import preflight_freshness_check
    _prev = prev_trading_day(engine, report_date)
    for line in preflight_freshness_check(engine, family="market", expected_date=_prev, slot="noon"):
        on_log(f"[freshness] ⚠ {line}")

    try:
        # For noon report, use TODAY's data (best-effort intraday)
        prefetched = prefetch_market_data(
            tushare=tushare, engine=engine, on_date=report_date,
            aux_report_type="morning_long",
            end_bjt=to_bjt(data_cutoff_at),
            on_log=on_log,
            slot="noon",
        )
        on_log("enriching focus stocks (10 + 20)…")
        imp_data, reg_data = enrich_market_focus(
            tushare=tushare, on_date=report_date,
            important=prefetched["important_focus"], regular=prefetched["regular_focus"],
            slot="noon",
        )
        on_log("loading morning hypotheses…")
        morning_hyps = _load_morning_hypotheses(engine, report_date=report_date)
        ctx = MarketCtx(
            engine=engine, llm=llm, tushare=tushare, run=run, user=user,
            on_log=on_log, important_focus_data=imp_data, regular_focus_data=reg_data,
            morning_hypotheses=morning_hyps,
            **prefetched,
        )

        sections: list[dict] = []
        for label, builder in [
            ("N1 tone",         lambda: _build_n1_tone(ctx, morning_hyps)),
            ("N2 index panel",  lambda: build_index_panel_section(ctx, order=2,
                                       title="上午指数与市场结构",
                                       key="market_noon.s2_index_panel")),
            ("N3 review",       lambda: _build_n3_review(ctx, morning_hyps)),
            ("N4 rotation",     lambda: build_rotation_section(ctx, order=4,
                                       title="上午板块轮动与主线状态",
                                       key="market_noon.s4_rotation")),
            ("N5 sentiment",    lambda: build_sentiment_section(ctx, order=5,
                                       title="市场情绪 · 午间状态",
                                       key="market_noon.s5_sentiment")),
            ("N6 focus deep",   lambda: build_focus_deep_section(ctx, order=6,
                                       title="重点关注股票午间更新 (10)",
                                       key="market_noon.s6_focus_deep")),
            ("N7 focus brief",  lambda: build_focus_brief_section(ctx, order=7,
                                       title="普通关注股票午间简表 (20)",
                                       key="market_noon.s7_focus_brief")),
            ("N10 scenarios",   lambda: _build_n10_scenarios(ctx, sections)),
            ("N11 review hooks",lambda: _build_n11_review_hooks(ctx, sections)),
            ("N12 disclaimer",  _build_n12_disclaimer),
        ]:
            t0 = time.monotonic()
            on_log(f"building {label}…")
            sec = builder()
            if sec is None:
                # Builder signalled "data not available — drop this section
                # entirely rather than show an empty placeholder."
                on_log(f"  {label} skipped (data not available at this slot)")
                continue
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
        staleness = compute_staleness_warning(
            report_date=run.report_date,
            dated_objects=[ctx.breadth, *ctx.indices, *ctx.sw_rotation,
                            *ctx.fund_top, *ctx.dragon_tiger],
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
        "title": f"中国 A 股中盘报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": f"China A-Share Market Midday Report — Lindenwood Management LLC · @{user}",
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
    fname = f"CN_market_noon_{run.report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
