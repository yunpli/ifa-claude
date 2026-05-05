"""Markdown renderer for Stock Edge."""
from __future__ import annotations

from typing import Any


def render_markdown(report: dict[str, Any]) -> str:
    plan = report["plan"]
    dl = report["decision_layer"]
    lines = [
        f"# 个股作战室 — {report['ts_code']}",
        "",
        f"- 分析模式：`{report['mode_label']}`",
        f"- 分析交易日：`{report['as_of_trade_date']}`",
        f"- 数据截止（北京时间）：`{report['data_cutoff_at_bjt']}`",
        f"- 截止规则：`{report['as_of_rule']}`",
        f"- 参数哈希：`{report['param_hash']}`",
        "",
        "## 三周期交易决策",
        "",
    ]
    for key in ("decision_5d", "decision_10d", "decision_20d"):
        d = dl[key]
        buy = d["buy_zone"]
        stop = d["stop_loss"]
        first = d["first_take_profit"]
        support = "；".join(s["label"] for s in d.get("key_supporting_signals", [])[:3]) or "暂无强支持"
        risks = "；".join(s["label"] for s in d.get("key_risk_signals", [])[:3]) or "暂无强风险"
        lines.extend(
            [
                f"### {d['horizon_label']}",
                f"- 结论：**{d['user_facing_label']}**（`{d['decision']}`）；score `{d['score']:.4f}`，类型 `{d['score_type']}`，风险 `{d['risk_level']}`，置信度 `{d['confidence_level']}`。",
                f"- 买入区间：`{_fmt_price(buy.get('low'))} - {_fmt_price(buy.get('high'))}`；不追高：`{_fmt_price(d.get('chase_warning_price'))}`；止损：`{_fmt_price(stop.get('price'))}`；第一止盈：`{_fmt_price(first.get('low'))} - {_fmt_price(first.get('high'))}`。",
                f"- 动作：{d['suggested_action']}",
                f"- 支持信号：{support}",
                f"- 风险信号：{risks}",
                f"- 概率提示：{d['probability_display_warning']}",
                "",
            ]
        )
    pred = report["prediction_context"]
    lines.extend(["", "## 买卖时机执行辅助", ""])
    d5 = dl["decision_5d"]
    lines.append(
        f"- 主路径：**{d5['user_facing_label']}**；`{d5['score_type']}` `{d5['score']:.4f}`；"
        f"风险 `{d5['risk_level']}`；模型 `{pred['probability_model']}`。"
    )
    lines.append("- 本节只作为三周期决策的执行辅助；未校准概率不作为确定性上涨概率展示。")
    today = pred["today_entry"]
    best = pred.get("best_opportunity")
    if best:
        lines.append(
            f"- 推荐目标：**{best['label']}**，`{best['horizon_days']}` 个交易日，"
            f"目标收益 `{best['return_pct']:.1f}%`，目标价 `{best['target_price']:.4f}`，"
            f"概率 `{best['probability']:.2%}`，期望值 `{best['expected_value']:.2%}`。"
        )
    if today["available"]:
        lines.append(
            f"- 今日买入价：`{today['entry_low']:.4f} - {today['entry_high']:.4f}`；"
            f"止损 `{today['stop_price']:.4f}`；{today['rule']}"
        )
    else:
        lines.append("- 今日买入价：暂无可执行买点。")
    if pred["next_5d"]:
        lines.append("- 未来5个交易日买入条件：")
        for row in pred["next_5d"]:
            lines.append(
                f"  - {row['scenario']}（{row['priority']}）：`{row['entry_low']:.4f} - {row['entry_high']:.4f}`；"
                f"止损 `{row['stop_price']:.4f}`；{row['condition']}"
            )
    if pred["sell_targets"]:
        lines.append("- 候选卖出目标：")
        for target in pred["sell_targets"]:
            suffix = "；推荐目标" if target.get("is_best") else ""
            probability = f"；概率 {target['probability']:.2%}" if target.get("probability") is not None else ""
            horizon = f"；{target['horizon_days']}日" if target.get("horizon_days") else ""
            lines.append(
                f"  - {target['label']}：`{target['price']:.4f}`（收益 {target['return_pct']:.1f}%{horizon}{probability}{suffix}）；{target['rule']}"
            )
    scenario = report.get("scenario_tree") or {}
    lines.extend(["", "## 预测执行场景树", ""])
    if scenario.get("available"):
        lines.append(f"- 摘要：{scenario.get('summary')}")
        for row in scenario.get("branches") or []:
            entry = (
                f"`{row['entry_low']:.4f} - {row['entry_high']:.4f}`"
                if row.get("entry_low") is not None and row.get("entry_high") is not None else "—"
            )
            target = f"`{row['target_price']:.4f}`" if row.get("target_price") is not None else "—"
            stop = f"`{row['stop_price']:.4f}`" if row.get("stop_price") is not None else "—"
            lines.append(
                f"- {row['label']}（{row['probability_label']}）：条件：{row['condition']}；动作：{row['action']}；"
                f"买入带 {entry}；目标 {target}；失效 {stop}；观察：{row['watch']}"
            )
        lines.append(f"- 生成规则：{scenario.get('note')}")
    else:
        lines.append("- 当前结构化数据不足，未生成场景树。")
    lines.extend(
        [
            "",
            "## 关键价位与技术图谱",
            "",
            "- HTML 报告内嵌日线 K 线与 MA5 / MA20 / MA60 图，并直接叠加支撑/压力线。",
            "- HTML 报告内嵌 MACD 图，用于观察趋势确认与背离。",
            "- HTML 报告内嵌 5 日动量图，用于观察短线惯性强弱。",
        ]
    )
    sm = report["strategy_matrix"]
    lines.extend(
        [
            "",
            "## 多策略矩阵",
            "",
            f"- 总分：`{sm['aggregate_score']:.4f}`；正向策略 `{sm['positive_count']}`；负向策略 `{sm['negative_count']}`；覆盖 TA 策略族 `{len(sm['covered_ta_families'])}`。",
        ]
    )
    for signal in sm["signals"][:12]:
        lines.append(
            f"- {signal['name']}（{signal['family']} / {signal['cluster_label']} / {signal['algorithm']}）："
            f"{signal['direction']}，分数 {signal['score']:+.2f}；{signal['evidence']}"
        )
    leaders = report["sector_leaders"]
    lines.extend(["", "## 同板块财务对照", ""])
    if leaders["available"]:
        lines.append(f"- SW L2：`{leaders['l2_name']}`")
        fundamentals = leaders.get("fundamentals") or {}
        if fundamentals.get("available"):
            lines.append("- 同板块财务比较：")
            for row in fundamentals.get("rows", [])[:10]:
                mark = "（目标股）" if row.get("is_target") else ""
                lines.append(
                    f"  - {row['name'] or row['ts_code']} {mark}：财务分 {_fmt_num(row.get('fundamental_score'))}，年报 {row.get('annual_period') or '—'}，"
                    f"年ROE {_fmt_signed_raw_pct(row.get('annual_roe'))}，年营收YoY {_fmt_signed_raw_pct(row.get('annual_growth'))}，"
                    f"季报 {row.get('quarterly_period') or '—'}，季ROE {_fmt_signed_raw_pct(row.get('quarterly_roe'))}，"
                    f"季营收YoY {_fmt_signed_raw_pct(row.get('quarterly_growth'))}。"
                )
        for row in leaders["rows"][:12]:
            mark = "（目标股）" if row.get("is_target") else ""
            lines.append(
                f"- {row['category_label']} #{row['rank']}：{row['name'] or row['ts_code']} {mark}"
                f"，{row['metric_label']} {_fmt_num(row['metric_value'])}，收盘 {_fmt_num(row['close'])}"
            )
    else:
        lines.append("- 本地尚未取得 SW L2 同板块财务对照数据。")
    if plan.get("targets"):
        lines.extend(["", "## 目标价格兼容审计", ""])
        lines.append("- 以下旧目标来自兼容 TradePlan，仅用于审计和回溯；用户主决策以三周期对象为准。")
        for target in plan["targets"]:
            lines.append(f"- {target['label']}：`{target['price']:.4f}` — {target['reason']}")
    prob = plan["probability"]
    lines.extend(
        [
            "",
            "## 概率审计",
            "",
            f"- 先止损估计：`{prob['prob_stop_first']:.2%}`",
            f"- 入场成交估计：`{prob['entry_fill_probability']:.2%}`",
            f"- 模型：`{prob['model_version']}`，校准状态：`{'已校准' if prob['calibrated'] else '规则基线'}`",
            "- 说明：兼容概率面不进入用户主决策，未校准值不能理解为确定性预测。",
        ]
    )
    if plan.get("vetoes"):
        lines.extend(["", "## 风控否决", ""])
        lines.extend(f"- {v}" for v in plan["vetoes"])
    val = report["strategy_validation"]
    lines.extend(["", "## 策略验证摘要", "", f"- 范围：{val['scope_label']}"])
    if val["available"]:
        for row in val["rows"]:
            lines.append(
                f"- {row['setup_name']}：样本 {row['triggers_count']}，"
                f"60日胜率 {_fmt_raw_pct(row['winrate_60d'])}，"
                f"60日均收益 {_fmt_signed_raw_pct(row['avg_return_60d'])}，"
                f"盈亏比 {_fmt_num(row['pl_ratio_60d'])}，"
                f"250日胜率 {_fmt_raw_pct(row['winrate_250d'])}，"
                f"衰减 {_fmt_signed_raw_pct(row['decay_score'])}"
            )
    else:
        lines.append("- 本地尚未取得该股触发策略对应的滚动验证指标。")
    lines.extend(
        [
            "",
            "## 免责声明",
            "",
            "本报告基于公开数据和本地结构化缓存生成，仅供信息参考，不构成投资建议。投资有风险，交易需独立判断。",
            "",
        ]
    )
    return "\n".join(lines)


def _fmt_raw_pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}%"


def _fmt_signed_raw_pct(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    return f"{number:+.2f}%"


def _fmt_num(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.2f}"


def _fmt_price(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.4f}"
