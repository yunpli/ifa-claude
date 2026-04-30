"""Jinja2 renderer for iFA reports.

Inlines the CSS so the rendered HTML is fully self-contained — survives email,
offline viewing, and printing without external asset fetches.
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

    def render(self, *, report: dict[str, Any]) -> str:
        template = self.env.get_template("report.html")
        return template.render(report=report, inline_css=self._css)
