"""HTML renderer for Stock Edge — Research-style institutional template."""
from __future__ import annotations

from pathlib import Path
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
