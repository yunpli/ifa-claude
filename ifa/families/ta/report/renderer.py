"""HTML + Markdown renderers for the TA evening report."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_html(report: dict) -> str:
    return _env.get_template("ta_evening.html").render(report=report)


def render_markdown(report: dict) -> str:
    out: list[str] = [f"# {report['title']}", "", f"_报告生成 · {report['report_date_bjt']}_", ""]
    for s in report["sections"]:
        t = s["type"]
        if t == "overview":
            out.append("## §01 市场概览")
            out.append(f"- 交易日 **{s['trade_date']}**")
            conf = f" ({s['regime_confidence']:.2f})" if s.get("regime_confidence") is not None else ""
            out.append(f"- 体制 **{s['regime']}**{conf}")
            out.append(f"- 候选总数 **{s['total_candidates']}** | 观察池 **{s['top_watchlist_count']}** "
                       f"| 激活 setup **{s['active_setup_count']}**")
            out.append("")
        elif t == "candidate_list":
            out.append(f"## {s['title']}")
            if not s["candidates"]:
                out.append(f"_本日无 {s['stars']} 星级候选。_")
            else:
                out.append("| # | 代码 | Setup | 分 | ★ | 触发条件 |")
                out.append("|---|---|---|---|---|---|")
                for c in s["candidates"]:
                    stars = "★" * c["stars"] + "☆" * (5 - c["stars"])
                    out.append(f"| {c['rank']} | {c['ts_code']} | {c['setup_name']} | "
                               f"{c['score']:.2f} | {stars} | {', '.join(c['triggers'])} |")
            out.append("")
        elif t == "verification":
            out.append(f"## {s['title']}")
            if not s.get("prev_date"):
                out.append("_无前一交易日数据可供验证。_")
            else:
                out.append(f"_基准：{s['prev_date']} 观察池 → 今日实际 T+1_")
                if s["summary"]:
                    out.append("")
                    out.append("分布: " + " | ".join(f"**{k}**: {v}" for k, v in s["summary"].items()))
                if s["candidates"]:
                    out.append("")
                    out.append("| # | 代码 | Setup | 分 | T+1 收益 | 状态 |")
                    out.append("|---|---|---|---|---|---|")
                    for c in s["candidates"]:
                        ret = f"{c['return_pct']:+.2f}%" if c.get("return_pct") is not None else "-"
                        out.append(f"| {c['rank']} | {c['ts_code']} | {c['setup_name']} | "
                                   f"{c['score']:.2f} | {ret} | {c.get('status') or '-'} |")
            out.append("")
        elif t == "metrics_table":
            out.append(f"## {s['title']}")
            if s["rows"]:
                out.append("| Setup | 样本 | 胜率 60d | 均收益 60d | 盈亏比 | 胜率 250d | 衰减 |")
                out.append("|---|---|---|---|---|---|---|")
                for r in s["rows"]:
                    def f(v, fmt):
                        return fmt.format(v) if v is not None else "-"
                    out.append(f"| {r['setup_name']} | {r['n'] or '-'} | "
                               f"{f(r['winrate_60d'], '{:.1f}%')} | "
                               f"{f(r['avg_return_60d'], '{:+.2f}%')} | "
                               f"{f(r['pl_ratio'], '{:.2f}')} | "
                               f"{f(r['winrate_250d'], '{:.1f}%')} | "
                               f"{f(r['decay'], '{:+.1f}')} |")
            out.append("")
        elif t == "market_state":
            out.append(f"## {s['title']}")
            parts = []
            if s.get("sse_close") is not None:
                parts.append(f"SSE 收 **{s['sse_close']:.2f}** ({s['sse_pct_chg']:+.2f}%)")
            if s.get("amount_yi_yuan") is not None:
                a = f"成交 **{s['amount_yi_yuan']:.0f} 亿**"
                if s.get("amount_pct_60d") is not None:
                    a += f" (60d {s['amount_pct_60d']:.0f}%)"
                parts.append(a)
            if s.get("up_count") is not None:
                parts.append(f"涨 **{s['up_count']}** 跌 **{s['down_count']}**")
            if s.get("limit_up") is not None:
                parts.append(f"涨停 **{s['limit_up']}** 跌停 **{s['limit_down']}** "
                             f"最高连板 **{s['consecutive_lb_high']}**")
            if s.get("blow_up_count"):
                parts.append(f"炸板 **{s['blow_up_count']}** ({s['blow_up_rate']:.1f}%)")
            if s.get("north_yi_yuan") is not None:
                parts.append(f"北向 **{s['north_yi_yuan']:+.1f} 亿**")
            if s.get("market_state"):
                parts.append(f"市态 **{s['market_state']}**")
            out.append(" · ".join(parts))
            out.append("")
        elif t == "family_grid":
            out.append(f"## {s['title']}")
            out.append("| 族 | 命中 | ≥4★ | 主导 Setup |")
            out.append("|---|---|---|---|")
            for fam_name, f in s["families"].items():
                if f["n"] == 0:
                    continue
                top3 = ", ".join(f"{x['name']} ({x['n']})" for x in f["setups"][:3])
                out.append(f"| **{fam_name}** | {f['n']} | {f['top']} | {top3} |")
            out.append("")
        elif t == "attribution":
            out.append(f"## {s['title']}")
            out.append(f"_窗口：{s['window_start']} → 今日（T+1 实际收益）_")
            if s["rows"]:
                out.append("")
                out.append("| Setup | 样本 | 胜率 | 均收益 |")
                out.append("|---|---|---|---|")
                for r in s["rows"]:
                    wr = f"{r['win_rate']:.1f}%" if r["win_rate"] is not None else "-"
                    ar = f"{r['avg_return_pct']:+.2f}%" if r["avg_return_pct"] is not None else "-"
                    out.append(f"| {r['setup_name']} | {r['n']} | {wr} | {ar} |")
            else:
                out.append("_窗口内无可归因样本。_")
            out.append("")
        elif t == "risk_scan":
            out.append(f"## {s['title']}")
            if s.get("climax_warning"):
                out.append(f"> ⚠ {s['climax_warning']}")
                out.append("")
            out.append(f"- 筹码松动候选 (C2)：**{s['chip_loose_count']}**")
            if s["decaying_setups"]:
                out.append("- 衰退 Setup（decay ≤ -5pp）：")
                for d in s["decaying_setups"]:
                    wr = f" / 胜率 60d {d['winrate_60d']:.1f}%" if d.get("winrate_60d") is not None else ""
                    out.append(f"  - {d['setup_name']}: {d['decay']:+.1f}pp{wr}")
            else:
                out.append("- 无 setup 衰减超 -5pp 阈值")
            out.append("")
        elif t == "hypotheses":
            out.append(f"## {s['title']}")
            out.append("_T+1 自动评估，结果写入 `ta.report_judgments`_")
            if s["hypotheses"]:
                out.append("")
                out.append("| 代码 | Setup | 分 | 假设陈述 |")
                out.append("|---|---|---|---|")
                for h in s["hypotheses"]:
                    out.append(f"| {h['ts_code']} | {h['setup_name']} | "
                               f"{h['score']:.2f} | {h['statement']} |")
            else:
                out.append("_本日无 5★ 候选可生成假设。_")
            out.append("")
        elif t == "disclaimer":
            out.append(f"## {s['title']}")
            out.append("> " + s["body"])
            out.append("")
    return "\n".join(out)
