"""Build renderable Stock Edge report models."""
from __future__ import annotations

from typing import Any

from ifa.core.report.disclaimer import (
    DISCLAIMER_PARAGRAPHS_EN,
    DISCLAIMER_PARAGRAPHS_ZH,
    FOOTER_SHORT_EN,
    FOOTER_SHORT_ZH,
    SHORT_HEADER_EN,
    SHORT_HEADER_ZH,
)
from ifa.families.stock.analysis import StockEdgeAnalysis
from ifa.families.stock.decision_layer import build_decision_layer
from ifa.families.stock.features import build_support_resistance, compute_technical_summary
from ifa.families.stock.features.support_resistance import nearest_resistance, nearest_support
from ifa.families.stock.report.charts import build_chart_context, build_peer_context_charts, build_peer_fundamental_chart
from ifa.families.stock.report.scenario_tree import build_scenario_tree
from ifa.families.stock.strategies import compute_strategy_matrix

_ACTION_LABELS = {
    "buy": "买入",
    "watch": "观察",
    "avoid": "回避",
    "exit": "退出",
    "update": "更新",
}

_CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

_MODE_LABELS = {
    "quick": "快速分析",
    "deep": "深度分析",
    "update": "更新分析",
}

_DATA_NAME_LABELS = {
    "daily_bars": "日线行情",
    "daily_basic": "每日基本面",
    "moneyflow": "资金流",
    "sector_membership": "申万行业归属",
    "ta_context": "技术分析上下文",
    "research_lineup": "财报研究阵列",
    "model_context": "既有模型信号",
    "intraday_5min": "5分钟行情",
}

_STATUS_LABELS = {
    "ok": "正常",
    "partial": "部分可用",
    "missing": "缺失",
    "stale": "过期",
}

_SOURCE_LABELS = {
    "postgres": "本地 PostgreSQL",
    "duckdb": "本地 DuckDB",
    "parquet": "本地 Parquet",
    "tushare_backfill": "TuShare 回填",
    "missing": "未取得",
}

_LEVEL_SOURCE_LABELS = {
    "20d_low": "20日低点",
    "20d_high": "20日高点",
    "swing_low": "摆动低点",
    "swing_high": "摆动高点",
    "ma20": "20日均线",
    "ma60": "60日均线",
}

_LEVEL_KIND_LABELS = {
    "support": "支撑",
    "resistance": "压力",
}


def build_report_model(analysis: StockEdgeAnalysis) -> dict[str, Any]:
    plan = analysis.plan
    ctx = analysis.ctx
    plan_dict = plan.to_dict()
    plan_dict["action_label"] = _ACTION_LABELS.get(plan.action, plan.action)
    plan_dict["confidence_label"] = _CONFIDENCE_LABELS.get(plan.confidence, plan.confidence)
    strategy_matrix = compute_strategy_matrix(analysis.snapshot)
    decision_layer = analysis.decision_layer or build_decision_layer(
        analysis.snapshot,
        plan,
        strategy_matrix=strategy_matrix,
    )
    price_context = _build_price_context(analysis)
    prediction_context = _build_prediction_context(analysis, price_context, strategy_matrix)
    return {
        "product": "个股作战室",
        "ts_code": ctx.request.ts_code,
        "stock_name": _target_stock_name(analysis),
        "mode": ctx.request.mode,
        "mode_label": _MODE_LABELS.get(ctx.request.mode, ctx.request.mode),
        "run_mode": ctx.request.run_mode,
        "template_version": "stock_edge_v2.2",
        "as_of_trade_date": ctx.as_of.as_of_trade_date,
        "data_cutoff_at_bjt": ctx.as_of.data_cutoff_at_bjt,
        "as_of_rule": ctx.as_of.rule,
        "disclaimer": _build_disclaimer_context(),
        "param_hash": ctx.param_hash,
        "freshness": [_decorate_freshness(item) for item in analysis.snapshot.freshness],
        "degraded_reasons": analysis.snapshot.degraded_reasons,
        "record_status_degraded_reasons": analysis.snapshot.record_status_degraded_reasons,
        "price_context": price_context,
        "chart_context": build_chart_context(
            analysis.snapshot.daily_bars.require(),
            price_levels=price_context.get("levels") or [],
        ),
        "strategy_matrix": strategy_matrix,
        "decision_layer": decision_layer,
        "prediction_context": prediction_context,
        "scenario_tree": build_scenario_tree(
            prediction_context=prediction_context,
            price_context=price_context,
            strategy_matrix=strategy_matrix,
            plan=plan_dict,
        ),
        "strategy_validation": _build_strategy_validation(analysis),
        "sector_leaders": _build_sector_leaders_context(analysis),
        "plan": plan_dict,
    }


def _decorate_freshness(item: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(item)
    decorated["display_name"] = _DATA_NAME_LABELS.get(str(item.get("name")), item.get("name"))
    decorated["status_label"] = _STATUS_LABELS.get(str(item.get("status")), item.get("status"))
    decorated["source_label"] = _SOURCE_LABELS.get(str(item.get("source")), item.get("source"))
    return decorated


def _build_price_context(analysis: StockEdgeAnalysis) -> dict[str, Any]:
    daily = analysis.snapshot.daily_bars.require()
    tech = compute_technical_summary(daily)
    levels = build_support_resistance(daily)
    support = nearest_support(levels, tech.close)
    resistance = nearest_resistance(levels, tech.close)
    ordered_levels = sorted(levels, key=lambda lvl: (lvl.kind != "support", abs(lvl.distance_pct)))
    return {
        "close": tech.close,
        "ma5": tech.ma5,
        "ma20": tech.ma20,
        "ma60": tech.ma60,
        "atr14": tech.atr14,
        "recent_20d_high": _window_extreme(daily, 20, "high", "max"),
        "recent_20d_low": _window_extreme(daily, 20, "low", "min"),
        "recent_60d_high": _window_extreme(daily, 60, "high", "max"),
        "recent_60d_low": _window_extreme(daily, 60, "low", "min"),
        "nearest_support": _level_to_dict(support),
        "nearest_resistance": _level_to_dict(resistance),
        "levels": [_level_to_dict(level) for level in ordered_levels[:8] if level is not None],
    }


def _window_extreme(df, window: int, column: str, op: str) -> float | None:
    if column not in df.columns or df.empty:
        return None
    tail = df.sort_values("trade_date").tail(min(window, len(df)))
    value = tail[column].max() if op == "max" else tail[column].min()
    return float(value)


def _build_prediction_context(
    analysis: StockEdgeAnalysis,
    price_context: dict[str, Any],
    strategy_matrix: dict[str, Any],
) -> dict[str, Any]:
    plan = analysis.plan
    risk = analysis.ctx.params.get("risk", {})
    target_pct = float(risk.get("right_tail_target_pct", 50.0))
    close = price_context.get("close")
    atr = price_context.get("atr14") or (float(close) * 0.03 if close else None)
    support = price_context.get("nearest_support") or {}
    resistance = price_context.get("nearest_resistance") or {}
    entry = plan.entry_zone
    stop = plan.stop
    can_buy_today = bool(plan.action == "buy" and entry and stop)
    can_watch_today = bool(plan.action == "watch" and entry and stop)
    base_entry_high = entry.high if entry else float(close or 0)
    sell_targets = []
    if base_entry_high > 0:
        opportunity_targets = plan.probability.opportunities or []
        target_specs = [(float(row["return_pct"]), str(row["label"]), row) for row in opportunity_targets]
        if not target_specs:
            target_specs = [(20.0, "纪律止盈", None), (30.0, "主目标", None), (target_pct, "右尾目标", None)]
        for pct, label, row in target_specs:
            sell_targets.append(
                {
                    "label": label,
                    "return_pct": pct,
                    "price": row.get("target_price") if row else round(base_entry_high * (1.0 + pct / 100.0), 4),
                    "probability": row.get("probability") if row else None,
                    "horizon_days": row.get("horizon_days") if row else None,
                    "expected_value": row.get("expected_value") if row else None,
                    "target_first_probability": row.get("target_first_probability") if row else None,
                    "stop_first_probability": row.get("stop_first_probability") if row else None,
                    "avg_days_to_target": row.get("avg_days_to_target") if row else None,
                    "avg_days_to_stop": row.get("avg_days_to_stop") if row else None,
                    "is_best": bool(plan.probability.best_opportunity and row and row.get("key") == plan.probability.best_opportunity.get("key")),
                    "rule": _sell_target_rule(row, pct),
                }
            )
    pullback_low = support.get("price")
    breakout_price = resistance.get("price")
    next_5d = []
    if pullback_low and atr:
        next_5d.append(
            {
                "scenario": "回踩买入",
                "condition": "未来 5 个交易日回落到最近支撑区，且没有放量跌破支撑；这是等价格回到安全边际后再买。",
                "entry_low": round(float(pullback_low) - 0.15 * float(atr), 4),
                "entry_high": round(float(pullback_low) + 0.35 * float(atr), 4),
                "stop_price": round(max(0.01, float(pullback_low) - 0.80 * float(atr)), 4),
                "priority": "优先级1：支撑低吸",
            }
        )
    if breakout_price and atr:
        next_5d.append(
            {
                "scenario": "突破回踩买入",
                "condition": "先放量站上压力位，再等回踩压力位不破后买；不是追单根急拉。",
                "entry_low": round(float(breakout_price) - 0.10 * float(atr), 4),
                "entry_high": round(float(breakout_price) + 0.25 * float(atr), 4),
                "stop_price": round(float(breakout_price) - 0.75 * float(atr), 4),
                "priority": "优先级2：确认突破",
            }
        )
    if close and atr:
        next_5d.append(
            {
                "scenario": "现价附近低吸",
                "condition": "仅在策略总分仍为正、盘中回落但收盘守住短期均线时考虑；否则放弃。",
                "entry_low": round(float(close) - 0.50 * float(atr), 4),
                "entry_high": round(float(close) + 0.10 * float(atr), 4),
                "stop_price": round(float(close) - 1.35 * float(atr), 4),
                "priority": "低优先级：只做小仓",
            }
        )
    vetoes = list(plan.vetoes)
    vetoes.extend(
        [
            "高开超过 6% 且未回踩，不追买。",
            "跌破预测止损位后，不用更低价格摊薄。",
            "策略矩阵总分跌破观察线，取消未来 5 日买入计划。",
        ]
    )
    return {
        "as_of_trade_date": analysis.ctx.as_of.as_of_trade_date,
        "decision": "今日可买" if can_buy_today else ("今日观察，不主动买" if can_watch_today else "今日不建议买"),
        "decision_reason": plan.setup_type,
        "today_entry": {
            "available": bool(entry),
            "entry_low": entry.low if entry else None,
            "entry_high": entry.high if entry else None,
            "stop_price": stop.price if stop else None,
            "rule": entry.reason if entry else "没有形成可执行买点。",
        },
        "next_5d": next_5d,
        "sell_targets": sell_targets,
        "best_opportunity": plan.probability.best_opportunity,
        "opportunities": plan.probability.opportunities or [],
        "holding_window": "legacy_audit_only",
        "hit_50_40d_probability": plan.probability.prob_hit_50_40d,
        "hit_30_40d_probability": plan.probability.prob_hit_30_40d,
        "hit_20_40d_probability": plan.probability.prob_hit_20_40d,
        "stop_first_probability": plan.probability.prob_stop_first,
        "entry_fill_probability": plan.probability.entry_fill_probability,
        "return_quantiles": {
            "p10": plan.probability.return_p10_40d,
            "p50": plan.probability.return_p50_40d,
            "p90": plan.probability.return_p90_40d,
        },
        "expected_return_40d": plan.probability.expected_return_40d,
        "expected_drawdown_40d": plan.probability.expected_drawdown_40d,
        "probability_model": plan.probability.model_version,
        "probability_calibrated": plan.probability.calibrated,
        "matrix_score": strategy_matrix.get("aggregate_score"),
        "vetoes": vetoes,
    }


def _sell_target_rule(row: dict[str, Any] | None, pct: float) -> str:
    if not row:
        return f"从实际成交价起算，触及 {pct:.0f}% 收益优先减仓或卖出；主决策以 5/10/20 三周期为准。"
    parts = [
        f"{row.get('horizon_days')} 个交易日内目标收益 {pct:.0f}%",
        f"模型概率 {row.get('probability'):.1%}",
    ]
    if row.get("target_first_probability") is not None:
        parts.append(f"目标先到 {row.get('target_first_probability'):.1%}")
    if row.get("stop_first_probability") is not None:
        parts.append(f"止损先到 {row.get('stop_first_probability'):.1%}")
    if row.get("avg_days_to_target") is not None:
        parts.append(f"目标均 {row.get('avg_days_to_target'):.1f} 天")
    return "，".join(parts) + "。"


def _level_to_dict(level) -> dict[str, Any] | None:
    if level is None:
        return None
    return {
        "price": level.price,
        "kind": level.kind,
        "kind_label": _LEVEL_KIND_LABELS.get(level.kind, level.kind),
        "source": level.source,
        "source_label": _LEVEL_SOURCE_LABELS.get(level.source, level.source),
        "strength": level.strength,
        "distance_pct": level.distance_pct,
    }


def _build_strategy_validation(analysis: StockEdgeAnalysis) -> dict[str, Any]:
    ta = analysis.snapshot.ta_context.data or {}
    metrics = ta.get("setup_metrics") or []
    rows = []
    for item in metrics:
        rows.append(
            {
                "setup_name": item.get("setup_name"),
                "triggers_count": item.get("triggers_count"),
                "winrate_60d": item.get("winrate_60d"),
                "avg_return_60d": item.get("avg_return_60d"),
                "pl_ratio_60d": item.get("pl_ratio_60d"),
                "winrate_250d": item.get("winrate_250d"),
                "decay_score": item.get("decay_score"),
                "combined_score_60d": item.get("combined_score_60d"),
            }
        )
    return {
        "available": bool(rows),
        "scope": "TA setup rolling metrics; not a single-stock standalone backtest.",
        "scope_label": "TA 策略滚动验证，不是本股独立回测",
        "rows": rows,
    }


def _build_sector_leaders_context(analysis: StockEdgeAnalysis) -> dict[str, Any]:
    sector = analysis.snapshot.sector_membership.data or {}
    leaders = sector.get("sector_leaders") or {}
    categories = [
        ("size", "市值龙头", "total_mv", "总市值"),
        ("momentum", "动量龙头", "return_5d_pct", "5日涨跌幅"),
        ("moneyflow", "资金龙头", "net_mf_amount_7d", "近7日净流"),
        ("ta", "TA形态龙头", "ta_score", "TA分数"),
    ]
    rows = []
    for key, label, metric_key, metric_label in categories:
        for rank, item in enumerate(leaders.get(key) or [], start=1):
            rows.append(
                {
                    "category": key,
                    "category_label": label,
                    "rank": rank,
                    "ts_code": item.get("ts_code"),
                    "name": item.get("name"),
                    "is_target": item.get("is_target"),
                    "metric_key": metric_key,
                    "metric_label": metric_label,
                    "metric_value": item.get(metric_key),
                    "close": item.get("close"),
                    "return_5d_pct": item.get("return_5d_pct"),
                    "return_10d_pct": item.get("return_10d_pct"),
                    "return_15d_pct": item.get("return_15d_pct"),
                    "total_mv": item.get("total_mv"),
                    "pe_ttm": item.get("pe_ttm"),
                    "pb": item.get("pb"),
                    "setup_label": item.get("setup_label"),
                    "daily_returns_15d": item.get("daily_returns_15d") or [],
                }
            )
    peer_rows = _unique_peer_rows(rows)
    fundamentals = _build_peer_fundamentals_context(
        sector.get("sector_peers") or peer_rows,
        sector.get("peer_fundamentals") or [],
    )
    return {
        "available": bool(rows),
        "l2_code": sector.get("l2_code"),
        "l2_name": sector.get("l2_name"),
        "rows": rows,
        "peers": peer_rows,
        "charts": {**build_peer_context_charts(peer_rows), "peer_fundamental_svg": build_peer_fundamental_chart(fundamentals.get("rows") or [])},
        "fundamentals": fundamentals,
        "fundamental_trigger_note": "同板块对比以财务报表和 Research deep 因子为主；市值、5/10/15日涨跌幅只作为辅助定位。",
        "peer_selection_notes": {
            "fundamental": (
                "财务质量样本：同一 SW L2 板块内、已落入本地 Research 年报/季报因子的公司；按财务综合分排序，"
                "最多展示 8 家，并强制保留目标股。若样本少，通常是本地财报因子尚未覆盖更多同行。"
            ),
            "trading": (
                "交易位置样本：同一 SW L2 板块内的市值龙头、5日动量龙头、近7日资金龙头和 TA 形态龙头合并去重，"
                "最多展示 12 家，并强制保留目标股。"
            ),
            "daily_returns": (
                "每日涨跌样本：沿用交易位置样本；中间 15 根柱来自本地日线 pct_chg 的最近 15 个交易日，"
                "右侧 5/10/15 日为累计涨跌幅。"
            ),
        },
    }


def _build_disclaimer_context() -> dict[str, Any]:
    return {
        "short_header_zh": SHORT_HEADER_ZH,
        "short_header_en": SHORT_HEADER_EN,
        "footer_short_zh": FOOTER_SHORT_ZH,
        "footer_short_en": FOOTER_SHORT_EN,
        "paragraphs_zh": list(DISCLAIMER_PARAGRAPHS_ZH),
        "paragraphs_en": list(DISCLAIMER_PARAGRAPHS_EN),
    }


def _target_stock_name(analysis: StockEdgeAnalysis) -> str | None:
    sector = analysis.snapshot.sector_membership.data or {}
    for row in sector.get("sector_peers") or []:
        if row.get("ts_code") == analysis.ctx.request.ts_code:
            return row.get("name")
    rows = analysis.snapshot.daily_bars.data
    if rows is not None and not rows.empty and "name" in rows.columns:
        value = rows["name"].dropna()
        if not value.empty:
            return str(value.iloc[-1])
    return None


def _unique_peer_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("ts_code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(row)
    return out


def _build_peer_fundamentals_context(peers: list[dict[str, Any]], factor_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not peers:
        return {"available": False, "rows": [], "note": "本地尚未取得同板块财务比较样本。"}
    peer_by_code = {str(row.get("ts_code")): row for row in peers if row.get("ts_code")}
    factors: dict[tuple[str, str], dict[str, Any]] = {}
    latest_periods: dict[tuple[str, str], str] = {}
    for row in factor_rows:
        code = str(row.get("ts_code") or "")
        period_type = str(row.get("period_type") or "")
        if not code or period_type not in {"annual", "quarterly"}:
            continue
        key = (code, period_type)
        period = str(row.get("period") or "")
        latest_periods[key] = max(latest_periods.get(key, ""), period)
        factors.setdefault(key, {})[str(row.get("factor_name"))] = _safe_float(row.get("value"))
        factors[key]["period"] = latest_periods[key]
    selected = _keep_target_peer_rows(peers, limit=10)
    rows = []
    for peer in selected:
        code = str(peer.get("ts_code") or "")
        annual = factors.get((code, "annual"), {})
        quarterly = factors.get((code, "quarterly"), {})
        if not annual and not quarterly and not peer.get("total_mv"):
            continue
        rows.append(
            {
                "ts_code": code,
                "name": peer.get("name") or peer_by_code.get(code, {}).get("name"),
                "is_target": bool(peer.get("is_target")),
                "total_mv": peer.get("total_mv"),
                "pe_ttm": peer.get("pe_ttm"),
                "pb": peer.get("pb"),
                "annual_period": annual.get("period"),
                "annual_roe": annual.get("ROE"),
                "annual_growth": annual.get("营收同比增速"),
                "annual_cfo_ni": annual.get("CFO/NI"),
                "annual_debt": annual.get("资产负债率"),
                "quarterly_period": quarterly.get("period"),
                "quarterly_roe": quarterly.get("ROE"),
                "quarterly_growth": quarterly.get("营收同比增速"),
                "quarterly_cfo_ni": quarterly.get("CFO/NI"),
                "quarterly_debt": quarterly.get("资产负债率"),
            }
        )
    rows = _attach_peer_financial_scores(rows)
    return {
        "available": bool(rows),
        "rows": rows,
        "note": "财务质量综合分优先看年报/季报 ROE、营收同比、CFO/NI、资产负债率，并用 PE/PB 作估值辅助；短线涨跌幅不参与该表主排序。",
    }


def _attach_peer_financial_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    scored = []
    for row in rows:
        quality = _avg_present([
            _percentile(rows, row, "annual_roe", higher_better=True),
            _percentile(rows, row, "quarterly_roe", higher_better=True),
        ])
        growth = _avg_present([
            _percentile(rows, row, "annual_growth", higher_better=True),
            _percentile(rows, row, "quarterly_growth", higher_better=True),
        ])
        cash = _avg_present([
            _percentile(rows, row, "annual_cfo_ni", higher_better=True),
            _percentile(rows, row, "quarterly_cfo_ni", higher_better=True),
        ])
        leverage = _avg_present([
            _percentile(rows, row, "annual_debt", higher_better=False),
            _percentile(rows, row, "quarterly_debt", higher_better=False),
        ])
        valuation = _avg_present([
            _percentile(rows, row, "pe_ttm", higher_better=False),
            _percentile(rows, row, "pb", higher_better=False),
        ])
        statement_components = [quality, growth, cash, leverage]
        score = (
            _weighted_avg([
                (quality, 0.30),
                (growth, 0.24),
                (cash, 0.22),
                (leverage, 0.14),
                (valuation, 0.10),
            ])
            if any(value is not None for value in statement_components)
            else None
        )
        scored.append({
            **row,
            "fundamental_score": round(score, 4) if score is not None else None,
            "quality_score": round(quality, 4) if quality is not None else None,
            "growth_score": round(growth, 4) if growth is not None else None,
            "cash_score": round(cash, 4) if cash is not None else None,
            "leverage_score": round(leverage, 4) if leverage is not None else None,
            "valuation_score": round(valuation, 4) if valuation is not None else None,
        })
    scored.sort(key=lambda row: (row.get("fundamental_score") is not None, float(row.get("fundamental_score") or -1)), reverse=True)
    target = next((row for row in rows if row.get("is_target")), None)
    if target and not any(row.get("ts_code") == target.get("ts_code") for row in scored[:10]):
        target_scored = next((row for row in scored if row.get("ts_code") == target.get("ts_code")), None)
        if target_scored:
            scored = [*scored[:9], target_scored]
    return scored


def _percentile(rows: list[dict[str, Any]], row: dict[str, Any], key: str, *, higher_better: bool) -> float | None:
    value = _safe_float(row.get(key))
    values = [_safe_float(item.get(key)) for item in rows]
    values = [v for v in values if v is not None]
    if value is None or len(values) < 2:
        return None
    rank = sum(1 for candidate in values if candidate <= value) / len(values)
    return rank if higher_better else 1.0 - rank + 1.0 / len(values)


def _avg_present(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _weighted_avg(items: list[tuple[float | None, float]]) -> float | None:
    present = [(value, weight) for value, weight in items if value is not None]
    if not present:
        return None
    weight_sum = sum(weight for _, weight in present)
    return sum(float(value) * weight for value, weight in present) / weight_sum


def _keep_target_peer_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda row: float(row.get("total_mv") or 0), reverse=True)
    target = next((row for row in ranked if row.get("is_target")), None)
    out = ranked[:limit]
    if target is not None and not any(row.get("ts_code") == target.get("ts_code") for row in out):
        out = [*out[: max(limit - 1, 0)], target]
    return out


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
