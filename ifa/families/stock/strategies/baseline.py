"""Rule-baseline Stock Edge trade-plan synthesis.

This is intentionally conservative. It produces an auditable first plan before
ML/Kronos/calibration exists, and records the probability model as
`heuristic_v0`.
"""
from __future__ import annotations

from ifa.families.stock.data.snapshot import StockEdgeSnapshot
from ifa.families.stock.features import build_support_resistance, compute_technical_summary
from ifa.families.stock.features.support_resistance import nearest_resistance, nearest_support
from ifa.families.stock.plan import (
    EvidenceItem,
    PositionSize,
    PriceLevel,
    PriceTarget,
    PriceZone,
    ProbabilityBlock,
    T0Plan,
    TradePlan,
)
from ifa.families.stock.strategies.matrix import compute_strategy_matrix
from ifa.families.stock.strategies.prediction_surface import build_prediction_surface
from ifa.families.stock.strategies.position_sizing import SizingInputs, build_position_size


def build_rule_baseline_plan(snapshot: StockEdgeSnapshot) -> TradePlan:
    daily = snapshot.daily_bars.require()
    tech = compute_technical_summary(daily)
    levels = build_support_resistance(daily)
    support = nearest_support(levels, tech.close)
    resistance = nearest_resistance(levels, tech.close)
    params = snapshot.ctx.params
    risk = params.get("risk", {})
    vetoes = _vetoes(snapshot, tech)
    strategy_matrix = compute_strategy_matrix(snapshot)
    score = float(strategy_matrix["aggregate_score"])
    matrix_params = params.get("strategy_matrix", {})
    aggregate_params = matrix_params.get("aggregate", {})
    buy_threshold = float(aggregate_params.get("buy_threshold", 0.68))
    watch_threshold = float(aggregate_params.get("watch_threshold", 0.50))

    if vetoes:
        action = "avoid"
        confidence = "high"
        setup = "硬性否决"
        position = PositionSize("禁止开仓", 0.0, "触发硬性风控否决。")
    elif score >= buy_threshold:
        action = "buy"
        confidence = "medium"
        setup = _setup_type(tech)
        position = PositionSize("待计算", 0.0, "等待概率面完成后计算仓位。")
    elif score >= watch_threshold:
        action = "watch"
        confidence = "medium"
        setup = _setup_type(tech)
        position = PositionSize("观察", 0.0, "证据强弱混合，等待价格和成交确认。")
    else:
        action = "avoid"
        confidence = "medium"
        setup = _setup_type(tech)
        position = PositionSize("禁止开仓", 0.0, "规则基线尚未显示足够交易优势。")

    entry_zone = _entry_zone(tech, support) if action in ("buy", "watch") else None
    stop = _stop_level(tech, support) if entry_zone else None
    targets = _targets(tech, entry_zone, resistance, float(risk.get("right_tail_target_pct", 50.0)))
    probability = _probability(
        strategy_matrix,
        params,
        close=tech.close,
        entry_high=entry_zone.high if entry_zone else None,
        stop_price=stop.price if stop else None,
        atr14=tech.atr14,
    )
    position = build_position_size(
        SizingInputs(
            action=action,
            confidence=confidence,
            entry_zone=entry_zone,
            stop=stop,
            probability=probability,
            vetoes=vetoes,
        ),
        params=params,
    )
    t0_plan = _t0_plan(snapshot, tech)
    evidence = _evidence(snapshot, tech, levels, score, strategy_matrix)

    return TradePlan(
        action=action,
        confidence=confidence,
        setup_type=setup,
        entry_zone=entry_zone,
        add_zone=None,
        stop=stop,
        targets=targets,
        holding_window_days=tuple(params.get("risk", {}).get("holding_window_days", [20, 40])),  # type: ignore[arg-type]
        probability=probability,
        position_size=position,
        t0_plan=t0_plan,
        vetoes=vetoes,
        evidence=evidence,
    )


def _vetoes(snapshot: StockEdgeSnapshot, tech) -> list[str]:
    risk = snapshot.ctx.params.get("risk", {})
    vetoes: list[str] = []
    min_amount = float(risk.get("min_avg_amount_yuan", 50_000_000))
    if tech.avg_amount_7d_yuan is not None and tech.avg_amount_7d_yuan < min_amount:
        vetoes.append(f"7日平均成交额低于流动性底线：{tech.avg_amount_7d_yuan:.0f} < {min_amount:.0f}")
    if snapshot.daily_bars.rows < 7:
        vetoes.append("本地日线少于7根")
    if snapshot.daily_bars.as_of != snapshot.ctx.as_of.as_of_trade_date:
        vetoes.append("最新本地日线早于分析交易日")
    return vetoes


def _setup_type(tech) -> str:
    if tech.trend_label == "uptrend":
        return "趋势延续"
    if tech.trend_label == "recovery":
        return "回踩修复"
    if tech.trend_label == "weak":
        return "弱结构"
    return "历史样本不足"


def _entry_zone(tech, support) -> PriceZone:
    if support is None:
        low = tech.close * 0.97
        high = tech.close
        return PriceZone(round(low, 4), round(high, 4), "附近缺少明确支撑，采用保守的现价回踩区。")
    atr = tech.atr14 or tech.close * 0.03
    low = support.price * 0.995
    high = min(tech.close, support.price + 0.35 * atr)
    if high < low:
        high = low * 1.01
    return PriceZone(round(low, 4), round(high, 4), f"参考最近支撑：{_source_label(support.source)}。")


def _stop_level(tech, support) -> PriceLevel:
    if support is None:
        return PriceLevel(round(tech.close * 0.92, 4), "缺少支撑锚，默认以8%跌幅作为失效线。")
    atr = tech.atr14 or tech.close * 0.03
    return PriceLevel(round(max(0.01, support.price - 0.8 * atr), 4), f"跌破支撑锚：{_source_label(support.source)}。")


def _targets(tech, entry_zone, resistance, right_tail_target_pct: float) -> list[PriceTarget]:
    if entry_zone is None:
        return []
    entry = entry_zone.high
    targets = [
        PriceTarget("目标一", round(entry * 1.12, 4), "规则基线的第一止盈位。"),
        PriceTarget("目标二", round(entry * 1.25, 4), "20-40个交易日波段目标的第二目标位。"),
        PriceTarget(
            "右尾目标",
            round(entry * (1.0 + right_tail_target_pct / 100.0), 4),
            "用户目标，仅配合保守概率展示，不代表保证触及。",
        ),
    ]
    if resistance is not None:
        targets.insert(0, PriceTarget("最近压力位", resistance.price, f"参考压力：{_source_label(resistance.source)}。"))
    return targets


def _probability(
    strategy_matrix: dict,
    params: dict,
    *,
    close: float,
    entry_high: float | None,
    stop_price: float | None,
    atr14: float | None,
) -> ProbabilityBlock:
    surface = build_prediction_surface(
        strategy_matrix=strategy_matrix,
        params=params,
        close=close,
        entry_high=entry_high,
        stop_price=stop_price,
        atr14=atr14,
    )
    return ProbabilityBlock(
        prob_hit_50_40d=surface.prob_hit_50_40d,
        expected_return_40d=surface.expected_return_40d,
        expected_drawdown_40d=surface.expected_drawdown_40d,
        model_version=surface.model_version,
        calibrated=surface.calibrated,
        prob_hit_20_40d=surface.prob_hit_20_40d,
        prob_hit_30_40d=surface.prob_hit_30_40d,
        prob_stop_first=surface.prob_stop_first,
        entry_fill_probability=surface.entry_fill_probability,
        return_p10_40d=surface.return_p10_40d,
        return_p50_40d=surface.return_p50_40d,
        return_p90_40d=surface.return_p90_40d,
        opportunities=surface.opportunities,
        best_opportunity=surface.best_opportunity,
    )


def _t0_plan(snapshot: StockEdgeSnapshot, tech) -> T0Plan | None:
    req = snapshot.ctx.request
    t0_params = snapshot.ctx.params.get("t0", {})
    if not t0_params.get("enabled", True):
        return None
    if not req.has_base_position:
        return T0Plan(eligible=False, do_not_t0_if=["没有底仓，A 股不能裸 T+0。"])
    atr = tech.atr14 or tech.close * 0.03
    return T0Plan(
        eligible=True,
        max_size_pct_of_base=float(t0_params.get("max_size_pct_of_base", 20.0)),
        sell_zone=PriceZone(round(tech.close + 0.6 * atr, 4), round(tech.close + 1.2 * atr, 4), "基于 ATR 的高抛区间。"),
        buyback_zone=PriceZone(round(tech.close - 1.0 * atr, 4), round(tech.close - 0.4 * atr, 4), "基于 ATR 的回补区间。"),
        do_not_t0_if=["一字涨停", "成交量低于近期常态50%", "高开超过6%"],
    )


def _evidence(snapshot: StockEdgeSnapshot, tech, levels, score: float, strategy_matrix: dict) -> list[EvidenceItem]:
    positive = [
        s["name"]
        for s in strategy_matrix.get("signals", [])
        if s.get("direction") == "positive"
    ][:5]
    negative = [
        s["name"]
        for s in strategy_matrix.get("signals", [])
        if s.get("direction") == "negative"
    ][:5]
    return [
        EvidenceItem("收盘价", tech.close, "smartmoney.raw_daily"),
        EvidenceItem("趋势状态", _trend_label(tech.trend_label), "规则基线"),
        EvidenceItem("5日涨跌幅", tech.return_5d_pct, "smartmoney.raw_daily"),
        EvidenceItem("7日平均成交额（元）", tech.avg_amount_7d_yuan, "smartmoney.raw_daily"),
        EvidenceItem("支撑压力位数量", len(levels), "支撑压力位计算"),
        EvidenceItem("策略矩阵总分", round(score, 4), "多策略矩阵", "尚未完成校准的启发式评分。"),
        EvidenceItem("正向策略", "、".join(positive) if positive else "—", "多策略矩阵"),
        EvidenceItem("负向策略", "、".join(negative) if negative else "—", "多策略矩阵"),
    ]


def _trend_label(label: str) -> str:
    return {
        "uptrend": "上升趋势",
        "recovery": "修复结构",
        "weak": "弱结构",
        "insufficient_history": "历史样本不足",
    }.get(label, label)


def _source_label(source: str) -> str:
    return {
        "20d_low": "20日低点",
        "20d_high": "20日高点",
        "swing_low": "摆动低点",
        "swing_high": "摆动高点",
        "ma20": "20日均线",
        "ma60": "60日均线",
    }.get(source, source)
