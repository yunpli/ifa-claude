"""Tech morning report — orchestrator + 12 section builders.

Sections per tech.txt §八:
  S1  tech_tone           今日 Tech 总体判断
  S2  layer_map           AI Five-Layer Cake 板块地图
  S3  category_strength   昨日科技板块强弱与热点回放
  S4  us_overnight        隔夜美股科技
  S5  news_list           全球科技与产业新闻摘要
  S6  mapping_table       今日可能活跃的科技方向
  S7  leader_table        科技龙头与核心票观察
  S8  candidate_pool      潜在蓄势待发标的池
  S9  focus_deep          用户重点关注科技股深度观察
  S10 focus_brief         用户普通关注科技股简要观察
  S11 hypotheses_list     今日需要验证的 Tech 假设
  S12 disclaimer          完整免责声明
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
    BoardSnapshot,
    FocusStockSnap,
    SectorBar,
    StockMover,
    USStockSnap,
)
from .focus import FocusStock, get_focus_for, tech_only
from .universe import AI_LAYERS, layer_by_id

TEMPLATE_VERSION = "tech_morning_v2.1.0"
REPORT_FAMILY = "tech"
REPORT_TYPE = "morning_long"
SLOT = "morning"
MARKET = "china_a"


@dataclass
class TechCtx:
    engine: Engine
    llm: LLMClient
    tushare: TuShareClient
    run: ReportRun
    user: str
    boards_by_layer: dict[str, list[BoardSnapshot]]
    tech_members: dict[str, set[str]]
    limit_up: list[StockMover]
    top_movers: list[StockMover]
    moneyflow_by_code: dict[str, float]
    us_stocks: list[USStockSnap]
    news_df: Any
    sw_sectors: list[SectorBar]
    important_focus: list[FocusStockSnap]
    regular_focus: list[FocusStockSnap]
    on_log: Callable[[str], None]


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


def _fmt_count(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1e8:
        return f"{v/1e8:.2f}亿"
    if v >= 1e4:
        return f"{v/1e4:.1f}万"
    return f"{v:,.0f}"


# ─── S1: Tech tone ────────────────────────────────────────────────────────

def _build_s1_tone(ctx: TechCtx) -> dict:
    layer_summary = []
    for L in AI_LAYERS:
        boards = ctx.boards_by_layer.get(L.layer_id, [])
        with_data = [b for b in boards if b.pct_change is not None]
        if with_data:
            avg = sum(b.pct_change for b in with_data) / len(with_data)
            top = max(with_data, key=lambda b: b.pct_change)
            layer_summary.append(
                f"  {L.layer_id} ({L.layer_name}): {len(with_data)}/{len(boards)} 板块有数据, "
                f"均 {avg:+.2f}%, 领涨 {top.name} {top.pct_change:+.2f}%"
            )
        else:
            layer_summary.append(f"  {L.layer_id}: 板块数据缺失")

    us_blob = ", ".join(
        f"{u.display_name} {u.pct_change:+.2f}%" if u.pct_change is not None else f"{u.display_name} 无数据"
        for u in ctx.us_stocks[:8]
    )
    n_limit_up = len(ctx.limit_up)

    user = f"""
=== 报告时点 ===
报告日期 (北京时间): {ctx.run.report_date}
数据截止: {fmt_bjt(ctx.run.data_cutoff_at)} 北京时间

=== 5 层昨日板块表现 ===
{chr(10).join(layer_summary)}

=== 隔夜美股科技 ===
{us_blob}

=== 上一交易日 tech 涨停股数 ===
{n_limit_up}

=== 任务 ===
{prompts.TONE_INSTRUCTIONS}

=== 输出 schema ===
{prompts.TONE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="tech_morning.s1_tone",
                                 prompt_name="tech_morning.s1_tone",
                                 parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {"tech_state": "暂非市场核心", "tech_state_short": "边缘",
                  "strongest_layer": "mixed", "risk_level": "low",
                  "headline": "今日科技线信号不强；以多日观察为主。",
                  "summary": "板块数据未形成明确合力，建议等待主线确认。",
                  "validation_points": []}
        ctx.run.fallback_used = True

    insert_judgment(
        ctx.engine, report_run_id=ctx.run.report_run_id,
        section_key="tech_morning.s1_tone", judgment_type="tech_tone",
        judgment_text=parsed.get("headline", ""),
        target=parsed.get("strongest_layer", "mixed"),
        horizon="today_full_day", confidence="medium",
        validation_method="evening review of A-share tech sector + leader behaviour",
    )
    return {
        "key": "tech_morning.s1_tone", "title": "今日 Tech 总体判断",
        "order": 1, "type": "tech_tone", "content_json": parsed,
        "prompt_name": "tech_morning.s1_tone", "model_output_id": moid,
    }


# ─── S2: Layer map ─────────────────────────────────────────────────────────

def _build_s2_layer_map(ctx: TechCtx) -> dict:
    bulk: list[dict] = []
    for L in AI_LAYERS:
        boards = ctx.boards_by_layer.get(L.layer_id, [])
        bulk.append({
            "layer_id": L.layer_id,
            "layer_name": L.layer_name,
            "layer_en": L.layer_en,
            "narrative": L.narrative,
            "boards": [
                {"name": b.name, "pct_change": b.pct_change,
                 "vol": b.volume, "history_close": b.history_close[-5:]}
                for b in boards if b.pct_change is not None
            ],
            "n_boards_with_data": sum(1 for b in boards if b.pct_change is not None),
            "n_boards_total": len(boards),
        })

    user = f"""
=== 5 层板块今日数据 ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.LAYER_MAP_INSTRUCTIONS}

=== 输出 schema ===
{prompts.LAYER_MAP_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="tech_morning.s2_layer_map",
                                 prompt_name="tech_morning.s2_layer_map",
                                 parsed=parsed, resp=resp, status=status)
    by_idx: dict[int, dict] = {}
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                by_idx[idx] = entry

    layers_payload: list[dict] = []
    for i, L in enumerate(AI_LAYERS):
        info = by_idx.get(i, {})
        boards = ctx.boards_by_layer.get(L.layer_id, [])
        boards_view = []
        for b in boards:
            if b.pct_change is None:
                continue
            boards_view.append({
                "name": b.name,
                "pct_display": _fmt_pct(b.pct_change),
                "pct_dir": _direction(b.pct_change),
            })
        layers_payload.append({
            "layer_id": L.layer_id,
            "layer_name": L.layer_name,
            "layer_en": L.layer_en,
            "narrative": L.narrative,
            "yesterday_strength": info.get("yesterday_strength") or "—",
            "today_attention": info.get("today_attention") or "—",
            "rotation_role": info.get("rotation_role") or "",
            "key_observation": info.get("key_observation") or "",
            "boards": boards_view,
        })

    return {
        "key": "tech_morning.s2_layer_map", "title": "AI Five-Layer Cake 科技板块地图",
        "order": 2, "type": "layer_map",
        "content_json": {"layers": layers_payload},
        "prompt_name": "tech_morning.s2_layer_map", "model_output_id": moid,
    }


# ─── S3: board strength recap ─────────────────────────────────────────────

def _build_s3_board_recap(ctx: TechCtx) -> dict:
    # Flatten all boards across layers, sort by pct_change desc
    all_boards: list[tuple[BoardSnapshot, str]] = []
    for L in AI_LAYERS:
        for b in ctx.boards_by_layer.get(L.layer_id, []):
            if b.pct_change is None:
                continue
            all_boards.append((b, L.layer_id))
    all_boards.sort(key=lambda x: x[0].pct_change or 0, reverse=True)
    top_n = all_boards[:14]

    bulk = []
    n_limit_up_by_board: dict[str, int] = {}
    for mover in ctx.limit_up:
        for board_code in mover.board_hits:
            n_limit_up_by_board[board_code] = n_limit_up_by_board.get(board_code, 0) + 1

    for i, (b, layer_id) in enumerate(top_n):
        bulk.append({
            "name": b.name,
            "layer_id": layer_id,
            "pct_change": b.pct_change,
            "vol": b.volume,
            "n_limit_up_in_board": n_limit_up_by_board.get(b.ts_code, 0),
        })

    user = f"""
=== 板块清单（按昨日涨幅排序，最多 14 条） ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 涨停 tech 个股数 ===
{len(ctx.limit_up)}

=== 任务 ===
{prompts.BOARD_RECAP_INSTRUCTIONS}

=== 输出 schema ===
{prompts.BOARD_RECAP_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="tech_morning.s3_board_recap",
                                 prompt_name="tech_morning.s3_board_recap",
                                 parsed=parsed, resp=resp, status=status)
    by_idx: dict[int, dict] = {}
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                by_idx[idx] = entry

    layer_labels = {L.layer_id: L.layer_id.upper() for L in AI_LAYERS}
    rows = []
    for i, (b, layer_id) in enumerate(top_n):
        info = by_idx.get(i, {})
        rows.append({
            "board_name": b.name,
            "layer_label": layer_labels.get(layer_id, "—"),
            "strength": info.get("strength") or "—",
            "pct_display": _fmt_pct(b.pct_change),
            "pct_dir": _direction(b.pct_change),
            "n_limit_up": n_limit_up_by_board.get(b.ts_code, 0),
            "commentary": info.get("commentary") or "—",
            "top_stock_role": info.get("top_stock_role") or "",
        })
    return {
        "key": "tech_morning.s3_board_recap",
        "title": "昨日科技板块强弱与热点回放",
        "order": 3, "type": "tech_board_recap",
        "content_json": {"rows": rows},
        "prompt_name": "tech_morning.s3_board_recap", "model_output_id": moid,
    }


# ─── S4: US overnight ─────────────────────────────────────────────────────

def _build_s4_us(ctx: TechCtx) -> dict:
    bulk = [{"ticker": u.ticker, "name": u.display_name, "role": u.role,
             "close": u.close, "pct_change": u.pct_change} for u in ctx.us_stocks]
    user = f"""
=== 隔夜美股科技板块 ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 任务 ===
为每只美股给出一个 30 字内的 A 股映射方向（A 股哪些板块/方向 likely 受影响）。
最后给出 commentary（120-150 字综合判断："今日 A 股科技开盘前可参考的隔夜信号"）。
返回 schema:
{{
  "results":[{{"candidate_index":0,"a_share_mapping":"..."}}],
  "commentary":"..."
}}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(ctx, section_key="tech_morning.s4_us",
                                 prompt_name="tech_morning.s4_us",
                                 parsed=parsed, resp=resp, status=status)
    by_idx: dict[int, str] = {}
    commentary = ""
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                by_idx[idx] = entry.get("a_share_mapping") or ""
        commentary = parsed.get("commentary") or ""

    stocks = []
    for i, u in enumerate(ctx.us_stocks):
        stocks.append({
            "ticker": u.ticker, "display_name": u.display_name, "role": u.role,
            "close_display": f"{u.close:,.2f}" if u.close is not None else "—",
            "pct_display": _fmt_pct(u.pct_change),
            "pct_dir": _direction(u.pct_change),
            "a_share_mapping": by_idx.get(i, ""),
        })
    return {
        "key": "tech_morning.s4_us", "title": "隔夜美股科技",
        "order": 4, "type": "us_overnight",
        "content_json": {"stocks": stocks, "commentary": commentary},
        "prompt_name": "tech_morning.s4_us", "model_output_id": moid,
    }


# ─── S5: tech news ────────────────────────────────────────────────────────

def _build_s5_news(ctx: TechCtx) -> dict:
    if ctx.news_df is None or (hasattr(ctx.news_df, "empty") and ctx.news_df.empty):
        return {
            "key": "tech_morning.s5_news", "title": "全球科技与产业新闻摘要",
            "order": 5, "type": "news_list",
            "content_json": {"events": [], "fallback_text": "近 24 小时未捕获显著的科技产业新闻。"},
        }
    from ifa.core.report.timezones import BJT
    candidates = []
    for _, row in ctx.news_df.head(20).iterrows():
        dt_v = row.get("datetime")
        # tag with BJT before serialising
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
=== 候选 tech 新闻 ===
{json.dumps(candidates, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.NEWS_INSTRUCTIONS}

=== 输出 schema ===
{prompts.NEWS_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="tech_morning.s5_news",
                                 prompt_name="tech_morning.s5_news",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"events": [], "fallback_text": ""}
    from ifa.families._shared.news import post_process_news_events
    content["events"] = post_process_news_events(content.get("events") or [], candidates)
    return {
        "key": "tech_morning.s5_news", "title": "全球科技与产业新闻摘要",
        "order": 5, "type": "news_list", "content_json": content,
        "prompt_name": "tech_morning.s5_news", "model_output_id": moid,
    }


# ─── S6: active directions ────────────────────────────────────────────────

def _build_s6_directions(ctx: TechCtx, prior: list[dict]) -> dict:
    ctx_blob = {s["key"]: s["content_json"] for s in prior}
    user = f"""
=== 上文 sections ===
{json.dumps(ctx_blob, ensure_ascii=False, default=str)[:5500]}

=== 任务 ===
{prompts.ACTIVE_DIRECTIONS_INSTRUCTIONS}

=== 输出 schema ===
{prompts.ACTIVE_DIRECTIONS_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="tech_morning.s6_directions",
                                 prompt_name="tech_morning.s6_directions",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"directions": []}
    return {
        "key": "tech_morning.s6_directions", "title": "今日可能活跃的科技方向",
        "order": 6, "type": "directions_list",
        "content_json": {"directions": content.get("directions") or []},
        "prompt_name": "tech_morning.s6_directions", "model_output_id": moid,
    }


# ─── S7: leaders ───────────────────────────────────────────────────────────

def _build_s7_leaders(ctx: TechCtx) -> dict:
    pool: dict[str, dict] = {}
    for m in ctx.limit_up:
        pool.setdefault(m.ts_code, {
            "ts_code": m.ts_code, "name": m.name, "pct_change": m.pct_change,
            "amount": m.amount, "turnover_rate": m.turnover_rate,
            "layer_id": m.layer_id, "limit_status": m.limit_status,
            "moneyflow_net": ctx.moneyflow_by_code.get(m.ts_code),
            "role_hint": "limit_up",
        })
    for m in ctx.top_movers[:25]:
        pool.setdefault(m.ts_code, {
            "ts_code": m.ts_code, "name": m.name, "pct_change": m.pct_change,
            "amount": m.amount, "turnover_rate": m.turnover_rate,
            "layer_id": m.layer_id, "limit_status": None,
            "moneyflow_net": ctx.moneyflow_by_code.get(m.ts_code),
            "role_hint": m.role,
        })
    candidates = list(pool.values())
    user = f"""
=== 候选个股池（涨停 + 涨幅前列，{len(candidates)} 只） ===
{json.dumps(candidates, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.LEADERS_INSTRUCTIONS}

=== 输出 schema ===
{prompts.LEADERS_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="tech_morning.s7_leaders",
                                 prompt_name="tech_morning.s7_leaders",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"leaders": []}
    return {
        "key": "tech_morning.s7_leaders", "title": "科技龙头与核心票观察",
        "order": 7, "type": "leader_table",
        "content_json": content,
        "prompt_name": "tech_morning.s7_leaders", "model_output_id": moid,
    }


# ─── S8: candidates pool ───────────────────────────────────────────────────

def _build_s8_candidates(ctx: TechCtx) -> dict:
    # filter top_movers to "蓄势" candidates: pct in [-1, +3], in tech board, not yet limit_up
    limit_up_codes = {m.ts_code for m in ctx.limit_up}
    candidates = []
    for m in ctx.top_movers:
        if m.ts_code in limit_up_codes:
            continue
        if m.pct_change is None:
            continue
        if -1.0 <= m.pct_change <= 3.0:
            candidates.append({
                "ts_code": m.ts_code, "name": m.name,
                "pct_change": m.pct_change, "amount": m.amount,
                "layer_id": m.layer_id, "boards": m.board_hits,
                "moneyflow_net": ctx.moneyflow_by_code.get(m.ts_code),
            })
    candidates = candidates[:25]
    user = f"""
=== 候选蓄势池 ({len(candidates)} 只) ===
{json.dumps(candidates, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.CANDIDATE_INSTRUCTIONS}

=== 输出 schema ===
{prompts.CANDIDATE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(ctx, section_key="tech_morning.s8_candidates",
                                 prompt_name="tech_morning.s8_candidates",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"candidates": []}
    return {
        "key": "tech_morning.s8_candidates", "title": "潜在蓄势待发标的池",
        "order": 8, "type": "candidate_pool",
        "content_json": content,
        "prompt_name": "tech_morning.s8_candidates", "model_output_id": moid,
    }


# ─── S9: focus deep ───────────────────────────────────────────────────────

def _build_s9_focus_deep(ctx: TechCtx) -> dict:
    tech_imp = [s for s in ctx.important_focus if s.spec.layer != "non_tech"][:5]
    if not tech_imp:
        return {
            "key": "tech_morning.s9_focus_deep", "title": "用户重点关注科技股深度观察",
            "order": 9, "type": "focus_deep",
            "content_json": {"rows": [], "fallback_text": "用户重点关注池中无 Tech 标的。"},
        }
    bulk = []
    for i, snap in enumerate(tech_imp):
        bulk.append({
            "candidate_index": i,
            "stock_code": snap.spec.ts_code,
            "stock_name": snap.spec.display_name,
            "layer_id": snap.spec.layer,
            "sub_theme": snap.spec.sub_theme,
            "close": snap.close, "pct_change": snap.pct_change,
            "turnover_rate": snap.turnover_rate, "pe": snap.pe, "pb": snap.pb,
            "moneyflow_net": snap.moneyflow_net,
            "history_close": snap.history_close[-5:],
            "data_status": snap.data_status,
        })
    user = f"""
=== 用户重点关注 Tech 标的 ({len(bulk)} 只) ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.FOCUS_DEEP_INSTRUCTIONS}

=== 输出 schema ===
{prompts.FOCUS_DEEP_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2800,
    )
    moid = _persist_model_output(ctx, section_key="tech_morning.s9_focus_deep",
                                 prompt_name="tech_morning.s9_focus_deep",
                                 parsed=parsed, resp=resp, status=status)
    by_idx: dict[int, dict] = {}
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                by_idx[idx] = entry
    rows = []
    for i, snap in enumerate(tech_imp):
        info = by_idx.get(i, {})
        spark = sparkline_svg(snap.history_close, width=180, height=32) if snap.history_close else ""
        rows.append({
            "stock_code": snap.spec.ts_code,
            "stock_name": snap.spec.display_name,
            "layer_id": snap.spec.layer,
            "sub_theme": snap.spec.sub_theme,
            "close_display": f"{snap.close:,.2f}" if snap.close is not None else "—",
            "pct_display": _fmt_pct(snap.pct_change),
            "pct_dir": _direction(snap.pct_change),
            "mf_display": _fmt_count(snap.moneyflow_net) + " 元" if snap.moneyflow_net is not None else "—",
            "spark_svg": spark,
            "status": info.get("status") or "—",
            "today_observation": info.get("today_observation") or "—",
            "scenario_plans": info.get("scenario_plans") or [],
            "risk_note": info.get("risk_note") or "",
        })
    return {
        "key": "tech_morning.s9_focus_deep",
        "title": f"用户重点关注科技股深度观察 · @{ctx.user}",
        "order": 9, "type": "focus_deep",
        "content_json": {"rows": rows},
        "prompt_name": "tech_morning.s9_focus_deep", "model_output_id": moid,
    }


# ─── S10: focus brief ─────────────────────────────────────────────────────

def _build_s10_focus_brief(ctx: TechCtx) -> dict:
    tech_reg = [s for s in ctx.regular_focus if s.spec.layer != "non_tech"][:10]
    if not tech_reg:
        return {
            "key": "tech_morning.s10_focus_brief", "title": "用户普通关注科技股简要观察",
            "order": 10, "type": "focus_brief",
            "content_json": {"rows": [], "fallback_text": "用户普通关注池中无 Tech 标的。"},
        }
    bulk = []
    for i, snap in enumerate(tech_reg):
        bulk.append({
            "candidate_index": i,
            "stock_code": snap.spec.ts_code, "stock_name": snap.spec.display_name,
            "layer_id": snap.spec.layer, "sub_theme": snap.spec.sub_theme,
            "pct_change": snap.pct_change, "turnover_rate": snap.turnover_rate,
            "moneyflow_net": snap.moneyflow_net,
            "history_close": snap.history_close[-5:],
        })
    user = f"""
=== 用户普通关注 Tech 标的 ({len(bulk)} 只) ===
{json.dumps(bulk, ensure_ascii=False, indent=2)}

=== 任务 ===
{prompts.FOCUS_BRIEF_INSTRUCTIONS}

=== 输出 schema ===
{prompts.FOCUS_BRIEF_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2200,
    )
    moid = _persist_model_output(ctx, section_key="tech_morning.s10_focus_brief",
                                 prompt_name="tech_morning.s10_focus_brief",
                                 parsed=parsed, resp=resp, status=status)
    by_idx: dict[int, dict] = {}
    if isinstance(parsed, dict):
        for entry in parsed.get("results") or []:
            idx = entry.get("candidate_index")
            if isinstance(idx, int):
                by_idx[idx] = entry
    rows = []
    for i, snap in enumerate(tech_reg):
        info = by_idx.get(i, {})
        spark = sparkline_svg(snap.history_close, width=130, height=28) if snap.history_close else ""
        rows.append({
            "stock_code": snap.spec.ts_code,
            "stock_name": snap.spec.display_name,
            "layer_id": snap.spec.layer,
            "sub_theme": snap.spec.sub_theme,
            "close_display": f"{snap.close:,.2f}" if snap.close is not None else "—",
            "pct_display": _fmt_pct(snap.pct_change),
            "pct_dir": _direction(snap.pct_change),
            "spark_svg": spark,
            "state": info.get("state") or "—",
            "today_hint": info.get("today_hint") or "—",
        })
    return {
        "key": "tech_morning.s10_focus_brief",
        "title": f"用户普通关注科技股简要观察 · @{ctx.user}",
        "order": 10, "type": "focus_brief",
        "content_json": {"rows": rows},
        "prompt_name": "tech_morning.s10_focus_brief", "model_output_id": moid,
    }


# ─── S11: hypotheses ──────────────────────────────────────────────────────

def _build_s11_hypotheses(ctx: TechCtx, prior: list[dict]) -> dict:
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
    moid = _persist_model_output(ctx, section_key="tech_morning.s11_hypotheses",
                                 prompt_name="tech_morning.s11_hypotheses",
                                 parsed=parsed, resp=resp, status=status)
    content = parsed if isinstance(parsed, dict) else {"hypotheses": []}
    for h in content.get("hypotheses", []) or []:
        try:
            insert_judgment(
                ctx.engine, report_run_id=ctx.run.report_run_id,
                section_key="tech_morning.s11_hypotheses", judgment_type="hypothesis",
                judgment_text=h.get("hypothesis", ""),
                target=", ".join(h.get("related_markets_or_sectors") or [])[:300],
                horizon=h.get("observation_window") or "today_full_day",
                confidence=(h.get("confidence") or "medium").lower(),
                validation_method=h.get("review_rule"),
            )
        except Exception:
            pass
    return {
        "key": "tech_morning.s11_hypotheses", "title": "今日需要验证的 Tech 假设",
        "order": 11, "type": "hypotheses_list", "content_json": content,
        "prompt_name": "tech_morning.s11_hypotheses", "model_output_id": moid,
    }


# ─── S12: disclaimer ──────────────────────────────────────────────────────

def _build_s12_disclaimer() -> dict:
    return {
        "key": "tech_morning.s12_disclaimer", "title": "免责声明",
        "order": 12, "type": "disclaimer",
        "content_json": {"paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
                         "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH},
    }


# ─── Orchestrator ─────────────────────────────────────────────────────────

def run_tech_morning(
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
    on_log(f"[run {str(run.report_run_id)[:8]}] starting Tech morning report for {report_date} user={user}")

    try:
        from ifa.core.calendar import prev_trading_day
        # Calendar T-1 fails on Mon / post-holiday opens — must be prev TRADING day.
        prev_day = prev_trading_day(engine, report_date)
        on_log("fetching SW L2 sector performance for tech 5 layers…")
        boards_by_layer = data.fetch_board_performance(tushare, on_date=prev_day, history_days=10, slot="morning")
        on_log("resolving tech sector members (SW PIT)…")
        tech_members = data.resolve_tech_members(tushare, engine, trade_date=prev_day)
        on_log(f"  {sum(len(v) for v in tech_members.values())} member rows across {len(tech_members)} boards")
        on_log("fetching limit-up tech stocks…")
        limit_up = data.fetch_limit_up_tech(tushare, on_date=prev_day, tech_members=tech_members)
        on_log(f"  {len(limit_up)} limit-up tech stocks on {prev_day}")
        on_log("fetching top movers in tech…")
        top_movers = data.fetch_top_movers_in_tech(tushare, on_date=prev_day,
                                                    tech_members=tech_members, top_n=40)
        all_tech_codes = list({m.ts_code for m in limit_up + top_movers})
        on_log("fetching money flow for tech codes…")
        mf_by_code = data.fetch_money_flow_top(tushare, on_date=prev_day, ts_codes=all_tech_codes)
        on_log("fetching US tech overnight…")
        us_stocks = data.fetch_us_tech_overnight(tushare, ref_date=prev_day)
        on_log("fetching tech news (last 24h)…")
        news_df = data.fetch_tech_news(tushare, end_bjt=to_bjt(data_cutoff_at), lookback_hours=24)
        on_log(f"  {len(news_df)} news after filter")
        on_log("fetching SW tech sector indexes…")
        sw_sectors = data.fetch_tech_sw_sectors(tushare, on_date=prev_day, slot="morning")
        on_log(f"loading user '{user}' focus list and enriching…")
        important_focus_specs, regular_focus_specs = get_focus_for(user)
        important_focus, regular_focus = data.enrich_focus(
            tushare, on_date=prev_day,
            important=important_focus_specs, regular=regular_focus_specs,
        )

        ctx = TechCtx(
            engine=engine, llm=llm, tushare=tushare, run=run, user=user,
            boards_by_layer=boards_by_layer, tech_members=tech_members,
            limit_up=limit_up, top_movers=top_movers,
            moneyflow_by_code=mf_by_code, us_stocks=us_stocks,
            news_df=news_df, sw_sectors=sw_sectors,
            important_focus=important_focus, regular_focus=regular_focus,
            on_log=on_log,
        )

        sections: list[dict] = []
        for label, builder in [
            ("S1 tone",        lambda: _build_s1_tone(ctx)),
            ("S2 layer map",   lambda: _build_s2_layer_map(ctx)),
            ("S3 board recap", lambda: _build_s3_board_recap(ctx)),
            ("S4 US",          lambda: _build_s4_us(ctx)),
            ("S5 news",        lambda: _build_s5_news(ctx)),
            ("S6 directions",  lambda: _build_s6_directions(ctx, sections)),
            ("S7 leaders",     lambda: _build_s7_leaders(ctx)),
            ("S8 candidates",  lambda: _build_s8_candidates(ctx)),
            ("S9 focus deep",  lambda: _build_s9_focus_deep(ctx)),
            ("S10 focus brief",lambda: _build_s10_focus_brief(ctx)),
            ("S11 hypotheses", lambda: _build_s11_hypotheses(ctx, sections)),
            ("S12 disclaimer", _build_s12_disclaimer),
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
        "title": f"中国科技 / AI 早盘报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": f"China Tech / AI Equity Pre-Open Briefing — Lindenwood Management LLC · @{user}",
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
    fname = f"CN_tech_morning_{user}_{run.report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
