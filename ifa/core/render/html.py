"""Jinja2 renderer for iFA reports.

Inlines the CSS so the rendered HTML is fully self-contained — survives email,
offline viewing, and printing without external asset fetches.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ifa.core.units import fmt_amt, fmt_pct as _core_fmt_pct, fmt_price
from ifa.core.render.glossary import annotate as _glossary_annotate

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _fmt_pct_safe(v: Any, precision: int = 2) -> str:
    """Format 0-100 percentage. Robust to None / non-numeric input."""
    if v is None:
        return "—"
    try:
        return _core_fmt_pct(float(v), precision=precision)
    except (TypeError, ValueError):
        return "—"


def _fmt_pct_signed(v: Any, precision: int = 2) -> str:
    """Format with explicit + / − sign for direction."""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if f >= 0 else "−"
    return f"{sign}{abs(f):.{precision}f}%"


def _fmt_num_safe(v: Any, precision: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{precision}f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_int_safe(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return "—"


def _fmt_amt_yi(v: Any, precision: int = 2) -> str:
    """Format yuan amount with auto-scale (亿/万亿). Robust to None."""
    if v is None:
        return "—"
    try:
        return fmt_amt(float(v), mode="auto", precision=precision)
    except (TypeError, ValueError):
        return "—"


def _fmt_price_safe(v: Any, precision: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return fmt_price(float(v), precision=precision)
    except (TypeError, ValueError):
        return "—"


def _fmt_dir(v: Any, threshold: float = 0.05) -> str:
    """Direction class: 'up' / 'down' / 'flat' for CSS data-tone."""
    if v is None:
        return "flat"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "flat"
    if f > threshold:
        return "up"
    if f < -threshold:
        return "down"
    return "flat"


class HtmlRenderer:
    def __init__(self) -> None:
        self.env = Environment(
            loader=FileSystemLoader(_TEMPLATES_DIR),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Robust formatting filters — usable from any template as
        # {{ value|fmt_pct }} / |fmt_pct_signed / |fmt_amt_yi / |fmt_num /
        # |fmt_int / |fmt_price / |fmt_dir
        self.env.filters["fmt_pct"] = _fmt_pct_safe
        self.env.filters["fmt_pct_signed"] = _fmt_pct_signed
        self.env.filters["fmt_num"] = _fmt_num_safe
        self.env.filters["fmt_int"] = _fmt_int_safe
        self.env.filters["fmt_amt_yi"] = _fmt_amt_yi
        self.env.filters["fmt_price"] = _fmt_price_safe
        self.env.filters["fmt_dir"] = _fmt_dir
        self.env.filters["ifa_term"] = _glossary_annotate
        self._css = (_TEMPLATES_DIR / "styles.css").read_text(encoding="utf-8")

    def render(self, *, report: dict[str, Any]) -> str:
        template = self.env.get_template("report.html")
        return template.render(report=report, inline_css=self._css)
