"""Macro morning report — orchestrator + 12 section builders.

The orchestrator:
  1. Inserts a `report_runs` row in 'running' state.
  2. Fetches all macro inputs once (panel, liquidity, cross-asset, memory tables).
  3. Builds sections one by one. Each LLM-driven section persists its
     `report_model_outputs` row, then the section content goes into
     `report_sections`.
  4. Renders HTML, saves to disk, finalizes the run.

Sections per ifa-macro-v1 §3-4:
  S0  [implicit in template]     Header / fixed disclaimer
  S1  tone_card                  今日宏观底色
  S2  data_panel                 核心宏观数据面板
  S3  liquidity_grid             盘前利率、流动性与汇率参考
  S4  news_list                  关键新闻、政策与事件摘要
  S5  policy_matrix              政策与大政方针观察
  S6  cross_asset_grid           隔夜外部、港股、跨资产
  S7  mapping_table              宏观→A股板块映射
  S8  risk_list                  今日宏观风险清单
  S9  hypotheses_list            今日需验证宏观假设
  S10 indicator_capture_table    新闻源抽取宏观数据捕获
  S11 disclaimer                 完整免责声明
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from ifa.config import RunMode, get_settings
from ifa.core.db import get_engine
from ifa.core.llm import LLMClient
from ifa.core.render import HtmlRenderer, sparkline_svg
from ifa.core.report import (
    DISCLAIMER_PARAGRAPHS_EN,
    DISCLAIMER_PARAGRAPHS_ZH,
    BJT,
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
from ifa.families.macro.data import (
    AssetSnapshot,
    LiquiditySnapshot,
    PolicyEventRow,
    TextDerivedRow,
    TimeSeries,
)

log = logging.getLogger(__name__)

TEMPLATE_VERSION = "macro_morning_v2.1.0"
REPORT_FAMILY = "macro"
REPORT_TYPE = "morning_long"
SLOT = "morning"
MARKET = "china_a"


@dataclass
class RuntimeCtx:
    engine: Engine
    llm: LLMClient
    tushare: TuShareClient
    run: ReportRun
    panel: dict[str, TimeSeries]
    liquidity: LiquiditySnapshot
    cross_asset: list[AssetSnapshot]
    text_derived: list[TextDerivedRow]
    policy_events: list[PolicyEventRow]
    on_log: Callable[[str], None]


# ─── Helpers ────────────────────────────────────────────────────────────────

def _safe_chat_json(llm: LLMClient, *, system: str, user: str,
                     max_tokens: int = 2400,
                     required_fields: list[str] | None = None,
                     ) -> tuple[dict | list | None, Any, str]:
    """Call LLM, parse JSON, optionally validate required fields. Returns
    (parsed_or_none, raw_response, status).

    If required_fields is provided, every listed key must be present and
    non-empty in the parsed dict — else we send ONE retry telling the LLM
    exactly which fields it skipped. List-valued fields ({"top3":[…]}) must
    have len ≥ 3 to count as populated; this prevents the LLM from returning
    an empty list and silently failing the schema.
    """
    import re
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        resp = llm.chat(messages=messages, max_tokens=max_tokens, temperature=0.2)
    except Exception as exc:
        return None, None, f"error: {type(exc).__name__}: {exc}"

    def _strip_fence(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            m = re.search(r"```(?:json|JSON)?\s*(.*?)```", text, re.DOTALL)
            if m:
                text = m.group(1).strip()
        return text

    def _try_parse(text: str):
        try:
            return json.loads(text), None
        except json.JSONDecodeError as e:
            return None, e

    parsed, _err = _try_parse(_strip_fence(resp.content))
    status = "parsed" if parsed is not None else "parse_failed"

    # JSON-parse retry path (legacy)
    if parsed is None:
        retry = llm.chat(
            messages=messages + [
                {"role": "assistant", "content": resp.content[:1000]},
                {"role": "user", "content": "你刚才返回的不是有效 JSON。请只返回 JSON 对象本身，不要任何前缀、后缀、markdown 围栏或解释。"},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        parsed, _ = _try_parse(_strip_fence(retry.content))
        if parsed is None:
            return None, retry, "parse_failed"
        resp = retry
        status = "fallback_used"

    # Schema-completeness retry — up to 3 attempts with backoff before
    # giving up and letting downstream fallback synthesize.
    if required_fields and isinstance(parsed, dict):
        import time

        def _missing(p):
            out = []
            for f in required_fields:
                v = p.get(f) if isinstance(p, dict) else None
                if v is None or (isinstance(v, (list, str)) and len(v) == 0):
                    out.append(f)
                elif f == "top3" and isinstance(v, list) and len(v) < 3:
                    out.append(f)
            return out

        max_schema_retries = 3
        backoff_secs = [2, 4, 8]
        attempt = 0
        while attempt < max_schema_retries:
            miss = _missing(parsed)
            if not miss:
                break
            attempt += 1
            sleep_s = backoff_secs[min(attempt - 1, len(backoff_secs) - 1)]
            time.sleep(sleep_s)
            try:
                retry = llm.chat(
                    messages=messages + [
                        {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)[:1500]},
                        {"role": "user", "content": (
                            f"[第 {attempt}/{max_schema_retries} 次重试] 你之前漏了必填字段："
                            + "、".join(miss)
                            + "。请重新输出**完整**的 JSON 对象（保留你已有的字段，补上漏的）。"
                            + ("`top3` 必须是恰好 3 条字符串数组，每条 ≤22 个汉字，写今日盘中要做的具体动作（盯哪个龙头/板块/位置）。不能空，也不能少于 3 条。"
                               if "top3" in miss else "")
                            + " 只返回 JSON，不要解释、不要 markdown 围栏。"
                        )},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
            except Exception:
                continue
            retried, _ = _try_parse(_strip_fence(retry.content))
            if isinstance(retried, dict):
                merged = dict(parsed)
                for k, v in retried.items():
                    if v not in (None, "", []):
                        merged[k] = v
                parsed = merged
                resp = retry
                still_miss = _missing(parsed)
                if not still_miss:
                    return parsed, resp, f"schema_retry_ok_attempt{attempt}"
        # Exited loop with miss still non-empty
        if _missing(parsed):
            status = f"schema_retry_partial_after{attempt}"
        else:
            status = f"schema_retry_ok_attempt{attempt}"

    return parsed, resp, status


def _persist_model_output(ctx: RuntimeCtx, *, section_key: str, prompt_name: str,
                          parsed: Any, resp: Any, status: str) -> uuid.UUID | None:
    if resp is None:
        return None
    # DB CHECK constraint allows only ['parsed','parse_failed','fallback_used','error'].
    # New schema-retry statuses get mapped to those; the detailed retry trace stays
    # in logs / response.endpoint / token counts.
    status_db = status
    if status.startswith("schema_retry_ok"):
        status_db = "parsed"
    elif status.startswith("schema_retry_partial"):
        status_db = "fallback_used"
    elif status.startswith("error"):
        status_db = "error"
    elif status_db not in ("parsed", "parse_failed", "fallback_used", "error"):
        # Defensive: any other unexpected status → fallback_used
        status_db = "fallback_used"
    return insert_model_output(
        ctx.engine,
        report_run_id=ctx.run.report_run_id,
        section_key=section_key,
        prompt_name=prompt_name,
        prompt_version=prompts.PROMPT_BUNDLE_VERSION,
        model_name=resp.model,
        endpoint=resp.endpoint,
        parsed_json=parsed if isinstance(parsed, (dict, list)) else None,
        status=status_db,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        latency_seconds=resp.latency_seconds,
    )


def _fmt_pct(v: float | None, *, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:+.{decimals}f}%"


def _fmt_value(v: float | None, *, decimals: int = 2, unit: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:,.{decimals}f}"


def _direction(v: float | None, *, threshold: float = 0.0) -> str:
    if v is None:
        return "flat"
    if v > threshold:
        return "up"
    if v < -threshold:
        return "down"
    return "flat"


def _series_summary(ts: TimeSeries) -> str:
    """A short text summary of a TimeSeries the LLM can reason over."""
    if not ts.values:
        return f"{ts.name}: 无数据"
    head = ", ".join(f"{p}={v}" for p, v in zip(ts.periods[-6:], ts.values[-6:]) if v is not None)
    yoy = ""
    if ts.latest_yoy is not None:
        yoy = f"; 最新同比 {ts.latest_yoy:+.2f}%"
    mom = ""
    if ts.latest_mom is not None:
        mom = f"; 最新环比 {ts.latest_mom:+.2f}%"
    return f"{ts.name}（{ts.unit}），最近6期: {head}{yoy}{mom}"


# ─── S1: tone card ──────────────────────────────────────────────────────────

def _build_s1_tone(ctx: RuntimeCtx) -> dict:
    panel_summary = "\n".join(
        f"  - {_series_summary(ctx.panel[k])}"
        for k in ["GDP", "CPI", "PPI", "PMI", "M2", "M1", "社融增量", "社融存量"]
        if k in ctx.panel and ctx.panel[k].values
    )
    liq = ctx.liquidity
    liq_summary = (
        f"SHIBOR 隔夜 {liq.shibor_overnight}; LPR 1Y {liq.lpr_1y} / 5Y {liq.lpr_5y}; "
        f"USD/CNH {liq.usdcnh_close} (Δ {liq.usdcnh_change}); "
        f"上一交易日北向 {liq.north_money} 亿元; 两融余额 {liq.margin_total} 万亿元 (Δ {liq.margin_change})"
    )
    cross = "; ".join(
        f"{a.name} {a.latest} ({a.pct_change:+.2f}% if not None)"
        if a.latest else f"{a.name} 数据缺失"
        for a in ctx.cross_asset[:6]
    )
    policies = "\n".join(
        f"  - [{p.policy_dimension}/{p.policy_signal}] {p.event_title} "
        f"({p.source_name or ''}, {fmt_bjt(p.publish_time, '%m-%d %H:%M')})"
        for p in ctx.policy_events[:10]
    ) or "  (无)"

    user = f"""
=== 今日报告时点 ===
报告日期 (北京时间): {ctx.run.report_date}
数据截止: {fmt_bjt(ctx.run.data_cutoff_at)} 北京时间
slot: {SLOT}

=== 宏观面板（最近若干期） ===
{panel_summary}

=== 利率/汇率/资金面快照 ===
{liq_summary}

=== 隔夜/上一交易日跨资产 ===
{cross}

=== 活跃政策事件（最近 14 天） ===
{policies}

=== 任务 ===
{prompts.TONE_INSTRUCTIONS}

=== 输出 schema (返回纯 JSON 不带任何围栏或解释) ===
{prompts.TONE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
        required_fields=["headline", "top3"],
    )
    moid = _persist_model_output(ctx, section_key="macro_morning.s1_tone",
                                 prompt_name="macro_morning.s1_tone",
                                 parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {
            "tone": "背景变量",
            "tone_short": "背景变量",
            "headline": "宏观今日为背景变量；待今日资金面与板块行为给出新增信号。",
            "summary": "本报告窗口内未捕获新的强宏观驱动；建议以政策记忆与上一交易日资金面作为基准。",
            "bullets": [],
        }
        ctx.run.fallback_used = True

    # Persist the tone judgment
    insert_judgment(
        ctx.engine,
        report_run_id=ctx.run.report_run_id,
        section_key="macro_morning.s1_tone",
        judgment_type="macro_tone",
        judgment_text=parsed.get("headline", ""),
        target="A股风险偏好",
        horizon="today_full_day",
        confidence="medium",
        validation_method="evening review of intraday market behavior vs. tone",
    )

    return {
        "key": "macro_morning.s1_tone",
        "title": "今日宏观底色",
        "order": 1,
        "type": "tone_card",
        "content_json": parsed,
        "prompt_name": "macro_morning.s1_tone",
        "model_output_id": moid,
    }


# ─── S2: core panel ─────────────────────────────────────────────────────────

_PANEL_ROWS = [
    ("GDP",    "GDP", "%",     0),
    ("CPI",    "CPI 同比", "%", 2),
    ("PPI",    "PPI 同比", "%", 2),
    ("PMI",    "PMI", "",     2),
    ("M2",     "M2 余额", "亿元", 0),
    ("M1",     "M1 余额", "亿元", 0),
    ("社融增量","社融月度增量", "亿元", 0),
    ("社融存量","社融存量", "万亿元", 2),
]


def _build_s2_panel(ctx: RuntimeCtx) -> dict:
    # Build base rows from structured data
    base_rows: list[dict[str, Any]] = []
    candidates_for_llm: list[dict[str, Any]] = []
    for key, label, unit, decimals in _PANEL_ROWS:
        ts = ctx.panel.get(key)
        if ts is None or not ts.values:
            base_rows.append({
                "indicator": label, "period": "—", "value": "—", "unit": "",
                "yoy_display": "—", "mom_display": "—",
                "yoy_dir": "flat", "mom_dir": "flat",
                "spark_svg": "", "commentary": "数据未取到",
                "timing": "数据缺失",
            })
            candidates_for_llm.append({"name": label, "summary": "数据缺失"})
            continue

        # Pick the value to display: for M2/社融 we show YoY (rate-of-change), value column shows level.
        latest_value = ts.latest_value
        # For M2/M1 raw level is in 亿元; convert to 万亿 for friendlier display
        if key in ("M2", "M1") and latest_value is not None:
            value_str = f"{latest_value/1e4:,.2f} 万亿"
        elif key == "社融增量" and latest_value is not None:
            value_str = f"{latest_value:,.0f} 亿元"
        elif key == "社融存量" and latest_value is not None:
            value_str = f"{latest_value:,.2f} 万亿元"
        elif key in ("CPI", "PPI", "GDP") and latest_value is not None:
            value_str = f"{latest_value:+.2f}%"
        elif key == "PMI" and latest_value is not None:
            value_str = f"{latest_value:.1f}"
        else:
            value_str = "—" if latest_value is None else f"{latest_value:,.{decimals}f}"

        spark_values = ts.yoy_values if key in ("M2", "M1") else ts.values
        spark_svg = sparkline_svg(spark_values, width=130, height=28)

        base_rows.append({
            "indicator": label,
            "period": ts.latest_period or "—",
            "value": value_str,
            "unit": "",
            "yoy_display": _fmt_pct(ts.latest_yoy) if ts.latest_yoy is not None else "—",
            "mom_display": _fmt_pct(ts.latest_mom) if ts.latest_mom is not None else "—",
            "yoy_dir": _direction(ts.latest_yoy),
            "mom_dir": _direction(ts.latest_mom),
            "spark_svg": spark_svg,
            "commentary": "—",
            "timing": "最近一期已披露",
        })
        candidates_for_llm.append({
            "name": label,
            "summary": _series_summary(ts),
        })

    # Batch LLM commentary
    bulk_text = "\n".join(
        f"[{i}] {c['name']} — {c['summary']}" for i, c in enumerate(candidates_for_llm)
    )
    user = f"""
=== 任务 ===
{prompts.PANEL_INSTRUCTIONS}

输入指标数（按 candidate_index 0..{len(candidates_for_llm)-1}）：
{bulk_text}

=== 输出 schema ===
{prompts.PANEL_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="macro_morning.s2_panel",
                                 prompt_name="macro_morning.s2_panel",
                                 parsed=parsed, resp=resp, status=status)

    overall = ""
    if isinstance(parsed, dict):
        results = parsed.get("results") or []
        for entry in results:
            idx = entry.get("candidate_index")
            if isinstance(idx, int) and 0 <= idx < len(base_rows):
                base_rows[idx]["commentary"] = (entry.get("commentary") or "").strip() or base_rows[idx]["commentary"]
        overall = parsed.get("overall_commentary") or ""

    return {
        "key": "macro_morning.s2_panel",
        "title": "核心宏观数据面板",
        "order": 2,
        "type": "data_panel",
        "content_json": {
            "intro": "GDP / CPI / PPI / PMI / 货币（M0–M2） / 社融。所有数据来自 TuShare 结构化接口；右侧 sparkline 反映最近 12 期序列。",
            "rows": base_rows,
            "commentary": overall,
        },
        "prompt_name": "macro_morning.s2_panel",
        "model_output_id": moid,
    }


# ─── S3: liquidity grid ────────────────────────────────────────────────────

def _build_s3_liquidity(ctx: RuntimeCtx) -> dict | None:
    liq = ctx.liquidity
    cells: list[dict[str, Any]] = []

    if liq.shibor_overnight is not None:
        cells.append({
            "label": "SHIBOR · 隔夜",
            "value": f"{liq.shibor_overnight:.3f}",
            "unit": "%",
            "period": fmt_bjt(dt.datetime.combine(liq.shibor_date or ctx.run.report_date, dt.time(), tzinfo=BJT), "%Y-%m-%d") if liq.shibor_date else "—",
            "note": f"1W {liq.shibor_1w:.3f}% · 3M {liq.shibor_3m:.3f}%" if liq.shibor_1w and liq.shibor_3m else "",
        })
    if liq.lpr_1y is not None:
        cells.append({
            "label": "LPR · 1Y / 5Y",
            "value": f"{liq.lpr_1y:.2f}% / {liq.lpr_5y:.2f}%" if liq.lpr_5y else f"{liq.lpr_1y:.2f}%",
            "unit": "",
            "period": liq.lpr_date.strftime("%Y-%m-%d") if liq.lpr_date else "—",
            "note": "信贷与地产链定价基准",
        })
    if liq.usdcnh_close is not None:
        delta_dir = _direction(liq.usdcnh_change)
        cells.append({
            "label": "USD / CNH",
            "value": f"{liq.usdcnh_close:.4f}",
            "unit": "",
            "delta": f"{liq.usdcnh_change:+.4f}" if liq.usdcnh_change else "持平",
            "delta_dir": delta_dir,
            "period": liq.usdcnh_date.strftime("%Y-%m-%d") if liq.usdcnh_date else "—",
            "note": "外资与成长股偏好信号",
        })
    if liq.north_money is not None:
        cells.append({
            "label": "上一交易日 · 北向资金",
            "value": f"{liq.north_money:+,.1f}",
            "unit": "亿元",
            "delta": f"南向 {liq.south_money:+,.1f} 亿" if liq.south_money is not None else "",
            "delta_dir": _direction(liq.north_money),
            "period": liq.hsgt_date.strftime("%Y-%m-%d") if liq.hsgt_date else "—",
            "note": "外资态度的最快可观测变量",
        })
    if liq.margin_total is not None:
        cells.append({
            "label": "两融余额",
            "value": f"{liq.margin_total:.2f}",
            "unit": "万亿元",
            "delta": f"Δ {liq.margin_change:+.3f} 万亿" if liq.margin_change is not None else "",
            "delta_dir": _direction(liq.margin_change),
            "period": liq.margin_date.strftime("%Y-%m-%d") if liq.margin_date else "—",
            "note": "活跃资金 / 杠杆情绪",
        })

    if not cells:
        return None

    cells_text = "\n".join(
        f"  - {c['label']}: {c['value']} {c.get('unit','')} ({c.get('period','—')})  {c.get('note','')}"
        for c in cells
    )
    user = f"""
=== 资金面快照 ===
{cells_text}

=== 任务 ===
{prompts.LIQUIDITY_INSTRUCTIONS}

=== 输出 schema ===
{prompts.LIQUIDITY_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=900,
    )
    moid = _persist_model_output(ctx, section_key="macro_morning.s3_liquidity",
                                 prompt_name="macro_morning.s3_liquidity",
                                 parsed=parsed, resp=resp, status=status)
    tone = "数据不足"
    commentary = ""
    if isinstance(parsed, dict):
        tone = parsed.get("tone") or tone
        commentary = parsed.get("commentary") or ""

    return {
        "key": "macro_morning.s3_liquidity",
        "title": "盘前利率、流动性与汇率参考",
        "order": 3,
        "type": "liquidity_grid",
        "content_json": {
            "cells": cells,
            "tone": tone,
            "commentary": commentary,
        },
        "prompt_name": "macro_morning.s3_liquidity",
        "model_output_id": moid,
    }


# ─── S4: news list (curated from active policy events) ────────────────────

def _build_s4_news(ctx: RuntimeCtx) -> dict | None:
    from ifa.families._shared.news import post_process_news_events
    if not ctx.policy_events:
        return None
    candidates = [
        {
            "title": p.event_title,
            "source_name": p.source_name,
            "publish_time": p.publish_time.isoformat() if p.publish_time else None,
            "policy_dimension": p.policy_dimension,
            "policy_signal": p.policy_signal,
            "summary": p.summary,
            "market_implication": p.market_implication,
            "affected_areas": p.affected_areas,
        }
        for p in ctx.policy_events[:30] if p.publish_time
    ]
    user = f"""
=== 候选事件（按时间倒序，{len(candidates)} 条） ===
{json.dumps(candidates, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.NEWS_INSTRUCTIONS}

=== 输出 schema ===
{prompts.NEWS_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="macro_morning.s4_news",
                                 prompt_name="macro_morning.s4_news",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"has_major_events": False, "events": [],
                                                        "fallback_text": "今日无重大宏观事件需提示。"}
    content["events"] = post_process_news_events(content.get("events") or [], candidates)
    return {
        "key": "macro_morning.s4_news",
        "title": "关键新闻、政策与事件摘要",
        "order": 4,
        "type": "news_list",
        "content_json": content,
        "prompt_name": "macro_morning.s4_news",
        "model_output_id": moid,
    }


# ─── S5: policy matrix ────────────────────────────────────────────────────

def _build_s5_policy(ctx: RuntimeCtx) -> dict:
    grouped: dict[str, list[PolicyEventRow]] = defaultdict(list)
    for p in ctx.policy_events:
        grouped[p.policy_dimension].append(p)

    by_dim_text = []
    for dim in ["稳增长", "新质生产力/科技自立", "消费与内需", "地产与信用",
                "资本市场", "金融监管/行业监管", "货币/财政", "外部冲击"]:
        items = grouped.get(dim, [])[:6]
        if items:
            joined = "; ".join(f"[{p.policy_signal}] {p.event_title}" for p in items)
        else:
            joined = "（无活跃事件）"
        by_dim_text.append(f"{dim}: {joined}")

    user = f"""
=== 当前活跃政策事件（按维度） ===
{chr(10).join(by_dim_text)}

=== 任务 ===
{prompts.POLICY_MATRIX_INSTRUCTIONS}

=== 输出 schema ===
{prompts.POLICY_MATRIX_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="macro_morning.s5_policy",
                                 prompt_name="macro_morning.s5_policy",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"rows": [], "summary": ""}
    return {
        "key": "macro_morning.s5_policy",
        "title": "政策与大政方针观察",
        "order": 5,
        "type": "policy_matrix",
        "content_json": content,
        "prompt_name": "macro_morning.s5_policy",
        "model_output_id": moid,
    }


# ─── S6: cross asset ───────────────────────────────────────────────────────

def _build_s6_cross_asset(ctx: RuntimeCtx) -> dict:
    items_for_llm = [
        {
            "name": a.name,
            "code": a.code,
            "latest": a.latest,
            "pct_change": a.pct_change,
            "period": a.period,
        }
        for a in ctx.cross_asset
    ]
    user = f"""
=== 跨资产快照（上一交易日，{len(items_for_llm)} 个） ===
{json.dumps(items_for_llm, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.CROSS_ASSET_INSTRUCTIONS}

=== 输出 schema ===
{prompts.CROSS_ASSET_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="macro_morning.s6_cross_asset",
                                 prompt_name="macro_morning.s6_cross_asset",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"items": [], "cross_asset_tone": "数据不足", "summary": ""}
    return {
        "key": "macro_morning.s6_cross_asset",
        "title": "隔夜外部、港股与跨资产联动",
        "order": 6,
        "type": "cross_asset_grid",
        "content_json": content,
        "prompt_name": "macro_morning.s6_cross_asset",
        "model_output_id": moid,
    }


# ─── S7: macro→sector mapping ──────────────────────────────────────────────

def _build_s7_mapping(ctx: RuntimeCtx, prior_sections: list[dict]) -> dict:
    # Pass earlier section JSONs as context so the mapping is consistent
    ctx_blob = {s["key"]: s["content_json"] for s in prior_sections if s["key"].startswith("macro_morning.s")}
    user = f"""
=== 上文 sections（已生成的本报告内容） ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:6000]}

=== 任务 ===
{prompts.MAPPING_INSTRUCTIONS}

=== 输出 schema ===
{prompts.MAPPING_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="macro_morning.s7_mapping",
                                 prompt_name="macro_morning.s7_mapping",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"rows": []}
    return {
        "key": "macro_morning.s7_mapping",
        "title": "宏观变量对 A 股板块映射",
        "order": 7,
        "type": "mapping_table",
        "content_json": content,
        "prompt_name": "macro_morning.s7_mapping",
        "model_output_id": moid,
    }


# ─── S8: risk list ─────────────────────────────────────────────────────────

def _build_s8_risk(ctx: RuntimeCtx, prior_sections: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior_sections if s["key"].startswith("macro_morning.s")}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5000]}

=== 任务 ===
{prompts.RISK_INSTRUCTIONS}

=== 输出 schema ===
{prompts.RISK_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="macro_morning.s8_risk",
                                 prompt_name="macro_morning.s8_risk",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"risk_level": "low", "risks": []}
    # CSS class mapping
    for r in content.get("risks", []) or []:
        c = (r.get("confidence") or "low").lower()
        r["confidence_class"] = {"high": "high", "medium": "med", "low": "low"}.get(c, "med")
        # store as judgment
        try:
            insert_judgment(
                ctx.engine,
                report_run_id=ctx.run.report_run_id,
                section_key="macro_morning.s8_risk",
                judgment_type="risk",
                judgment_text=r.get("risk", ""),
                target=r.get("watch_indicator"),
                horizon="today_full_day",
                confidence=c if c in {"high","medium","low"} else "low",
                validation_method=r.get("possible_impact"),
            )
        except Exception:
            pass
    return {
        "key": "macro_morning.s8_risk",
        "title": "今日宏观风险清单",
        "order": 8,
        "type": "risk_list",
        "content_json": content,
        "prompt_name": "macro_morning.s8_risk",
        "model_output_id": moid,
    }


# ─── S9: hypotheses ────────────────────────────────────────────────────────

def _build_s9_hypotheses(ctx: RuntimeCtx, prior_sections: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior_sections if s["key"].startswith("macro_morning.s")}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5000]}

=== 任务 ===
{prompts.HYPOTHESES_INSTRUCTIONS}

=== 输出 schema ===
{prompts.HYPOTHESES_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="macro_morning.s9_hypotheses",
                                 prompt_name="macro_morning.s9_hypotheses",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"hypotheses": []}
    # Persist each hypothesis as a judgment (key marker: judgment_type='hypothesis')
    for h in content.get("hypotheses", []) or []:
        try:
            insert_judgment(
                ctx.engine,
                report_run_id=ctx.run.report_run_id,
                section_key="macro_morning.s9_hypotheses",
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
        "key": "macro_morning.s9_hypotheses",
        "title": "今日需要验证的宏观假设",
        "order": 9,
        "type": "hypotheses_list",
        "content_json": content,
        "prompt_name": "macro_morning.s9_hypotheses",
        "model_output_id": moid,
    }


# ─── S10: text-derived capture (DB read, no LLM) ──────────────────────────

def _build_s10_capture(ctx: RuntimeCtx) -> dict:
    rows = []
    for r in ctx.text_derived[:12]:
        if r.value is None:
            value_display = "—"
        elif r.unit and r.unit.endswith("%"):
            value_display = f"{r.value:+.2f}%"
        elif r.unit:
            value_display = f"{r.value:,.2f} {r.unit}"
        else:
            value_display = f"{r.value:,.2f}"
        yoy_display = _fmt_pct(r.yoy) if r.yoy is not None else "—"
        rows.append({
            "indicator_display": r.indicator_display,
            "reported_period": r.reported_period,
            "value_display": value_display,
            "yoy_display": yoy_display,
            "source_name": f"{r.publisher_or_origin or ''} / {r.source_name or ''}".strip(" /"),
            "publish_time_display": fmt_bjt(r.source_publish_time, "%Y-%m-%d %H:%M"),
            "evidence_sentence": (r.evidence_sentence or "")[:200],
            "confidence": r.confidence,
        })
    return {
        "key": "macro_morning.s10_capture",
        "title": "新闻源抽取的宏观数据捕获",
        "order": 10,
        "type": "indicator_capture_table",
        "content_json": {
            "intro": "由 macro_text_derived_capture_job 从 major_news / news / npr 抽取，等待官方结构化源确认。",
            "rows": rows,
            "fallback_text": "本报告窗口内未捕获新增的'新增贷款 / 贷款余额'等低频金融数据。",
        },
    }


# ─── S11: disclaimer (static) ──────────────────────────────────────────────

def _build_s11_disclaimer() -> dict:
    return {
        "key": "macro_morning.s11_disclaimer",
        "title": "免责声明",
        "order": 11,
        "type": "disclaimer",
        "content_json": {
            "paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
            "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH,
        },
    }


# ─── Orchestrator ─────────────────────────────────────────────────────────

def run_macro_morning(
    *,
    report_date: dt.date,
    data_cutoff_at: dt.datetime,
    triggered_by: str | None = None,
    on_log: Callable[[str], None] = lambda m: None,
) -> Path:
    """Build the full Macro morning report. Returns the path of the rendered HTML."""
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
    on_log(f"[run {str(run.report_run_id)[:8]}] starting morning report for {report_date} cutoff {fmt_bjt(data_cutoff_at)} BJT")

    try:
        from ifa.core.calendar import prev_trading_day
        prev_td = prev_trading_day(engine, report_date)  # post-holiday-safe
        # Pre-fetch all data
        on_log("fetching macro panel (cn_gdp/cpi/ppi/pmi/m/sf_month)…")
        panel = data.fetch_macro_panel(tushare)
        on_log("fetching liquidity snapshot (shibor/lpr/fx/hsgt/margin)…")
        liquidity = data.fetch_liquidity_snapshot(tushare, ref_date=prev_td)
        on_log("fetching cross-asset (HK / 沪深 / 期货)…")
        cross_asset = data.fetch_cross_asset(tushare, ref_date=prev_td)
        on_log("reading macro_text_derived_indicators + macro_policy_event_memory…")
        text_derived = data.fetch_text_derived(engine, since_days=120)
        policy_events = data.fetch_active_policy_events(engine, since_days=14)

        ctx = RuntimeCtx(
            engine=engine, llm=llm, tushare=tushare, run=run,
            panel=panel, liquidity=liquidity, cross_asset=cross_asset,
            text_derived=text_derived, policy_events=policy_events, on_log=on_log,
        )

        sections: list[dict] = []
        for label, builder in [
            ("S1 tone",         lambda: _build_s1_tone(ctx)),
            ("S2 panel",        lambda: _build_s2_panel(ctx)),
            ("S3 liquidity",    lambda: _build_s3_liquidity(ctx)),
            ("S4 news",         lambda: _build_s4_news(ctx)),
            ("S5 policy",       lambda: _build_s5_policy(ctx)),
            ("S6 cross asset",  lambda: _build_s6_cross_asset(ctx)),
            ("S7 mapping",      lambda: _build_s7_mapping(ctx, sections)),
            ("S8 risk",         lambda: _build_s8_risk(ctx, sections)),
            ("S9 hypotheses",   lambda: _build_s9_hypotheses(ctx, sections)),
            ("S10 capture",     lambda: _build_s10_capture(ctx)),
            ("S11 disclaimer",  _build_s11_disclaimer),
        ]:
            t0 = time.monotonic()
            on_log(f"building {label}…")
            sec = builder()
            if sec is None:
                on_log(f'  {label} skipped (data not available at this slot)')
                continue
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

        # Render
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
        "title": f"中国宏观早盘报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": "China Macro Pre-Open Briefing — Lindenwood Management LLC",
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
    fname = f"CN_macro_morning_{run.report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
