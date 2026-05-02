"""SmartMoney evening report — orchestrator + 14 section builders.

Sections per smartmoney.txt §八 (with light reorganization):
  E1  tone_card           今日 SmartMoney 综述判断卡
  E2  market_pulse        市场资金水位（成交额 + 涨跌停结构 + 状态）
  E3  sector_flow_in      Top 资金流入板块
  E4  sector_flow_out     Top 资金流出板块
  E5  quality_flow        高质量流入板块（量价齐升 + 趋势确认）
  E6  crowding            拥挤板块预警
  E7  cycle_grid          板块情绪周期一览
  E8  tomorrow_targets    明日资金候选 Top 3-5
  E9  sector_structure    重点板块内部结构（龙头/中军/情绪先锋）
  E10 candidate_pool      候选股票池（补涨 / 趋势）
  E11 strategy_view       策略观察（主线延续 / 分歧修复 / 高低切 / 防守切换）
  E12 validation_points   明日验证点（沉淀的假设资产）
  E13 review              昨日假设复盘
  E14 disclaimer

Persistence:
  - Each LLM call → model_outputs row (via _persist_model_output).
  - Each hypothesis from E12 → report_judgments row (judgment_type='hypothesis').
  - Each section → report_sections row.
  - Final HTML → ~/claude/ifaenv/out/<run_mode>/CN_smartmoney_evening_YYYYMMDD_HHMM.html
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
from ifa.families.macro.morning import _safe_chat_json

from . import data, prompts
from .data import (
    CandidateStock,
    CycleGridRow,
    CycleTrajectoryRow,
    MarketPulse,
    SectorFlowRow,
    SectorStructureRow,
    TomorrowTarget,
)
from .transition_matrix import PHASES, TransitionMatrixModel

log = logging.getLogger(__name__)

TEMPLATE_VERSION = "smartmoney_evening_v0.1"
REPORT_FAMILY = "smartmoney"
REPORT_TYPE = "evening_long"
SLOT = "evening"
MARKET = "china_a"


# ── Runtime context ───────────────────────────────────────────────────────────

@dataclass
class SMEveningCtx:
    engine: Engine
    llm: LLMClient
    run: ReportRun
    pulse: MarketPulse
    flow_in: list[SectorFlowRow]
    flow_out: list[SectorFlowRow]
    quality: list[SectorFlowRow]
    crowded: list[SectorFlowRow]
    cycle_rows: list[CycleGridRow]
    cycle_trajectory: list[CycleTrajectoryRow]
    transition_model: TransitionMatrixModel | None
    pulse_series: dict[str, Any]
    target_pool: list[TomorrowTarget]
    structures: list[SectorStructureRow]
    candidates: list[CandidateStock]
    yesterday_hypotheses: list[dict[str, Any]]
    today_outcome: dict[str, Any]
    used_trade_date: dt.date | None
    on_log: Callable[[str], None]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _persist_model_output(ctx: "SMEveningCtx", *, section_key: str, prompt_name: str,
                          parsed: Any, resp: Any, status: str) -> uuid.UUID | None:
    """SmartMoney-specific persister so model_outputs.prompt_version uses
    smartmoney_prompts_v0.1 (not macro's bundle version)."""
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


def _fmt_amt(v: float | None, *, scale: float = 1e8, suffix: str = "亿") -> str:
    """Format an amount value (default: 万元 → 亿元)."""
    if v is None:
        return "—"
    return f"{v / scale:+.2f}{suffix}" if v < 0 else f"{v / scale:.2f}{suffix}"


def _fmt_pct(v: float | None) -> str:
    return f"{v:+.2f}%" if v is not None else "—"


def _svg_dual_line(
    *,
    series_a: list[float],
    series_b: list[float],
    width: int = 320,
    height: int = 64,
    pad_x: int = 4,
    pad_y: int = 6,
    color_a: str = "#1d4ed8",
    color_b: str = "#c2410c",
    label_a: str = "成交",
    label_b: str = "北向",
) -> str:
    """Inline SVG for two parallel normalized line series.

    Each series is min-max normalized independently (different units).  Empty
    inputs return ''. Series are padded with circle markers at each datapoint
    and a baseline ruler at y=midpoint.
    """
    n = max(len(series_a), len(series_b))
    if n < 2:
        return ""

    inner_w = width - 2 * pad_x
    inner_h = height - 2 * pad_y

    def _path(series: list[float], color: str) -> tuple[str, list[tuple[float, float]]]:
        if not series or len(series) < 2:
            return "", []
        smin, smax = min(series), max(series)
        rng = (smax - smin) or 1.0
        pts: list[tuple[float, float]] = []
        for i, v in enumerate(series):
            x = pad_x + (inner_w * i / (len(series) - 1))
            # Higher value → smaller y (flip)
            y = pad_y + inner_h * (1.0 - (v - smin) / rng)
            pts.append((x, y))
        d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        return d, pts

    path_a, pts_a = _path(series_a, color_a)
    path_b, pts_b = _path(series_b, color_b)

    circles_a = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="{color_a}" />'
        for x, y in pts_a
    )
    circles_b = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="{color_b}" />'
        for x, y in pts_b
    )

    legend = (
        f'<g font-family="-apple-system,BlinkMacSystemFont,sans-serif" font-size="9.5">'
        f'<text x="{pad_x}" y="{pad_y + 8}" fill="{color_a}">●&#160;{label_a}</text>'
        f'<text x="{pad_x + 56}" y="{pad_y + 8}" fill="{color_b}">●&#160;{label_b}</text>'
        f'</g>'
    )

    # End-of-series labels (latest values)
    end_labels = ""
    if pts_a:
        last_v = series_a[-1]
        end_labels += (
            f'<text x="{pts_a[-1][0]+3:.1f}" y="{pts_a[-1][1]+3:.1f}" fill="{color_a}" '
            f'font-family="-apple-system,BlinkMacSystemFont,sans-serif" font-size="9">{last_v:.0f}</text>'
        )
    if pts_b:
        last_v = series_b[-1]
        end_labels += (
            f'<text x="{pts_b[-1][0]+3:.1f}" y="{pts_b[-1][1]+10:.1f}" fill="{color_b}" '
            f'font-family="-apple-system,BlinkMacSystemFont,sans-serif" font-size="9">{last_v:+.0f}</text>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="10日资金面迷你折线图">'
        f'<path d="{path_a}" fill="none" stroke="{color_a}" stroke-width="1.4" />'
        f'{circles_a}'
        f'<path d="{path_b}" fill="none" stroke="{color_b}" stroke-width="1.4" />'
        f'{circles_b}'
        f'{legend}'
        f'{end_labels}'
        f'</svg>'
    )


# ── Section builders ─────────────────────────────────────────────────────────

# ─── E1: Tone card ────────────────────────────────────────────────────────────

def _build_e1_tone(ctx: SMEveningCtx) -> dict:
    flow_in_blob = "\n".join(
        f"  - {s.sector_name} ({s.sector_source})  {_fmt_pct(s.pct_change)}  "
        f"净流入 {_fmt_amt(s.net_amount, scale=1e4, suffix='万')}  "
        f"角色={s.role or '—'}  周期={s.cycle_phase or '—'}"
        for s in ctx.flow_in[:6]
    )
    flow_out_blob = "\n".join(
        f"  - {s.sector_name}  {_fmt_pct(s.pct_change)}  "
        f"净流出 {_fmt_amt(s.net_amount, scale=1e4, suffix='万')}"
        for s in ctx.flow_out[:5]
    )
    cycle_blob = "; ".join(
        f"{c.sector_name}={c.cycle_phase}"
        for c in ctx.cycle_rows[:10]
    ) or "(无活跃板块)"
    leaders_blob = "\n".join(
        f"  - {st.sector_name} 龙头={st.leader.get('name') if st.leader else '—'}  "
        f"周期={st.cycle_phase}  角色={st.role}"
        for st in ctx.structures[:5]
    ) or "  (无活跃板块结构数据)"

    user = f"""
=== 今日报告 ===
报告日期: {ctx.run.report_date} (北京时间)
slot: {SLOT}

=== 市场水位 ===
总成交额 {_fmt_amt(ctx.pulse.total_amount)}; 10 日均 {_fmt_amt(ctx.pulse.amount_10d_avg)};
60 日分位 {ctx.pulse.amount_percentile_60d:.2f};
涨家数 {ctx.pulse.up_count} / 跌家数 {ctx.pulse.down_count};
涨停 {ctx.pulse.limit_up_count} / 跌停 {ctx.pulse.limit_down_count};
最高连板 {ctx.pulse.max_consecutive_limit_up}; 炸板率 {ctx.pulse.blow_up_rate:.2%};
市场状态 = {ctx.pulse.market_state}

=== 资金流入 Top ===
{flow_in_blob or '  (无数据)'}

=== 资金流出 Top ===
{flow_out_blob or '  (无数据)'}

=== 板块情绪周期 ===
{cycle_blob}

=== 重点板块龙头 ===
{leaders_blob}

=== 任务 ===
{prompts.TONE_INSTRUCTIONS}

=== 输出 schema (返回纯 JSON 不带任何围栏或解释) ===
{prompts.TONE_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1600,
    )
    moid = _persist_model_output(ctx, section_key="smartmoney_evening.e1_tone",
                                 prompt_name="smartmoney_evening.e1_tone",
                                 parsed=parsed, resp=resp, status=status)
    if not isinstance(parsed, dict):
        parsed = {
            "tone": "中性观望", "tone_short": "中性",
            "headline": "今日资金行为信号有限，建议以数据面为主。",
            "summary": "本报告窗口内未捕获足以驱动报告主结论的资金面信号；以下分章节为客观数据面。",
            "bullets": [],
        }
        ctx.run.fallback_used = True
    return {
        "key": "smartmoney_evening.e1_tone", "title": "今日 SmartMoney 综述",
        "order": 1, "type": "tone_card",
        "content_json": parsed,
        "prompt_name": "smartmoney_evening.e1_tone", "model_output_id": moid,
    }


# ─── E2: Market pulse ─────────────────────────────────────────────────────────

def _build_e2_pulse(ctx: SMEveningCtx) -> dict:
    p = ctx.pulse
    series = ctx.pulse_series or {}
    amt_series = series.get("total_amount_yi") or []
    north_series = series.get("north_money_yi") or []
    series_dates = series.get("trade_dates") or []

    mini_chart_svg = ""
    if amt_series and len(amt_series) >= 2:
        mini_chart_svg = _svg_dual_line(
            series_a=amt_series,
            series_b=north_series if north_series and len(north_series) == len(amt_series) else amt_series,
            label_a="成交(亿)",
            label_b="北向(亿)" if north_series else "成交(亿)",
        )

    content = {
        "trade_date": str(p.trade_date) if p.trade_date else None,
        "market_state": p.market_state,
        "total_amount_yi": round(p.total_amount / 1e4, 2),         # 亿元
        "amount_10d_avg_yi": round(p.amount_10d_avg / 1e4, 2),
        "amount_percentile_60d": round(p.amount_percentile_60d, 4),
        "amount_ratio_10d": round(p.amount_ratio_10d, 3),
        "up_count": p.up_count, "down_count": p.down_count, "flat_count": p.flat_count,
        "limit_up_count": p.limit_up_count,
        "limit_down_count": p.limit_down_count,
        "max_consecutive_limit_up": p.max_consecutive_limit_up,
        "blow_up_count": p.blow_up_count,
        "blow_up_rate_pct": round(p.blow_up_rate * 100, 2),
        "mini_chart_svg": mini_chart_svg,
        "mini_chart_dates": [d.strftime("%m-%d") for d in series_dates],
        "mini_chart_amount_today": amt_series[-1] if amt_series else None,
        "mini_chart_north_today": north_series[-1] if north_series else None,
    }
    return {
        "key": "smartmoney_evening.e2_pulse", "title": "市场资金水位",
        "order": 2, "type": "sm_market_pulse", "content_json": content,
    }


# ─── E3 / E4: Sector flow in/out (with batched LLM commentary) ────────────────

def _build_flow_section(
    ctx: SMEveningCtx,
    *,
    direction: str,  # "in" | "out"
    flows: list[SectorFlowRow],
    order: int,
) -> dict:
    if not flows:
        return {
            "key": f"smartmoney_evening.e{order}_flow_{direction}",
            "title": ("Top 资金流入板块" if direction == "in" else "Top 资金流出板块"),
            "order": order, "type": "sm_sector_flow",
            "content_json": {
                "direction": direction, "rows": [],
                "fallback_text": "今日无该方向的数据。",
            },
        }

    # Build LLM input for batch commentary
    candidates_blob = "\n".join(
        f"  [{i}] {s.sector_name} ({s.sector_source})  涨幅 {_fmt_pct(s.pct_change)}  "
        f"净流{('入' if direction == 'in' else '出')} "
        f"{_fmt_amt(abs(s.net_amount or 0))};  "
        f"超大单占比 {(s.elg_buy_rate or 0):.2f}%;  "
        f"角色 {s.role or '—'};  周期 {s.cycle_phase or '—'}"
        for i, s in enumerate(flows)
    )
    user = f"""
=== 板块净流{('入' if direction == 'in' else '出')} Top {len(flows)} ===
{candidates_blob}

=== 任务 ===
{prompts.FLOW_COMMENTARY_INSTRUCTIONS}

=== 输出 schema ===
{prompts.FLOW_COMMENTARY_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1500,
    )
    moid = _persist_model_output(
        ctx, section_key=f"smartmoney_evening.e{order}_flow_{direction}",
        prompt_name=f"smartmoney_evening.flow_{direction}_commentary",
        parsed=parsed, resp=resp, status=status,
    )

    overall = ""
    by_idx: dict[int, dict[str, Any]] = {}
    if isinstance(parsed, dict):
        overall = parsed.get("overall_commentary", "")
        for r in parsed.get("results", []) or []:
            if isinstance(r, dict) and "candidate_index" in r:
                by_idx[int(r["candidate_index"])] = r

    rows = []
    for i, s in enumerate(flows):
        comm = by_idx.get(i, {})
        rows.append({
            "sector_name": s.sector_name,
            "sector_code": s.sector_code,
            "sector_source": s.sector_source,
            "pct_change": s.pct_change,
            "pct_change_display": _fmt_pct(s.pct_change),
            "net_amount_yi": round((s.net_amount or 0) / 1e8, 2),
            "net_amount_display": _fmt_amt(s.net_amount),
            "elg_buy_rate": s.elg_buy_rate,
            "role": s.role or "—",
            "cycle_phase": s.cycle_phase or "—",
            "commentary": comm.get("sector_commentary"),
            "signal_quality": comm.get("signal_quality"),
        })

    return {
        "key": f"smartmoney_evening.e{order}_flow_{direction}",
        "title": ("Top 资金流入板块" if direction == "in" else "Top 资金流出板块"),
        "order": order, "type": "sm_sector_flow",
        "content_json": {
            "direction": direction,
            "overall_commentary": overall,
            "rows": rows,
        },
        "prompt_name": f"smartmoney_evening.flow_{direction}_commentary",
        "model_output_id": moid,
    }


# ─── E5: Quality flow ────────────────────────────────────────────────────────

def _build_e5_quality(ctx: SMEveningCtx) -> dict:
    rows = [
        {
            "sector_name": s.sector_name, "sector_code": s.sector_code,
            "pct_change": s.pct_change,
            "pct_change_display": _fmt_pct(s.pct_change),
            "net_amount_display": _fmt_amt(s.net_amount),
            "elg_buy_rate": s.elg_buy_rate,
            "role": s.role or "—", "cycle_phase": s.cycle_phase or "—",
        }
        for s in ctx.quality
    ]

    # Batch LLM commentary for each quality row (reuse flow commentary infrastructure)
    quality_commentary: dict[int, str] = {}
    if ctx.quality:
        q_blob = "\n".join(
            f"  [{i}] {s.sector_name}  涨幅 {_fmt_pct(s.pct_change)}  "
            f"净流入 {_fmt_amt(s.net_amount)};  超大单 {(s.elg_buy_rate or 0):.1f}%;  "
            f"角色 {s.role or '—'};  周期 {s.cycle_phase or '—'}"
            for i, s in enumerate(ctx.quality)
        )
        q_user = f"""
=== 高质量流入板块（量价齐升+主线/轮动角色）===
{q_blob}

=== 任务 ===
针对每个板块给出 1-2 句中文解读，说明：①量价关系质量 ②资金性质（超大单高→机构驱动；低→散户/题材） ③周期位置风险提示。
每条解读不超过 40 字，直接作为表格"解读"列展示。

=== 输出 schema ===
{{
  "rows": [
    {{"idx": 0, "commentary": "..."}},
    ...
  ]
}}
"""
        q_parsed, q_resp, q_status = _safe_chat_json(
            ctx.llm, system=prompts.SYSTEM_PERSONA, user=q_user, max_tokens=800,
        )
        _persist_model_output(ctx, section_key="smartmoney_evening.e5_quality",
                              prompt_name="smartmoney_evening.quality_commentary",
                              parsed=q_parsed, resp=q_resp, status=q_status)
        if isinstance(q_parsed, dict):
            for item in q_parsed.get("rows", []):
                if isinstance(item, dict):
                    quality_commentary[item.get("idx", -1)] = item.get("commentary", "")

    for i, row in enumerate(rows):
        row["commentary"] = quality_commentary.get(i, "")

    return {
        "key": "smartmoney_evening.e5_quality", "title": "高质量流入板块",
        "order": 5, "type": "sm_quality_flow",
        "content_json": {
            "kind": "quality",
            "intro": "量价齐升 + 趋势确认 + 角色为主线/中军/轮动/催化的板块。",
            "rows": rows,
            "fallback_text": "今日无符合『高质量流入』标准的板块。",
        },
    }


# ─── E6: Crowding alert (data + LLM risk note) ────────────────────────────────

def _build_e6_crowding(ctx: SMEveningCtx) -> dict:
    rows = [
        {
            "sector_name": s.sector_name, "sector_code": s.sector_code,
            "pct_change": s.pct_change,
            "pct_change_display": _fmt_pct(s.pct_change),
            "net_amount_display": _fmt_amt(s.net_amount),
            "role": s.role or "—", "cycle_phase": s.cycle_phase or "—",
        }
        for s in ctx.crowded
    ]

    summary_text = ""
    risk_items: list[dict[str, Any]] = []
    action_hint = ""
    moid = None

    if ctx.crowded:
        crowded_blob = "\n".join(
            f"  - {s.sector_name}  涨跌 {_fmt_pct(s.pct_change)}  "
            f"角色 {s.role or '—'}  周期 {s.cycle_phase or '—'}"
            for s in ctx.crowded
        )
        user = f"""
=== 拥挤板块列表 ===
{crowded_blob}

=== 当日市场水位 ===
状态={ctx.pulse.market_state}; 涨停={ctx.pulse.limit_up_count}; 炸板率={ctx.pulse.blow_up_rate:.2%}

=== 任务 ===
{prompts.CROWDING_INSTRUCTIONS}

=== 输出 schema ===
{prompts.CROWDING_SCHEMA}
"""
        parsed, resp, status = _safe_chat_json(
            ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1200,
        )
        moid = _persist_model_output(
            ctx, section_key="smartmoney_evening.e6_crowding",
            prompt_name="smartmoney_evening.crowding_risks",
            parsed=parsed, resp=resp, status=status,
        )
        if isinstance(parsed, dict):
            summary_text = parsed.get("summary", "")
            risk_items = parsed.get("risks", []) or []
            action_hint = parsed.get("action_hint", "")

    return {
        "key": "smartmoney_evening.e6_crowding", "title": "拥挤板块预警",
        "order": 6, "type": "sm_quality_flow",
        "content_json": {
            "kind": "crowded",
            "intro": "资金已堆积但价格滞涨/分歧的板块——观察是否进入退潮阶段。",
            "rows": rows,
            "summary": summary_text,
            "risks": risk_items,
            "action_hint": action_hint,
            "fallback_text": "今日无明显拥挤的板块。",
        },
        "prompt_name": "smartmoney_evening.crowding_risks",
        "model_output_id": moid,
    }


# ─── E7: Cycle grid ──────────────────────────────────────────────────────────

def _build_e7_cycle(ctx: SMEveningCtx) -> dict:
    """7×N phase-trajectory matrix with next-day transition probabilities.

    Each row is one active sector; columns are the last N trading days plus
    a 『下个交易日』 prediction column populated from the B5 transition matrix.
    """
    # Leader lookup: sector_name → leader stock name (best effort)
    leader_by_name: dict[str, str] = {}
    for st in ctx.structures:
        top = (st.leader or st.vanguard or
               (st.core_troops[0] if st.core_troops else None))
        if top and st.sector_name:
            leader_by_name[st.sector_name] = top.get("name", "")
    trajectory_names = [t.sector_name for t in ctx.cycle_trajectory
                         if t.sector_name not in leader_by_name]
    if trajectory_names and ctx.used_trade_date:
        from .data import _load_kpl_stocks_for_sectors
        kpl_map = _load_kpl_stocks_for_sectors(ctx.engine, ctx.used_trade_date, trajectory_names)
        for sn, stocks in kpl_map.items():
            if stocks and sn not in leader_by_name:
                leader_by_name[sn] = stocks[0]["name"]

    rows: list[dict[str, Any]] = []
    for traj in ctx.cycle_trajectory:
        # Predict next phase using B5 transition matrix
        next_top: list[dict[str, Any]] = []
        if ctx.transition_model is not None:
            pred = ctx.transition_model.predict(
                sector_code=traj.sector_code,
                sector_source=traj.sector_source,
                current_phase=traj.current_phase,
            )
            ranked = sorted(pred.distribution.items(), key=lambda kv: -kv[1])
            for phase, prob in ranked[:3]:
                if prob > 0.005:  # filter near-zero noise
                    next_top.append({
                        "phase": phase,
                        "prob": round(prob, 3),
                        "prob_pct": round(prob * 100, 1),
                    })

        rows.append({
            "sector_name": traj.sector_name,
            "sector_source": traj.sector_source,
            "role": traj.role,
            "current_phase": traj.current_phase,
            "phase_history": traj.phase_history,
            "leader_name": leader_by_name.get(traj.sector_name, ""),
            "heat_score": round(traj.heat_score, 3) if traj.heat_score is not None else None,
            "next_top": next_top,
        })

    trade_dates_disp = [td.strftime("%m-%d") for td in (ctx.cycle_trajectory[0].trade_dates if ctx.cycle_trajectory else [])]

    return {
        "key": "smartmoney_evening.e7_cycle", "title": "板块情绪周期轨迹",
        "order": 7, "type": "sm_cycle_grid",
        "content_json": {
            "intro": "活跃板块过去 N 个交易日的相位轨迹；最右列为下个交易日的转移概率（B5 经验矩阵 + 板块 Bayes 后验）。",
            "phases": PHASES,
            "trade_dates_disp": trade_dates_disp,
            "rows": rows,
            "fallback_text": "今日无活跃情绪周期信号。",
        },
    }


# ─── E8: Tomorrow targets (LLM-driven) ────────────────────────────────────────

def _build_e8_targets(ctx: SMEveningCtx) -> dict:
    if not ctx.target_pool:
        return {
            "key": "smartmoney_evening.e8_targets", "title": "明日资金候选",
            "order": 8, "type": "sm_tomorrow_targets",
            "content_json": {
                "summary": "今日活跃板块池为空，明日候选不足以形成。",
                "targets": [],
                "fallback_text": "—",
            },
        }

    pool_blob_lines = []
    for tgt in ctx.target_pool:
        leader_str = ", ".join(
            f"{l.get('name','—')}({l.get('role','—')})" for l in (tgt.leaders or [])
        )
        pool_blob_lines.append(
            f"  - {tgt.sector_name} ({tgt.sector_source}, code={tgt.sector_code})  "
            f"角色={tgt.role}  周期={tgt.cycle_phase}  "
            f"heat={tgt.heat_score:.2f}  trend={tgt.trend_score:.2f}  "
            f"persist={tgt.persistence_score:.2f}  crowd={tgt.crowding_score:.2f}  "
            f"龙头={leader_str or '—'}"
            if all(v is not None for v in (tgt.heat_score, tgt.trend_score,
                                            tgt.persistence_score, tgt.crowding_score))
            else f"  - {tgt.sector_name} 角色={tgt.role} 周期={tgt.cycle_phase}"
        )
    user = f"""
=== 今日活跃板块池 ===
{chr(10).join(pool_blob_lines)}

=== 当前市场状态 ===
{ctx.pulse.market_state}

=== 任务 ===
{prompts.TOMORROW_TARGETS_INSTRUCTIONS}

=== 输出 schema ===
{prompts.TOMORROW_TARGETS_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=2400,
    )
    moid = _persist_model_output(
        ctx, section_key="smartmoney_evening.e8_targets",
        prompt_name="smartmoney_evening.tomorrow_targets",
        parsed=parsed, resp=resp, status=status,
    )
    if not isinstance(parsed, dict):
        parsed = {"summary": "LLM 输出不可用。", "targets": []}
        ctx.run.fallback_used = True
    return {
        "key": "smartmoney_evening.e8_targets", "title": "明日资金候选",
        "order": 8, "type": "sm_tomorrow_targets",
        "content_json": parsed,
        "prompt_name": "smartmoney_evening.tomorrow_targets",
        "model_output_id": moid,
    }


# ─── E9: Sector internal structure ────────────────────────────────────────────

def _build_e9_structure(ctx: SMEveningCtx) -> dict:
    rows = []
    for st in ctx.structures:
        rows.append({
            "sector_name": st.sector_name,
            "sector_code": st.sector_code,
            "sector_source": st.sector_source,
            "role": st.role, "cycle_phase": st.cycle_phase,
            "leader": st.leader,
            "core_troops": st.core_troops,
            "vanguard": st.vanguard,
        })
    return {
        "key": "smartmoney_evening.e9_structure", "title": "重点板块内部结构",
        "order": 9, "type": "sm_sector_structure",
        "content_json": {
            "intro": "活跃板块的内部资金结构：龙头 / 中军 / 情绪先锋。",
            "rows": rows,
            "fallback_text": "今日无活跃板块结构数据。",
        },
    }


# ─── E10: Candidate pool (reuse _candidate_pool partial style — but custom) ────

def _build_e10_candidates(ctx: SMEveningCtx) -> dict:
    fillers = [c for c in ctx.candidates if c.role == "补涨"]
    trending = [c for c in ctx.candidates if c.role == "趋势"]

    def _fmt(c: CandidateStock) -> dict[str, Any]:
        ev = c.evidence or {}
        return {
            "stock_code": c.ts_code, "stock_name": c.name or "—",
            "layer_id": c.role,
            "setup_logic": (
                f"sector={c.primary_sector_name or '—'}; "
                f"主题={c.theme or '—'}"
            ),
            "trigger_condition": (
                f"今日涨幅 {_fmt_pct(c.pct_chg_today)};  评分 {c.score:.3f}"
                if c.role == "补涨"
                else f"5日上涨天数 {ev.get('up_days_5d','—')};  量比 {ev.get('vol_ratio_today','—')}"
            ),
            "failure_condition": (
                "若主板块切换至退潮 / 龙头分歧 → 失效"
                if c.role == "补涨"
                else "若 5 日上涨天数跌破 3 → 失效"
            ),
            "risk_note": "样本较小，需配合主板块共振验证；不构成买卖建议。",
            "signal_strength": "high" if c.score >= 0.75 else ("medium" if c.score >= 0.55 else "low"),
        }

    cards = [_fmt(c) for c in (fillers[:8] + trending[:8])]
    return {
        "key": "smartmoney_evening.e10_candidates", "title": "候选股票池",
        "order": 10, "type": "candidate_pool",
        "content_json": {
            "candidates": cards,
            "fallback_text": "今日无符合候选标准的股票。",
        },
    }


# ─── E11: Strategy view ───────────────────────────────────────────────────────

def _build_e11_strategy(ctx: SMEveningCtx) -> dict:
    market_blob = (
        f"市场状态={ctx.pulse.market_state}; 60d 分位={ctx.pulse.amount_percentile_60d:.2f};\n"
        f"涨停={ctx.pulse.limit_up_count}; 炸板率={ctx.pulse.blow_up_rate:.2%}"
    )
    quality_blob = ", ".join(s.sector_name for s in ctx.quality[:5]) or "(无)"
    crowded_blob = ", ".join(s.sector_name for s in ctx.crowded[:5]) or "(无)"
    cycle_summary = "; ".join(
        f"{c.sector_name}={c.cycle_phase}" for c in ctx.cycle_rows[:8]
    ) or "(无活跃周期)"
    flow_in_top = ", ".join(s.sector_name for s in ctx.flow_in[:5]) or "(无)"

    user = f"""
=== 市场水位 ===
{market_blob}

=== 高质量流入板块 Top ===
{quality_blob}

=== 拥挤板块 Top ===
{crowded_blob}

=== 板块情绪周期 ===
{cycle_summary}

=== 资金流入 Top ===
{flow_in_top}

=== 任务 ===
{prompts.STRATEGY_VIEW_INSTRUCTIONS}

=== 输出 schema ===
{prompts.STRATEGY_VIEW_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(
        ctx, section_key="smartmoney_evening.e11_strategy",
        prompt_name="smartmoney_evening.strategy_view",
        parsed=parsed, resp=resp, status=status,
    )
    if not isinstance(parsed, dict):
        parsed = {"stances": [], "closing_note": "LLM 不可用，跳过策略视角。"}
        ctx.run.fallback_used = True
    return {
        "key": "smartmoney_evening.e11_strategy", "title": "策略观察",
        "order": 11, "type": "sm_strategy_view",
        "content_json": parsed,
        "prompt_name": "smartmoney_evening.strategy_view",
        "model_output_id": moid,
    }


# ─── E12: Validation points (sets up tomorrow's hypotheses) ───────────────────

def _build_e12_validation(ctx: SMEveningCtx) -> tuple[dict, list[dict[str, Any]]]:
    """Returns (section, hypotheses_list_for_db).

    The hypotheses are persisted as report_judgments rows with
    judgment_type='hypothesis'.
    """
    # Re-feed the LLM with full report context (E1+E2+E5+E6+E7+E8+E11 abstracted)
    pulse_blob = (
        f"市场状态={ctx.pulse.market_state}; 60d 分位={ctx.pulse.amount_percentile_60d:.2f}; "
        f"涨停={ctx.pulse.limit_up_count}; 炸板率={ctx.pulse.blow_up_rate:.2%}"
    )
    cycle_blob = "; ".join(f"{c.sector_name}={c.cycle_phase}" for c in ctx.cycle_rows[:8]) or "(无)"
    quality_blob = ", ".join(s.sector_name for s in ctx.quality[:6]) or "(无)"
    crowded_blob = ", ".join(s.sector_name for s in ctx.crowded[:5]) or "(无)"
    targets_blob = ", ".join(t.sector_name for t in ctx.target_pool[:6]) or "(无)"

    user = f"""
=== 市场水位 ===
{pulse_blob}

=== 板块情绪周期 ===
{cycle_blob}

=== 高质量流入 ===
{quality_blob}

=== 拥挤板块 ===
{crowded_blob}

=== 明日候选板块池 ===
{targets_blob}

=== 任务 ===
{prompts.VALIDATION_INSTRUCTIONS}

=== 输出 schema ===
{prompts.VALIDATION_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(
        ctx, section_key="smartmoney_evening.e12_validation",
        prompt_name="smartmoney_evening.validation_points",
        parsed=parsed, resp=resp, status=status,
    )

    hyps_for_db: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    if isinstance(parsed, dict):
        for h in parsed.get("hypotheses", []) or []:
            if not isinstance(h, dict):
                continue
            items.append({
                "hypothesis": h.get("hypothesis_text", ""),
                "related": h.get("target", ""),
                "review_rule": h.get("validation_method", ""),
                "confidence": h.get("confidence", "medium"),
                "horizon": h.get("horizon", "next_day"),
            })
            hyps_for_db.append({
                "judgment_text": h.get("hypothesis_text", ""),
                "target": h.get("target"),
                "horizon": h.get("horizon", "next_day"),
                "validation_method": h.get("validation_method"),
                "confidence": h.get("confidence", "medium"),
            })

    if not items:
        items = [{"hypothesis": "今日 LLM 未生成可沉淀假设。",
                  "related": "—", "review_rule": "—",
                  "confidence": "low", "horizon": "next_day"}]
        ctx.run.fallback_used = True

    section = {
        "key": "smartmoney_evening.e12_validation", "title": "明日验证点",
        "order": 12, "type": "hypotheses_list",
        "content_json": {"hypotheses": items},
        "prompt_name": "smartmoney_evening.validation_points",
        "model_output_id": moid,
    }
    return section, hyps_for_db


# ─── E13: Review (yesterday's hypotheses) ─────────────────────────────────────

def _build_e13_review(ctx: SMEveningCtx) -> dict:
    if not ctx.yesterday_hypotheses:
        return {
            "key": "smartmoney_evening.e13_review", "title": "昨日假设复盘",
            "order": 13, "type": "review_table",
            "content_json": {
                "rows": [],
                "fallback_text": "无可复盘的昨日假设（首次运行或近 4 个交易日无晚报）。",
            },
        }

    hyp_blob = "\n".join(
        f"  [{i}] target={h['target']}; horizon={h['horizon']}; "
        f"validation={h['validation_method']}; "
        f"hypothesis_text={h['hypothesis']}"
        for i, h in enumerate(ctx.yesterday_hypotheses)
    )
    outcome_blob = json.dumps(ctx.today_outcome, ensure_ascii=False, indent=2)

    user = f"""
=== 昨日假设 ===
{hyp_blob}

=== 今日实际表现快照 ===
{outcome_blob}

=== 任务 ===
{prompts.REVIEW_INSTRUCTIONS}

=== 输出 schema ===
{prompts.REVIEW_SCHEMA}
"""
    parsed, resp, status = _safe_chat_json(
        ctx.llm, system=prompts.SYSTEM_PERSONA, user=user, max_tokens=1800,
    )
    moid = _persist_model_output(
        ctx, section_key="smartmoney_evening.e13_review",
        prompt_name="smartmoney_evening.hypothesis_review",
        parsed=parsed, resp=resp, status=status,
    )

    rows = []
    summary_text = ""
    if isinstance(parsed, dict):
        summary_text = parsed.get("summary", "")
        results = parsed.get("results", []) or []
        result_by_idx = {int(r["hypothesis_index"]): r for r in results
                         if isinstance(r, dict) and "hypothesis_index" in r}
        for i, h in enumerate(ctx.yesterday_hypotheses):
            r = result_by_idx.get(i, {})
            rows.append({
                "hypothesis": h["hypothesis"],
                "review_result": r.get("review_result", "not_applicable"),
                "review_result_display": r.get("review_result_display", "不适用"),
                "evidence_text": r.get("evidence_text", ""),
                "lesson": r.get("lesson", ""),
            })

    return {
        "key": "smartmoney_evening.e13_review", "title": "昨日假设复盘",
        "order": 13, "type": "review_table",
        "content_json": {"rows": rows, "summary": summary_text},
        "prompt_name": "smartmoney_evening.hypothesis_review",
        "model_output_id": moid,
    }


# ─── E14: Disclaimer ──────────────────────────────────────────────────────────

def _build_e14_disclaimer() -> dict:
    return {
        "key": "smartmoney_evening.e14_disclaimer", "title": "免责声明",
        "order": 14, "type": "disclaimer",
        "content_json": {
            "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH,
            "paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
        },
    }


# ── Main entrypoint ──────────────────────────────────────────────────────────

def run_smartmoney_evening(
    *,
    report_date: dt.date,
    data_cutoff_at: dt.datetime,
    triggered_by: str | None = None,
    on_log: Callable[[str], None] = lambda m: None,
) -> Path:
    """Render a full SmartMoney evening report end-to-end. Returns saved Path."""
    settings = get_settings()
    engine = get_engine(settings)
    llm = LLMClient(settings)

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
    on_log(f"[run {str(run.report_run_id)[:8]}] starting SmartMoney evening for {report_date}")

    try:
        # Resolve actual trade date (handle weekends / holidays)
        used_td = data.find_latest_trade_date(engine, on_or_before=report_date)
        if used_td is None:
            on_log("⚠️  no market_state_daily data found; report will use empty fallbacks")
            used_td = report_date
        elif used_td != report_date:
            on_log(f"  using nearest trade date {used_td} (report_date={report_date} not a trading day)")

        # Pre-load all data
        on_log("loading data...")
        pulse = data.load_market_pulse(engine, used_td)
        flow_in = data.load_sector_flows(engine, used_td, direction="in", top_n=10)
        flow_out = data.load_sector_flows(engine, used_td, direction="out", top_n=10)
        quality = data.load_quality_flows(engine, used_td, top_n=8)
        crowded = data.load_crowded_sectors(engine, used_td, top_n=6)
        cycle_rows = data.load_cycle_grid(engine, used_td, top_n=25)
        cycle_trajectory = data.load_cycle_trajectory(engine, used_td, n_days=5, top_n=18)
        pulse_series = data.load_amount_north_series(engine, used_td, n_days=10)
        target_pool = data.load_tomorrow_target_pool(engine, used_td, top_n=8)
        structures = data.load_sector_structures(engine, used_td, top_n_sectors=6)
        candidates = data.load_candidate_pool(engine, used_td, fillers_n=12, trending_n=12)
        yesterday = data.load_yesterday_hypotheses(engine, report_date=report_date)
        outcome = data.load_today_outcome_for_review(engine, used_td)

        # Fit the B5 transition matrix once for reuse across §07 predictions
        try:
            transition_model = TransitionMatrixModel.fit(
                engine, lookback_days=180, end_date=used_td,
            )
        except Exception as exc:  # noqa: BLE001
            on_log(f"  ⚠️ transition_matrix.fit failed: {exc}")
            transition_model = None

        ctx = SMEveningCtx(
            engine=engine, llm=llm, run=run,
            pulse=pulse, flow_in=flow_in, flow_out=flow_out,
            quality=quality, crowded=crowded, cycle_rows=cycle_rows,
            cycle_trajectory=cycle_trajectory, transition_model=transition_model,
            pulse_series=pulse_series,
            target_pool=target_pool, structures=structures, candidates=candidates,
            yesterday_hypotheses=yesterday, today_outcome=outcome,
            used_trade_date=used_td, on_log=on_log,
        )
        on_log(f"  pulse={pulse.market_state}; flow_in={len(flow_in)}; flow_out={len(flow_out)}; "
               f"quality={len(quality)}; crowded={len(crowded)}; cycle={len(cycle_rows)}; "
               f"targets={len(target_pool)}; structures={len(structures)}; "
               f"cands={len(candidates)}; yesterday_hyps={len(yesterday)}")

        sections: list[dict[str, Any]] = []
        e12_hyps_for_db: list[dict[str, Any]] = []

        for label, builder in [
            ("E1 tone",         lambda: _build_e1_tone(ctx)),
            ("E2 pulse",        lambda: _build_e2_pulse(ctx)),
            ("E3 flow_in",      lambda: _build_flow_section(ctx, direction="in", flows=flow_in, order=3)),
            ("E4 flow_out",     lambda: _build_flow_section(ctx, direction="out", flows=flow_out, order=4)),
            ("E5 quality",      lambda: _build_e5_quality(ctx)),
            ("E6 crowding",     lambda: _build_e6_crowding(ctx)),
            ("E7 cycle",        lambda: _build_e7_cycle(ctx)),
            ("E8 targets",      lambda: _build_e8_targets(ctx)),
            ("E9 structure",    lambda: _build_e9_structure(ctx)),
            ("E10 candidates",  lambda: _build_e10_candidates(ctx)),
            ("E11 strategy",    lambda: _build_e11_strategy(ctx)),
        ]:
            t0 = time.monotonic()
            on_log(f"building {label}…")
            sec = builder()
            sections.append(sec)
            insert_section(
                engine, report_run_id=run.report_run_id,
                section_key=sec["key"], section_title=sec["title"],
                section_order=sec["order"],
                content_json=sec["content_json"],
                prompt_name=sec.get("prompt_name"),
                prompt_version=prompts.PROMPT_BUNDLE_VERSION,
                model_output_id=sec.get("model_output_id"),
                fallback_used=sec.get("fallback_used", False),
            )
            on_log(f"  {label} done in {time.monotonic()-t0:.1f}s")

        # E12 validation — also persists each hypothesis as a report_judgment
        on_log("building E12 validation…")
        t0 = time.monotonic()
        e12, e12_hyps_for_db = _build_e12_validation(ctx)
        sections.append(e12)
        insert_section(
            engine, report_run_id=run.report_run_id,
            section_key=e12["key"], section_title=e12["title"], section_order=e12["order"],
            content_json=e12["content_json"],
            prompt_name=e12.get("prompt_name"),
            prompt_version=prompts.PROMPT_BUNDLE_VERSION,
            model_output_id=e12.get("model_output_id"),
        )
        for h in e12_hyps_for_db:
            try:
                insert_judgment(
                    engine, report_run_id=run.report_run_id,
                    section_key=e12["key"],
                    judgment_type="hypothesis",
                    judgment_text=h["judgment_text"],
                    target=h.get("target") or "—",
                    horizon=h.get("horizon") or "next_day",
                    confidence=(h.get("confidence") or "medium").lower(),
                    validation_method=h.get("validation_method"),
                )
            except Exception:
                pass
        on_log(f"  E12 done in {time.monotonic()-t0:.1f}s ({len(e12_hyps_for_db)} hypotheses)")

        # E13 review + E14 disclaimer
        for label, builder in [
            ("E13 review",     lambda: _build_e13_review(ctx)),
            ("E14 disclaimer", _build_e14_disclaimer),
        ]:
            t0 = time.monotonic()
            on_log(f"building {label}…")
            sec = builder()
            sections.append(sec)
            insert_section(
                engine, report_run_id=run.report_run_id,
                section_key=sec["key"], section_title=sec["title"],
                section_order=sec["order"],
                content_json=sec["content_json"],
                prompt_name=sec.get("prompt_name"),
                prompt_version=prompts.PROMPT_BUNDLE_VERSION,
                model_output_id=sec.get("model_output_id"),
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
        "title": f"中国 SmartMoney 晚盘报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": "China A-Share Smart Money Evening Briefing — Lindenwood Management LLC",
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
    fname = f"CN_smartmoney_evening_{run.report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
