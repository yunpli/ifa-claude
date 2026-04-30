"""Asset evening report — orchestrator + 11 section builders.

Sections per asset.txt §八:
  S1  commentary             今日 Asset 复盘一句话
  S2  commodity_dashboard    商品/期货核心看板
  S3  category_strength      大类商品强弱与异常
  S4  review_table           早报 Asset 假设 Review
  S5  transmission_review    Asset → A 股板块传导复盘（结合申万行业当日表现）
  S6  chain_review           能源/贵金属/有色/黑色/化工/农产品 分链复盘
  S7  news_list              Asset 相关新闻与事件复盘
  S8  watchlist              明日 Asset 观察清单
  S9  hypotheses_list        可沉淀的 Asset 判断资产
  S10 disclaimer             完整免责声明

(Section "持仓/仓单/资金结构" is dropped this iteration because fut_holding /
fut_wsr / fut_settle remain doc-only on the current account; we'll re-introduce
it once those endpoints are sample-proven.)
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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
from .data import (
    AnomalyFlag,
    CategoryStrength,
    CommoditySnapshot,
    SectorBar,
)
from .morning import (
    _build_s2_dashboard as _morning_dashboard,
    _build_s3_strength as _morning_strength,
    _build_s9_disclaimer,
)
from .universe import INDUSTRY_CHAINS

TEMPLATE_VERSION = "asset_evening_v0.1"
REPORT_FAMILY = "asset"
REPORT_TYPE = "evening_long"
SLOT = "evening"
MARKET = "cross_asset"


@dataclass
class AssetEveningCtx:
    engine: Engine
    llm: LLMClient
    tushare: TuShareClient
    run: ReportRun
    snapshots: dict[str, CommoditySnapshot]
    strengths: list[CategoryStrength]
    anomalies: list[AnomalyFlag]
    news_df: object
    sector_bars: list[SectorBar]
    morning_hypotheses: list[dict]
    used_trade_date: dt.date | None
    on_log: Callable[[str], None]


def _load_morning_hypotheses(engine: Engine, *, report_date: dt.date) -> list[dict]:
    sql = text("""
        SELECT j.judgment_id, j.judgment_text, j.target, j.horizon,
               j.validation_method, j.confidence
          FROM report_judgments j
          JOIN report_runs r ON r.report_run_id = j.report_run_id
         WHERE r.report_family = 'asset'
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
            "review_rule": r.validation_method,
            "confidence": r.confidence,
        }
        for r in rows
    ]


# ─── S1: one-paragraph headline ────────────────────────────────────────────

def _build_e1_headline(ctx: AssetEveningCtx) -> dict:
    cat_blob = []
    for s in ctx.strengths:
        cat_blob.append(f"{s.category}: 平均 {s.avg_pct_change:+.2f}% (上涨占比 {s.up_share*100:.0f}%)"
                        if s.avg_pct_change is not None else f"{s.category}: 数据不足")
    sectors_blob = ", ".join(
        f"{b.name} {b.pct_change:+.2f}%" for b in ctx.sector_bars[:10] if b.pct_change is not None
    )
    user = f"""
=== 商品大类强弱 ===
{chr(10).join(cat_blob)}

=== 申万行业当日表现 ===
{sectors_blob or '无可用数据'}

=== 异常品种数 ===
{len(ctx.anomalies)}

=== 任务 ===
{prompts.EVENING_HEADLINE_INSTRUCTIONS}

=== 输出 schema ===
{prompts.EVENING_HEADLINE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1400,
    )
    moid = _persist_model_output(ctx, section_key="asset_evening.s1_headline",
                                 prompt_name="asset_evening.s1_headline",
                                 parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {"label": "晚盘 Asset 综述",
                  "text": "今日商品端综合数据不足以形成强结论，请参考下文各分链复盘。"}
        ctx.run.fallback_used = True
    return {
        "key": "asset_evening.s1_headline",
        "title": "今日 Asset 复盘一句话",
        "order": 1,
        "type": "commentary",
        "content_json": parsed,
        "prompt_name": "asset_evening.s1_headline",
        "model_output_id": moid,
    }


# ─── S2: commodity dashboard (reuse morning) ───────────────────────────────

def _build_e2_dashboard(ctx: AssetEveningCtx) -> dict:
    sec = _morning_dashboard(ctx)  # type: ignore[arg-type]
    sec["key"] = "asset_evening.s2_dashboard"
    sec["title"] = "商品/期货核心看板"
    sec["order"] = 2
    return sec


# ─── S3: category strength (reuse morning) ─────────────────────────────────

def _build_e3_strength(ctx: AssetEveningCtx) -> dict:
    sec = _morning_strength(ctx)  # type: ignore[arg-type]
    sec["key"] = "asset_evening.s3_strength"
    sec["title"] = "大类商品强弱与异常波动"
    sec["order"] = 3
    return sec


# ─── S4: morning hypotheses review ─────────────────────────────────────────

def _build_e4_review(ctx: AssetEveningCtx) -> dict:
    hyps = ctx.morning_hypotheses
    if not hyps:
        return {
            "key": "asset_evening.s4_review",
            "title": "早报 Asset 假设 Review",
            "order": 4,
            "type": "review_table",
            "content_json": {"rows": [],
                              "fallback_text": "今日未找到早报 Asset 假设（早报未生成或未成功）。"},
        }
    cat_blob = []
    for s in ctx.strengths:
        cat_blob.append(f"  {s.category}: 平均 {s.avg_pct_change}, 领涨 {s.leader} {s.leader_pct}, 领跌 {s.laggard} {s.laggard_pct}"
                        if s.avg_pct_change is not None else f"  {s.category}: 数据不足")
    sectors_blob = "; ".join(f"{b.name} {b.pct_change:+.2f}%"
                              for b in ctx.sector_bars if b.pct_change is not None)
    candidates_text = "\n".join(
        f"[{i}] {h['hypothesis']}  · 验证规则: {h.get('review_rule') or '—'}  · 关联: {h.get('related') or '—'}"
        for i, h in enumerate(hyps)
    )
    user = f"""
=== 今日商品大类表现 ===
{chr(10).join(cat_blob)}

=== 申万行业当日表现 ===
{sectors_blob or '无可用数据'}

=== 早报假设 ===
{candidates_text}

=== 任务 ===
{prompts.REVIEW_INSTRUCTIONS}

=== 输出 schema ===
{prompts.REVIEW_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="asset_evening.s4_review",
                                 prompt_name="asset_evening.s4_review",
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
                        UPDATE report_judgments SET review_status = :rs
                         WHERE judgment_id = CAST(:jid AS UUID)
                    """), {"rs": entry.get("review_result") or "pending", "jid": h["judgment_id"]})
                    conn.execute(text("""
                        INSERT INTO report_reviews
                            (judgment_id, review_report_run_id, review_result, evidence_text, lesson)
                        VALUES (CAST(:jid AS UUID), CAST(:rid AS UUID), :rr, :ev, :lz)
                    """), {
                        "jid": h["judgment_id"], "rid": str(ctx.run.report_run_id),
                        "rr": entry.get("review_result") or "not_applicable",
                        "ev": (entry.get("evidence_text") or "")[:500],
                        "lz": (entry.get("lesson") or "")[:500],
                    })
            except Exception:
                pass
    return {
        "key": "asset_evening.s4_review",
        "title": "早报 Asset 假设 Review",
        "order": 4,
        "type": "review_table",
        "content_json": {"rows": rows, "fallback_text": "今日早报暂无可复盘判断。"},
        "prompt_name": "asset_evening.s4_review",
        "model_output_id": moid,
    }


# ─── S5: transmission review ──────────────────────────────────────────────

def _build_e5_transmission(ctx: AssetEveningCtx) -> dict:
    cat_blob = []
    for s in ctx.strengths:
        cat_blob.append({
            "category": s.category,
            "avg_pct_change": s.avg_pct_change,
            "up_share": s.up_share,
            "leader": s.leader,
            "laggard": s.laggard,
        })
    sectors_blob = [{"sector": b.name, "pct_change": b.pct_change}
                    for b in ctx.sector_bars if b.pct_change is not None]
    user = f"""
=== 今日商品大类 ===
{json.dumps(cat_blob, ensure_ascii=False, indent=2)}

=== 申万行业当日表现 ===
{json.dumps(sectors_blob, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.TRANSMISSION_REVIEW_INSTRUCTIONS}

=== 输出 schema ===
{prompts.TRANSMISSION_REVIEW_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="asset_evening.s5_transmission",
                                 prompt_name="asset_evening.s5_transmission",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"rows": []}
    return {
        "key": "asset_evening.s5_transmission",
        "title": "Asset → A 股板块传导复盘",
        "order": 5,
        "type": "transmission_review",
        "content_json": content,
        "prompt_name": "asset_evening.s5_transmission",
        "model_output_id": moid,
    }


# ─── S6: chain review (per-chain detail) ───────────────────────────────────

def _build_e6_chain_review(ctx: AssetEveningCtx) -> dict:
    chains_input = []
    for ch in INDUSTRY_CHAINS:
        upstream_data = []
        for sym in ch.upstream_symbols:
            snap = ctx.snapshots.get(sym)
            if snap and snap.data_status == "ok":
                upstream_data.append(f"{snap.spec.display_name} {snap.pct_change:+.2f}%")
        relevant_sectors = [b for b in ctx.sector_bars
                            if any(s in b.name for s in ch.downstream_a_share)]
        sec_blob = "; ".join(f"{b.name} {b.pct_change:+.2f}%"
                              for b in relevant_sectors if b.pct_change is not None)
        chains_input.append({
            "name": ch.name,
            "upstream": upstream_data,
            "downstream_a_share": ch.downstream_a_share,
            "today_a_share_sectors": sec_blob or "数据不足",
            "narrative": ch.narrative,
        })
    user = f"""
=== 6 条产业链 + 今日实际数据 ===
{json.dumps(chains_input, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.CHAIN_REVIEW_INSTRUCTIONS}

=== 输出 schema ===
{prompts.CHAIN_REVIEW_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2800,
    )
    moid = _persist_model_output(ctx, section_key="asset_evening.s6_chain_review",
                                 prompt_name="asset_evening.s6_chain_review",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"chains": []}
    return {
        "key": "asset_evening.s6_chain_review",
        "title": "能源 / 贵金属 / 有色 / 黑色 / 化工 / 农产品 分链复盘",
        "order": 6,
        "type": "chain_review",
        "content_json": content,
        "prompt_name": "asset_evening.s6_chain_review",
        "model_output_id": moid,
    }


# ─── S7: news list ────────────────────────────────────────────────────────

def _build_e7_news(ctx: AssetEveningCtx) -> dict:
    if ctx.news_df is None or (hasattr(ctx.news_df, "empty") and ctx.news_df.empty):
        return {
            "key": "asset_evening.s7_news",
            "title": "Asset 相关新闻与事件复盘",
            "order": 7,
            "type": "news_list",
            "content_json": {"events": [], "fallback_text": "今日窗口未捕获显著的商品/期货相关新闻。"},
        }
    from ifa.core.report.timezones import BJT
    candidates = []
    for _, row in ctx.news_df.head(20).iterrows():
        dt_v = row.get("datetime")
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
=== 候选新闻（{len(candidates)} 条） ===
{json.dumps(candidates, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.NEWS_INSTRUCTIONS}

=== 输出 schema ===
{prompts.NEWS_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="asset_evening.s7_news",
                                 prompt_name="asset_evening.s7_news",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"events": [], "fallback_text": ""}
    return {
        "key": "asset_evening.s7_news",
        "title": "Asset 相关新闻与事件复盘",
        "order": 7,
        "type": "news_list",
        "content_json": content,
        "prompt_name": "asset_evening.s7_news",
        "model_output_id": moid,
    }


# ─── S8: tomorrow's watchlist ─────────────────────────────────────────────

def _build_e8_watchlist(ctx: AssetEveningCtx, prior: list[dict]) -> dict:
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
    moid = _persist_model_output(ctx, section_key="asset_evening.s8_watchlist",
                                 prompt_name="asset_evening.s8_watchlist",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"items": []}
    return {
        "key": "asset_evening.s8_watchlist",
        "title": "明日 Asset 观察清单",
        "order": 8,
        "type": "watchlist",
        "content_json": content,
        "prompt_name": "asset_evening.s8_watchlist",
        "model_output_id": moid,
    }


# ─── S9: reviewable hypothesis assets ──────────────────────────────────────

def _build_e9_reviewable(ctx: AssetEveningCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5000]}

=== 任务 ===
基于今日 Asset 复盘 + A 股相关板块表现，输出 3-5 条"可在明日或多日内验证"的 Asset 判断（如商品链续强、传导失效、资金驱动延续等），作为下一日 Asset 早报的待验证候选。
{prompts.HYPOTHESES_INSTRUCTIONS}

=== 输出 schema ===
{prompts.HYPOTHESES_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="asset_evening.s9_reviewable",
                                 prompt_name="asset_evening.s9_reviewable",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"hypotheses": []}
    for h in content.get("hypotheses", []) or []:
        try:
            insert_judgment(
                ctx.engine, report_run_id=ctx.run.report_run_id,
                section_key="asset_evening.s9_reviewable",
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
        "key": "asset_evening.s9_reviewable",
        "title": "可沉淀的 Asset 判断资产（明日复盘）",
        "order": 9,
        "type": "hypotheses_list",
        "content_json": content,
        "prompt_name": "asset_evening.s9_reviewable",
        "model_output_id": moid,
    }


# ─── S10: disclaimer ──────────────────────────────────────────────────────

def _build_e10_disclaimer() -> dict:
    return {
        "key": "asset_evening.s10_disclaimer",
        "title": "免责声明",
        "order": 10,
        "type": "disclaimer",
        "content_json": {
            "paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
            "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH,
        },
    }


# ─── Orchestrator ─────────────────────────────────────────────────────────

def run_asset_evening(
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
    on_log(f"[run {str(run.report_run_id)[:8]}] starting Asset evening report for {report_date}")

    try:
        on_log("resolving main contracts (today)…")
        snapshots, used_date = data.resolve_main_contracts(tushare, on_date=report_date)
        n_ok = sum(1 for s in snapshots.values() if s.data_status == "ok")
        on_log(f"  main contracts resolved: {n_ok}/{len(snapshots)} on {used_date}")
        on_log("attaching histories…")
        data.attach_histories(tushare, snapshots, end_date=report_date, days=10)
        strengths = data.category_strengths(snapshots)
        anomalies = data.detect_anomalies(snapshots)
        on_log("filtering commodity news (last 24h)…")
        bjt_cutoff = to_bjt(data_cutoff_at)
        news_df = data.fetch_commodity_news(tushare, end_bjt=bjt_cutoff, lookback_hours=24, max_keep=30)
        on_log("fetching SW industry index closes…")
        sector_bars = data.fetch_a_share_sectors(tushare, on_date=report_date)
        on_log("loading this morning's Asset hypotheses…")
        morning_hypotheses = _load_morning_hypotheses(engine, report_date=report_date)

        ctx = AssetEveningCtx(
            engine=engine, llm=llm, tushare=tushare, run=run,
            snapshots=snapshots, strengths=strengths, anomalies=anomalies,
            news_df=news_df, sector_bars=sector_bars,
            morning_hypotheses=morning_hypotheses,
            used_trade_date=used_date, on_log=on_log,
        )

        sections: list[dict] = []
        for label, builder in [
            ("E1 headline",     lambda: _build_e1_headline(ctx)),
            ("E2 dashboard",    lambda: _build_e2_dashboard(ctx)),
            ("E3 strength",     lambda: _build_e3_strength(ctx)),
            ("E4 review",       lambda: _build_e4_review(ctx)),
            ("E5 transmission", lambda: _build_e5_transmission(ctx)),
            ("E6 chain review", lambda: _build_e6_chain_review(ctx)),
            ("E7 news",         lambda: _build_e7_news(ctx)),
            ("E8 watchlist",    lambda: _build_e8_watchlist(ctx, sections)),
            ("E9 reviewable",   lambda: _build_e9_reviewable(ctx, sections)),
            ("E10 disclaimer",  _build_e10_disclaimer),
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
        "title": f"中国 Asset 晚盘报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": "China Asset Post-Close Briefing — Lindenwood Management LLC",
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
    fname = f"CN_asset_evening_{run.report_date.strftime('%Y-%m-%d')}_{bjt_now.strftime('%H-%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
