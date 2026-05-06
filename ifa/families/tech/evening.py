"""Tech evening report — orchestrator + 12 section builders.

Sections per tech.txt §九:
  S1  commentary           今日 Tech 复盘一句话
  S2  layer_map            AI Five-Layer Cake 日内表现复盘
  S3  category_strength    科技板块强弱与热点复盘
  S4  review_table         早报 Tech 假设 Review
  S5  leader_table         科技龙头与高标复盘
  S6  candidate_pool       潜在蓄势池表现复盘 (review-flavored)
  S7  focus_deep           用户重点关注科技股复盘
  S8  focus_brief          用户普通关注科技股复盘
  S9  news_list            全球科技新闻 / 产业事件复盘
  S10 watchlist            明日 Tech 观察清单
  S11 hypotheses_list      可沉淀的 Tech 判断资产
  S12 disclaimer           完整免责声明
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
    insert_report_run,
    insert_section,
)
from ifa.core.tushare import TuShareClient
from ifa.families.macro.morning import _persist_model_output, _safe_chat_json

from . import data, prompts
from .focus import get_focus_for
from .morning import (
    TechCtx,
    _build_s2_layer_map,
    _build_s3_board_recap,
    _build_s5_news,
    _build_s7_leaders,
    _build_s8_candidates,
    _build_s9_focus_deep,
    _build_s10_focus_brief,
    _build_s12_disclaimer,
    _direction,
    _fmt_count,
    _fmt_pct,
)

TEMPLATE_VERSION = "tech_evening_v2.1.0"
REPORT_FAMILY = "tech"
REPORT_TYPE = "evening_long"
SLOT = "evening"
MARKET = "china_a"


def _load_morning_hypotheses(engine: Engine, *, report_date: dt.date) -> list[dict]:
    sql = text("""
        SELECT j.judgment_id, j.judgment_text, j.target, j.horizon,
               j.validation_method, j.confidence
          FROM report_judgments j
          JOIN report_runs r ON r.report_run_id = j.report_run_id
         WHERE r.report_family = 'tech'
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


# ─── S1: evening headline commentary ───────────────────────────────────────

def _build_e1_headline(ctx: TechCtx, morning_hyps: list[dict]) -> dict:
    sw_blob = "; ".join(f"{b.name} {b.pct_change:+.2f}%"
                         for b in ctx.sw_sectors if b.pct_change is not None)
    layer_summary = []
    from .universe import AI_LAYERS
    for L in AI_LAYERS:
        boards = ctx.boards_by_layer.get(L.layer_id, [])
        with_data = [b for b in boards if b.pct_change is not None]
        if with_data:
            avg = sum(b.pct_change for b in with_data) / len(with_data)
            layer_summary.append(f"  {L.layer_id}: 均 {avg:+.2f}%, 个数 {len(with_data)}/{len(boards)}")
    morn_block = (
        "\n".join(f"[{i+1}] {h.get('hypothesis','')}" for i, h in enumerate(morning_hyps[:6]))
        if morning_hyps else "(无 — 早报未生成或假设为空)"
    )
    user = f"""
=== 今日 5 层板块 ===
{chr(10).join(layer_summary)}

=== 申万 TMT 行业当日表现 ===
{sw_blob}

=== 涨停 tech 个股数 ===
{len(ctx.limit_up)}

=== 早报 Tech 假设（{len(morning_hyps)} 条；E4 单独逐条复盘）===
{morn_block}

注意：本节是收盘 headline，需基于今日实际收盘对早报方向做总结，不要写"早报假设未提供"——上面已给。

=== 任务 ===
{prompts.EVENING_HEADLINE_INSTRUCTIONS}

=== 输出 schema ===
{prompts.EVENING_HEADLINE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1400,
    )
    moid = _persist_model_output(ctx, section_key="tech_evening.s1_headline",
                                 prompt_name="tech_evening.s1_headline",
                                 parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {"label": "晚盘 Tech 综述",
                  "text": "今日科技数据未能形成明确合力，请参考下文各分层复盘。"}
        ctx.run.fallback_used = True
    return {
        "key": "tech_evening.s1_headline", "title": "今日 Tech 复盘一句话",
        "order": 1, "type": "commentary", "content_json": parsed,
        "prompt_name": "tech_evening.s1_headline", "model_output_id": moid,
    }


# ─── reuse morning helpers (with re-tagging) ───────────────────────────────

def _retag(sec: dict, *, key: str, title: str, order: int) -> dict:
    sec = dict(sec)
    sec["key"] = key
    sec["title"] = title
    sec["order"] = order
    sec["prompt_name"] = key
    return sec


def _build_e2_layer_map(ctx: TechCtx) -> dict:
    sec = _build_s2_layer_map(ctx)
    return _retag(sec, key="tech_evening.s2_layer_map",
                  title="AI Five-Layer Cake 日内表现复盘", order=2)


def _build_e3_board_recap(ctx: TechCtx) -> dict:
    sec = _build_s3_board_recap(ctx)
    return _retag(sec, key="tech_evening.s3_board_recap",
                  title="科技板块强弱与热点复盘", order=3)


# ─── S4: morning hypothesis review ────────────────────────────────────────

def _build_e4_review(ctx: TechCtx, morning_hypotheses: list[dict]) -> dict:
    if not morning_hypotheses:
        return {"key": "tech_evening.s4_review", "title": "早报 Tech 假设 Review",
                "order": 4, "type": "review_table",
                "content_json": {"rows": [],
                                  "fallback_text": "今日未找到当天 Tech 早报的假设；可能早报未生成或未成功。"}}

    # Build per-layer board snapshots (the morning hypotheses reference layers/themes,
    # so the LLM needs board-level pct_change to validate, not just SW industries).
    from .universe import AI_LAYERS
    layer_blob: list[str] = []
    for L in AI_LAYERS:
        boards = ctx.boards_by_layer.get(L.layer_id, [])
        with_data = [b for b in boards if b.pct_change is not None]
        if with_data:
            avg = sum(b.pct_change for b in with_data) / len(with_data)
            top = max(with_data, key=lambda b: b.pct_change)
            bot = min(with_data, key=lambda b: b.pct_change)
            board_pcts = "; ".join(f"{b.name} {b.pct_change:+.2f}%" for b in with_data)
            layer_blob.append(
                f"  {L.layer_id} ({L.layer_name}): 均 {avg:+.2f}%; "
                f"领涨 {top.name} {top.pct_change:+.2f}%, 领跌 {bot.name} {bot.pct_change:+.2f}%; "
                f"全部: {board_pcts}"
            )
        else:
            layer_blob.append(f"  {L.layer_id}: 板块数据缺失")

    # Top tech movers (ts_code + pct + amount)
    top_movers_text = "; ".join(
        f"{m.name or m.ts_code} {m.pct_change:+.2f}%"
        for m in ctx.top_movers[:12] if m.pct_change is not None
    ) or "无数据"

    sw_blob = "; ".join(f"{b.name} {b.pct_change:+.2f}%"
                         for b in ctx.sw_sectors if b.pct_change is not None) or "无可用数据"
    candidates_text = "\n".join(
        f"[{i}] {h['hypothesis']}  · 验证规则: {h.get('review_rule') or '—'}  · 关联: {h.get('related') or '—'}"
        for i, h in enumerate(morning_hypotheses)
    )
    user = f"""
=== 今日 5 层板块 (SW L2 行业，按层聚合) ===
{chr(10).join(layer_blob)}

=== 涨停 tech 个股数 ===
{len(ctx.limit_up)}（个股: {", ".join(m.name or m.ts_code for m in ctx.limit_up[:10])}）

=== 涨幅前列 tech 个股 ===
{top_movers_text}

=== 申万 TMT 行业当日表现 ===
{sw_blob}

=== 早报 Tech 假设 ===
{candidates_text}

=== 任务 ===
{prompts.REVIEW_INSTRUCTIONS}

=== 输出 schema ===
{prompts.REVIEW_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="tech_evening.s4_review",
                                 prompt_name="tech_evening.s4_review",
                                 parsed=parsed, resp=resp, status=status)
    rows = []
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(morning_hypotheses):
                continue
            h = morning_hypotheses[idx]
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
        "key": "tech_evening.s4_review", "title": "早报 Tech 假设 Review",
        "order": 4, "type": "review_table",
        "content_json": {"rows": rows, "fallback_text": ""},
        "prompt_name": "tech_evening.s4_review", "model_output_id": moid,
    }


def _build_e5_leaders(ctx: TechCtx) -> dict:
    sec = _build_s7_leaders(ctx)
    return _retag(sec, key="tech_evening.s5_leaders",
                  title="科技龙头与高标复盘", order=5)


def _build_e6_candidates(ctx: TechCtx) -> dict:
    sec = _build_s8_candidates(ctx)
    return _retag(sec, key="tech_evening.s6_candidates",
                  title="潜在蓄势待发池表现复盘", order=6)


def _build_e7_focus_deep(ctx: TechCtx) -> dict:
    sec = _build_s9_focus_deep(ctx)
    return _retag(sec, key="tech_evening.s7_focus_deep",
                  title=f"用户重点关注科技股复盘 · @{ctx.user}", order=7)


def _build_e8_focus_brief(ctx: TechCtx) -> dict:
    sec = _build_s10_focus_brief(ctx)
    return _retag(sec, key="tech_evening.s8_focus_brief",
                  title=f"用户普通关注科技股复盘 · @{ctx.user}", order=8)


def _build_e9_news(ctx: TechCtx) -> dict:
    sec = _build_s5_news(ctx)
    return _retag(sec, key="tech_evening.s9_news",
                  title="全球科技新闻 / 产业事件复盘", order=9)


# ─── S10: tomorrow watchlist ──────────────────────────────────────────────

def _build_e10_watchlist(ctx: TechCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
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
    moid = _persist_model_output(ctx, section_key="tech_evening.s10_watchlist",
                                 prompt_name="tech_evening.s10_watchlist",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"items": []}
    return {
        "key": "tech_evening.s10_watchlist", "title": "明日 Tech 观察清单",
        "order": 10, "type": "watchlist", "content_json": content,
        "prompt_name": "tech_evening.s10_watchlist", "model_output_id": moid,
    }


# ─── S11: reviewable judgments ────────────────────────────────────────────

def _build_e11_reviewable(ctx: TechCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5000]}

=== 任务 ===
基于今日 Tech 复盘 + A 股板块 + 美股 + 政策上下文，输出 3-5 条"可在明日或多日内验证"的 Tech 判断，作为下一交易日 Tech 早报的待验证候选。
{prompts.HYPOTHESES_INSTRUCTIONS}

=== 输出 schema ===
{prompts.HYPOTHESES_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="tech_evening.s11_reviewable",
                                 prompt_name="tech_evening.s11_reviewable",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"hypotheses": []}
    for h in content.get("hypotheses", []) or []:
        try:
            insert_judgment(
                ctx.engine, report_run_id=ctx.run.report_run_id,
                section_key="tech_evening.s11_reviewable", judgment_type="hypothesis",
                judgment_text=h.get("hypothesis", ""),
                target=", ".join(h.get("related_markets_or_sectors") or [])[:300],
                horizon=h.get("observation_window") or "tomorrow",
                confidence=(h.get("confidence") or "medium").lower(),
                validation_method=h.get("review_rule"),
            )
        except Exception:
            pass
    return {
        "key": "tech_evening.s11_reviewable",
        "title": "可沉淀的 Tech 判断资产（明日复盘）",
        "order": 11, "type": "hypotheses_list", "content_json": content,
        "prompt_name": "tech_evening.s11_reviewable", "model_output_id": moid,
    }


# ─── S12: disclaimer ──────────────────────────────────────────────────────

def _build_e12_disclaimer() -> dict:
    return {
        "key": "tech_evening.s12_disclaimer", "title": "免责声明",
        "order": 12, "type": "disclaimer",
        "content_json": {"paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
                         "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH},
    }


# ─── Orchestrator ─────────────────────────────────────────────────────────

def run_tech_evening(
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
    on_log(f"[run {str(run.report_run_id)[:8]}] starting Tech evening report for {report_date} user={user}")

    from ifa.core.report.freshness import preflight_freshness_check
    for line in preflight_freshness_check(engine, family="tech", expected_date=report_date):
        on_log(f"[freshness] ⚠ {line}")

    try:
        on_log("fetching SW L2 sector performance for tech 5 layers (today)…")
        boards_by_layer = data.fetch_board_performance(tushare, on_date=report_date, history_days=10, slot="evening", engine=engine)
        on_log("resolving tech sector members (SW PIT)…")
        tech_members = data.resolve_tech_members(tushare, engine, trade_date=report_date)
        on_log("fetching limit-up tech stocks (today)…")
        limit_up = data.fetch_limit_up_tech(tushare, on_date=report_date, tech_members=tech_members)
        on_log(f"  {len(limit_up)} limit-up tech stocks today")
        on_log("fetching top movers in tech (today)…")
        top_movers = data.fetch_top_movers_in_tech(tushare, on_date=report_date,
                                                    tech_members=tech_members, top_n=40)
        all_tech_codes = list({m.ts_code for m in limit_up + top_movers})
        on_log("fetching money flow…")
        mf_by_code = data.fetch_money_flow_top(tushare, on_date=report_date, ts_codes=all_tech_codes)
        on_log("fetching US tech (latest)…")
        us_stocks = data.fetch_us_tech_overnight(tushare, ref_date=report_date)
        on_log("fetching tech news (last 24h)…")
        news_df = data.fetch_tech_news(tushare, end_bjt=to_bjt(data_cutoff_at), lookback_hours=24)
        on_log("fetching SW TMT sectors…")
        sw_sectors = data.fetch_tech_sw_sectors(tushare, on_date=report_date, slot="evening", engine=engine)
        on_log(f"loading user '{user}' focus list and enriching…")
        important_focus_specs, regular_focus_specs = get_focus_for(user)
        important_focus, regular_focus = data.enrich_focus(
            tushare, on_date=report_date,
            important=important_focus_specs, regular=regular_focus_specs,
        )
        on_log("loading this morning's Tech hypotheses…")
        morning_hyps = _load_morning_hypotheses(engine, report_date=report_date)

        ctx = TechCtx(
            engine=engine, llm=llm, tushare=tushare, run=run, user=user,
            boards_by_layer=boards_by_layer, tech_members=tech_members,
            limit_up=limit_up, top_movers=top_movers, moneyflow_by_code=mf_by_code,
            us_stocks=us_stocks, news_df=news_df, sw_sectors=sw_sectors,
            important_focus=important_focus, regular_focus=regular_focus,
            on_log=on_log,
        )

        sections: list[dict] = []
        for label, builder in [
            ("E1 headline",      lambda: _build_e1_headline(ctx, morning_hyps)),
            ("E2 layer map",     lambda: _build_e2_layer_map(ctx)),
            ("E3 board recap",   lambda: _build_e3_board_recap(ctx)),
            ("E4 review",        lambda: _build_e4_review(ctx, morning_hyps)),
            ("E5 leaders",       lambda: _build_e5_leaders(ctx)),
            ("E6 candidates",    lambda: _build_e6_candidates(ctx)),
            ("E7 focus deep",    lambda: _build_e7_focus_deep(ctx)),
            ("E8 focus brief",   lambda: _build_e8_focus_brief(ctx)),
            ("E9 news",          lambda: _build_e9_news(ctx)),
            ("E10 watchlist",    lambda: _build_e10_watchlist(ctx, sections)),
            ("E11 reviewable",   lambda: _build_e11_reviewable(ctx, sections)),
            ("E12 disclaimer",   _build_e12_disclaimer),
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
        all_boards = [b for boards in ctx.boards_by_layer.values() for b in boards]
        staleness = compute_staleness_warning(
            report_date=run.report_date,
            dated_objects=[*all_boards, *ctx.sw_sectors],
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
        "title": f"中国科技 / AI 晚盘报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": f"China Tech / AI Equity Post-Close Briefing — Lindenwood Management LLC · @{user}",
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
    fname = f"CN_tech_evening_{user}_{run.report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
