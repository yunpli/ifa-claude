"""Scenario-tree synthesis for Stock Edge reports.

The scenario tree is a report-layer execution aid: it turns the numeric trade
plan, prediction surface, and strategy clusters into falsifiable branches. Any
LLM rewrite must preserve these structured numbers; the deterministic builder
is the production fallback and is safe for tests/offline runs.
"""
from __future__ import annotations

from typing import Any


def build_scenario_tree(
    *,
    prediction_context: dict[str, Any],
    price_context: dict[str, Any],
    strategy_matrix: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Build a Chinese execution scenario tree from structured report inputs."""
    today = prediction_context.get("today_entry") or {}
    best = prediction_context.get("best_opportunity") or {}
    stop_price = today.get("stop_price") or ((plan.get("stop") or {}).get("price"))
    next_5d = list(prediction_context.get("next_5d") or [])
    sell_targets = list(prediction_context.get("sell_targets") or [])
    clusters = list(strategy_matrix.get("cluster_plans") or [])
    top_positive, top_negative = _top_drivers(strategy_matrix)
    primary_target = _primary_target(best, sell_targets)

    branches = []
    if today.get("available"):
        branches.append(
            {
                "key": "today_execute",
                "label": "今日执行",
                "probability_label": _probability_label(prediction_context.get("entry_fill_probability")),
                "condition": f"价格落在 {today.get('entry_low'):.4f}-{today.get('entry_high'):.4f}，且收盘不跌破执行逻辑。",
                "action": "按计划分批买入；若盘中快速拉离区间，不追高。",
                "entry_low": today.get("entry_low"),
                "entry_high": today.get("entry_high"),
                "target_price": primary_target.get("price"),
                "target_label": primary_target.get("label"),
                "stop_price": stop_price,
                "watch": _join_nonempty(top_positive[:2], fallback=today.get("rule") or "观察成交与承接质量。"),
            }
        )
    else:
        branches.append(
            {
                "key": "today_wait",
                "label": "今日等待",
                "probability_label": "条件未满足",
                "condition": prediction_context.get("decision") or "今日不形成主动买点。",
                "action": "不主动买入；等待未来 5 日条件单或重新生成报告。",
                "entry_low": None,
                "entry_high": None,
                "target_price": primary_target.get("price"),
                "target_label": primary_target.get("label"),
                "stop_price": stop_price,
                "watch": _join_nonempty(top_negative[:2], fallback="等待策略矩阵回到观察线以上。"),
            }
        )

    for idx, row in enumerate(next_5d[:3], start=1):
        branches.append(
            {
                "key": f"next5_{idx}",
                "label": row.get("scenario") or f"未来5日路径{idx}",
                "probability_label": row.get("priority") or "条件触发",
                "condition": row.get("condition") or "等待价格触发。",
                "action": "触发后按买入带执行；未触发则放弃，不为成交而降级标准。",
                "entry_low": row.get("entry_low"),
                "entry_high": row.get("entry_high"),
                "target_price": primary_target.get("price"),
                "target_label": primary_target.get("label"),
                "stop_price": row.get("stop_price") or stop_price,
                "watch": _cluster_watch(clusters, row.get("scenario")),
            }
        )

    branches.append(
        {
            "key": "invalidate",
            "label": "失效/回避",
            "probability_label": _probability_label(prediction_context.get("stop_first_probability")),
            "condition": _invalidate_condition(prediction_context, stop_price),
            "action": "取消买入计划；已持仓按失效线纪律处理，不用摊薄替代止损。",
            "entry_low": None,
            "entry_high": None,
            "target_price": None,
            "target_label": None,
            "stop_price": stop_price,
            "watch": _join_nonempty(top_negative[:3], fallback="跌破关键支撑、资金转弱或风险簇主导。"),
        }
    )

    return {
        "available": bool(branches),
        "model_used": "structured_scenario_tree_v1",
        "llm_tool": "ifa.core.llm.LLMClient",
        "note": "场景树由结构化数值生成；LLM 只能做表述压缩，不允许改写价格、概率或止损。",
        "summary": _summary(prediction_context, primary_target, top_positive, top_negative),
        "branches": branches,
        "drivers": {
            "positive": top_positive,
            "negative": top_negative,
            "clusters": [
                {
                    "label": row.get("cluster_label"),
                    "action": row.get("action_label"),
                    "score": row.get("display_score"),
                    "signals": row.get("top_signals") or [],
                }
                for row in clusters[:6]
            ],
        },
        "risk_controls": list(prediction_context.get("vetoes") or [])[:5],
    }


def _primary_target(best: dict[str, Any], sell_targets: list[dict[str, Any]]) -> dict[str, Any]:
    if best:
        return {"label": best.get("label"), "price": best.get("target_price"), "return_pct": best.get("return_pct")}
    if sell_targets:
        row = sell_targets[0]
        return {"label": row.get("label"), "price": row.get("price"), "return_pct": row.get("return_pct")}
    return {"label": "目标价", "price": None, "return_pct": None}


def _top_drivers(strategy_matrix: dict[str, Any]) -> tuple[list[str], list[str]]:
    signals = list(strategy_matrix.get("signals") or [])
    active = [s for s in signals if s.get("status") != "missing"]
    positive = sorted([s for s in active if float(s.get("score") or 0) > 0], key=lambda s: float(s.get("score") or 0), reverse=True)
    negative = sorted([s for s in active if float(s.get("score") or 0) < 0], key=lambda s: float(s.get("score") or 0))
    return (
        [f"{s.get('name')}：{s.get('evidence')}" for s in positive[:5]],
        [f"{s.get('name')}：{s.get('evidence')}" for s in negative[:5]],
    )


def _probability_label(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "未校准"


def _cluster_watch(clusters: list[dict[str, Any]], scenario: Any) -> str:
    label = str(scenario or "")
    if "突破" in label:
        candidates = [c for c in clusters if c.get("cluster") in {"trend_breakout", "order_flow_smart_money", "sw_l2_sector_leadership"}]
    elif "回踩" in label:
        candidates = [c for c in clusters if c.get("cluster") in {"pullback_continuation", "fundamentals_quality", "model_ensemble"}]
    else:
        candidates = clusters[:3]
    parts = []
    for row in candidates[:2]:
        parts.append(f"{row.get('cluster_label')} {row.get('action_label')}，分数 {float(row.get('display_score') or 0):.2f}")
    return "；".join(parts) or "观察策略簇是否维持正向。"


def _invalidate_condition(prediction_context: dict[str, Any], stop_price: Any) -> str:
    base = f"跌破止损/失效价 {float(stop_price):.4f}" if stop_price else "跌破最新失效线"
    return f"{base}，或策略矩阵总分低于观察线，或先止损概率继续抬升。"


def _summary(prediction_context: dict[str, Any], target: dict[str, Any], positive: list[str], negative: list[str]) -> str:
    decision = prediction_context.get("decision") or "今日无结论"
    target_label = target.get("label") or "目标"
    target_price = target.get("price")
    target_text = f"{target_label} {float(target_price):.4f}" if target_price is not None else target_label
    pos = positive[0] if positive else "缺少强正向驱动"
    neg = negative[0] if negative else "暂无强硬负向信号"
    return f"{decision}；主要目标为 {target_text}。核心顺风是 {pos}；核心约束是 {neg}。"


def _join_nonempty(items: list[str], *, fallback: str) -> str:
    cleaned = [item for item in items if item]
    return "；".join(cleaned) if cleaned else fallback
