"""HTML renderer for Stock Edge — Research-style institutional template."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_RESEARCH_STYLES = Path(__file__).parents[2] / "research" / "report" / "templates" / "styles.css"


class HtmlRenderer:
    def __init__(self) -> None:
        self.env = Environment(
            loader=FileSystemLoader(_TEMPLATES_DIR),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        base_css = _RESEARCH_STYLES.read_text(encoding="utf-8")
        local_css = (_TEMPLATES_DIR / "styles.css").read_text(encoding="utf-8")
        self._css = f"{base_css}\n\n{local_css}"
        self.env.filters["fmt_pct"] = _fmt_pct
        self.env.filters["fmt_pct_raw"] = _fmt_pct_raw
        self.env.filters["fmt_signed_pct_raw"] = _fmt_signed_pct_raw
        self.env.filters["fmt_price"] = _fmt_price
        self.env.filters["fmt_score"] = _fmt_score
        self.env.filters["fmt_signed_score"] = _fmt_signed_score
        self.env.filters["human_signal"] = _human_signal
        self.env.filters["cn_level"] = _cn_level

    def render(self, *, report: dict[str, Any]) -> str:
        template = self.env.get_template("stock_edge_report.html")
        return template.render(report=report, inline_css=self._css)


def render_html(report: dict[str, Any]) -> str:
    return HtmlRenderer().render(report=report)


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_price(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_signed_pct_raw(v: Any) -> str:
    if v is None:
        return "—"
    try:
        value = float(v)
        sign = "+" if value > 0 else ""
        return f"{sign}{value:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct_raw(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_score(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_signed_score(v: Any) -> str:
    if v is None:
        return "—"
    try:
        value = float(v)
        sign = "+" if value > 0 else ""
        return f"{sign}{value:.2f}"
    except (TypeError, ValueError):
        return "—"


def _human_signal(v: Any) -> str:
    """Remove backtest/model jargon from user-facing report evidence."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        text = "；".join(str(item) for item in v if item)
    else:
        text = str(v)
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
    text = re.sub(r"。\s*。+", "。", text)
    return text.strip("； ")


def _cn_level(v: Any) -> str:
    mapping = {
        "high": "高",
        "medium": "中",
        "low": "低",
        "extreme": "极高",
    }
    return mapping.get(str(v), str(v))
