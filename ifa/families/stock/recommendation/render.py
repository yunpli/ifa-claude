"""Render Stock Edge recommendation briefs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import RecommendationBriefReport

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STOCK_TEMPLATES = Path(__file__).parents[1] / "report" / "templates"
_RESEARCH_STYLES = Path(__file__).parents[2] / "research" / "report" / "templates" / "styles.css"


def render_html(report: RecommendationBriefReport) -> str:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["fmt_score"] = _fmt_score
    env.filters["group_label"] = _group_label
    css = "\n\n".join(
        [
            _RESEARCH_STYLES.read_text(encoding="utf-8"),
            (_STOCK_TEMPLATES / "styles.css").read_text(encoding="utf-8"),
            (_TEMPLATES_DIR / "recommendation_brief.css").read_text(encoding="utf-8"),
        ]
    )
    return env.get_template("recommendation_brief.html").render(report=report.to_dict(), inline_css=css)


def render_markdown(report: RecommendationBriefReport) -> str:
    data = report.to_dict()
    lines = [
        f"# {data['title']}",
        "",
        f"- 观察交易日: `{data['as_of_trade_date']}`",
        f"- 数据截止: `{data['data_cutoff_bjt']}`",
        f"- 生成时间: `{data['generated_at_bjt']}`",
        f"- 逻辑版本: `{data['logic_version']}`",
        "",
        f"> {data['disclaimer']['short_header_zh']}",
        "",
    ]
    for group in ("strong", "watchlist", "avoid"):
        lines.extend([f"## {_group_label(group)}", ""])
        rows = data["groups"].get(group) or []
        if not rows:
            lines.extend(["暂无候选。", ""])
            continue
        for c in rows:
            name = f"{c['name']} " if c.get("name") else ""
            sector = c.get("l2_name") or "板块未知"
            lines.append(f"### {name}{c['ts_code']} · {sector}")
            lines.append(
                f"- 分数/排名: leader `{_fmt_score(c.get('leader_score'))}`, "
                f"sector `{_fmt_score(c.get('sector_score'))}`, rank `{c.get('rank_in_sector') or '-'} / {c.get('sector_rank_count') or '-'}`"
            )
            lines.append(f"- 周期适配: 5d `{c['horizon_suitability']['5d']}`, 10d `{c['horizon_suitability']['10d']}`, 20d `{c['horizon_suitability']['20d']}`")
            lines.append(f"- 触发: {c['trigger']}")
            lines.append(f"- 失效: {c['invalidation']}")
            if c.get("evidence"):
                lines.append("- 证据: " + "；".join(f"{e['label']}={e['value']}" for e in c["evidence"][:5]))
            if c.get("conflicts"):
                lines.append("- 冲突: " + "；".join(c["conflicts"]))
            if c.get("risk_notes"):
                lines.append("- 风险: " + "；".join(c["risk_notes"]))
            lines.append("")
    lines.extend(["## 数据源状态", ""])
    for name, status in data["source_status"].items():
        availability = "available" if status.get("available") else "unavailable"
        lines.append(f"- `{name}`: {availability}, rows={status.get('rows')}, latest={status.get('latest')}")
    lines.extend(["", "## 完整免责声明 / Full Disclaimer", ""])
    for p in data["disclaimer"]["paragraphs_zh"]:
        lines.append(p)
        lines.append("")
    for p in data["disclaimer"]["paragraphs_en"]:
        lines.append(p)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _fmt_score(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "—"


def _group_label(value: str) -> str:
    return {
        "strong": "强候选",
        "watchlist": "观察池",
        "avoid": "规避/风险",
    }.get(value, value)
