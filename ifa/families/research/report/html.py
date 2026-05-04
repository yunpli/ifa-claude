"""HTML renderer for ResearchReport — self-contained Jinja templates.

Inlines CSS so the rendered HTML is portable (email, offline, print).
Templates live in `ifa/families/research/report/templates/`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class HtmlRenderer:
    def __init__(self) -> None:
        self.env = Environment(
            loader=FileSystemLoader(_TEMPLATES_DIR),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._css = (_TEMPLATES_DIR / "styles.css").read_text(encoding="utf-8")
        # Filters
        self.env.filters["status_icon"] = _status_icon
        self.env.filters["status_label_zh"] = _status_label_zh
        self.env.filters["fmt_score"] = _fmt_score
        self.env.filters["fmt_pct"] = _fmt_pct

    def render(self, *, report: dict[str, Any]) -> str:
        template = self.env.get_template("research_report.html")
        return template.render(report=report, inline_css=self._css)


# ─── Filters ──────────────────────────────────────────────────────────────────

_STATUS_ICON = {
    "green": "🟢", "yellow": "🟡", "red": "🔴", "unknown": "⬜",
}
_STATUS_LABEL = {
    "green": "健康", "yellow": "谨慎", "red": "高风险", "unknown": "数据不足",
}


def _status_icon(s: str | None) -> str:
    return _STATUS_ICON.get(s or "unknown", "⬜")


def _status_label_zh(s: str | None) -> str:
    return _STATUS_LABEL.get(s or "unknown", "—")


def _fmt_score(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(v: Any, scale_to_pct: bool = True) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        return f"{f*100:.0f}%" if scale_to_pct else f"{f:.0f}%"
    except (TypeError, ValueError):
        return "—"
