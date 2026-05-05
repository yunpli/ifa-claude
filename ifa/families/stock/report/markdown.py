"""Markdown renderer for Stock Edge."""
from __future__ import annotations

import re
from typing import Any


def render_markdown(report: dict[str, Any]) -> str:
    dl = report["decision_layer"]
    d5 = dl["decision_5d"]
    title_name = f"{report['stock_name']} · " if report.get("stock_name") else ""
    lines = [
        f"# 个股作战室 — {title_name}{report['ts_code']}",
        "",
        f"- 分析模式：`{report['mode_label']}`",
        f"- 分析交易日：`{report['as_of_trade_date']}`",
        f"- 数据截止（北京时间）：`{report['data_cutoff_at_bjt']}`",
        f"- 说明：{report['disclaimer']['short_header_zh']}",
        "",
        "## 今日结论",
        "",
        f"**{d5['user_facing_label']}**：{d5['decision_summary']}",
        "",
        f"- 还没买：{d5.get('if_not_holding')}",
        f"- 已经持有：{d5.get('if_already_holding')}",
        f"- 不要做：超过 `{_fmt_price(d5.get('chase_warning_price'))}` 不追高；跌破 `{_fmt_price((d5.get('stop_loss') or {}).get('price'))}` 先按失效处理。",
        "",
        "## 三周期决策",
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
                f"- 结论：**{d['user_facing_label']}**；系统评分 `{d['score']:.2f}`，风险 `{d['risk_level']}`，置信度 `{d['confidence_level']}`。",
                f"- 买入区间：`{_fmt_price(buy.get('low'))} - {_fmt_price(buy.get('high'))}`；不追高：`{_fmt_price(d.get('chase_warning_price'))}`；止损：`{_fmt_price(stop.get('price'))}`；第一止盈：`{_fmt_price(first.get('low'))} - {_fmt_price(first.get('high'))}`。",
                f"- 动作：{d['suggested_action']}",
                f"- 支持信号：{support}",
                f"- 风险信号：{risks}",
                "",
            ]
        )
    lines.extend(
        [
            "## 价格作战地图",
            "",
            "- HTML 报告内嵌日线 K 线，并直接标出支撑、压力、买入区、止损和止盈参考。",
            "- HTML 报告内嵌 MACD 与 5 日动量，用来辅助判断趋势顺逆和短线过热。",
            "",
        ]
    )
    scenario = report.get("scenario_tree") or {}
    lines.extend(["## 未来5日执行路线", ""])
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
                f"买入带 {entry}；目标 {target}；失效 {stop}；观察：{_human_signal(row['watch'])}"
            )
    else:
        lines.append("- 当前结构化数据不足，未生成场景树。")
    lines.extend(["", "## 为什么这样判断", ""])
    lines.append("- 支持理由：" + ("；".join(s["label"] for s in d5.get("key_supporting_signals", [])[:5]) or "暂无强支持"))
    lines.append("- 风险理由：" + ("；".join(s["label"] for s in d5.get("key_risk_signals", [])[:5]) or "暂无强风险"))
    sm = report["strategy_matrix"]
    lines.extend(
        [
            "",
            "## 策略矩阵",
            "",
            f"- 综合强度：`{sm['aggregate_score']:.2f}`；正向模型 `{sm['positive_count']}`；风险模型 `{sm['negative_count']}`；覆盖 TA 策略族 `{len(sm['covered_ta_families'])}`。",
        ]
    )
    for signal in sm["signals"][:12]:
        lines.append(
            f"- {signal['name']}（{signal['cluster_label']}）：{signal['direction']}，强度 {signal['score']:+.2f}；{_human_signal(signal['evidence'])}"
        )
    leaders = report["sector_leaders"]
    lines.extend(["", "## 同板块位置", ""])
    if leaders["available"]:
        lines.append(f"- SW L2：`{leaders['l2_name']}`")
        fundamentals = leaders.get("fundamentals") or {}
        if fundamentals.get("available"):
            lines.append(f"- 财务质量样本：{leaders.get('peer_selection_notes', {}).get('fundamental')}")
            lines.append("- 同板块财务比较：")
            for row in fundamentals.get("rows", [])[:10]:
                mark = "（目标股）" if row.get("is_target") else ""
                lines.append(
                    f"  - {row['name'] or row['ts_code']} {mark}：财务分 {_fmt_num(row.get('fundamental_score'))}，年报 {row.get('annual_period') or '—'}，"
                    f"年ROE {_fmt_signed_raw_pct(row.get('annual_roe'))}，年营收YoY {_fmt_signed_raw_pct(row.get('annual_growth'))}，"
                    f"季报 {row.get('quarterly_period') or '—'}，季ROE {_fmt_signed_raw_pct(row.get('quarterly_roe'))}，"
                        f"季营收YoY {_fmt_signed_raw_pct(row.get('quarterly_growth'))}。"
                )
        lines.append(f"- 交易位置样本：{leaders.get('peer_selection_notes', {}).get('trading')}")
        lines.append(f"- 每日涨跌样本：{leaders.get('peer_selection_notes', {}).get('daily_returns')}")
        for row in leaders["rows"][:12]:
            mark = "（目标股）" if row.get("is_target") else ""
            lines.append(
                f"- {row['category_label']} #{row['rank']}：{row['name'] or row['ts_code']} {mark}"
                f"，{row['metric_label']} {_fmt_num(row['metric_value'])}，收盘 {_fmt_num(row['close'])}"
            )
    else:
        lines.append("- 本地尚未取得 SW L2 同板块财务对照数据。")
    lines.extend(
        [
            "",
            "## 风险纪律",
            "",
            f"- 价格失效：{d5.get('invalidation_condition')}",
            f"- 已有仓位：{d5.get('if_already_holding')}",
            f"- 没有仓位：{d5.get('if_not_holding')}",
            "- T+0：A 股不能裸 T+0；没有底仓时不输出日内高抛低吸计划。",
        ]
    )
    lines.extend(
        [
            "",
            "## 完整免责声明 / Full Disclaimer",
            "",
            "### Disclaimer",
            "",
        ]
    )
    for p in report.get("disclaimer", {}).get("paragraphs_en") or []:
        lines.extend([p, ""])
    lines.extend(["### 免责声明", ""])
    for p in report.get("disclaimer", {}).get("paragraphs_zh") or []:
        lines.extend([p, ""])
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


def _human_signal(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        text = "；".join(str(item) for item in value if item)
    else:
        text = str(value)
    replacements = [
        (r"score=([0-9.]+)", r"系统评分 \1"),
        (r"风险=high", "风险偏高"),
        (r"风险=medium", "风险中等"),
        (r"风险=low", "风险较低"),
        (r"置信=high", "置信度高"),
        (r"置信=medium", "置信度中"),
        (r"置信=low", "置信度低"),
        (r"置信度=high", "置信度高"),
        (r"置信度=medium", "置信度中"),
        (r"置信度=low", "置信度低"),
        (r"；?60日胜率均值\s*[^；。]*[；。]?", "；"),
        (r"；?衰减均值\s*[^；。]*[；。]?", "；"),
        (r"暂无滚动胜率", "历史样本不足"),
        (r"收益置信带过宽/左尾重：40日\+50%：P10\s*[^。；]*[。；]?", "收益分布偏宽，需降低追高。"),
        (r"右尾\s*GBM\s*压制：[^。；]*[。；]?", "极端上涨模型暂不支持追高。"),
        (r"OOS AUC proxy\s*[^，。；]*", ""),
        (r"\d+\s*个训练标签", "历史样本"),
        (r"40日\+\d+%", "高收益目标"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    text = re.sub(r"；{2,}", "；", text)
    return text.strip("； ")
