"""Write Stock Edge report assets under the configured ifaenv output root."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ifa.config import Settings
from ifa.core.report.timezones import bjt_now
from ifa.families.stock.analysis import StockEdgeAnalysis
from ifa.families.stock.output import output_dir_for_stock_edge

from .builder import build_report_model
from .html import render_html
from .markdown import render_markdown


@dataclass(frozen=True)
class RenderedStockEdgeReport:
    html_path: Path
    md_path: Path


def render_report_assets(analysis: StockEdgeAnalysis, settings: Settings) -> RenderedStockEdgeReport:
    report = build_report_model(analysis)
    out_dir = output_dir_for_stock_edge(
        settings,
        analysis.ctx.as_of.as_of_trade_date,
        run_mode=analysis.ctx.request.run_mode,
    )
    stamp = bjt_now().strftime("%H%M%S")
    code = analysis.ctx.request.ts_code.replace(".", "_")
    base = f"CN_stock_edge_{code}_{analysis.ctx.as_of.as_of_trade_date.strftime('%Y%m%d')}_{stamp}"
    html_path = out_dir / f"{base}.html"
    md_path = out_dir / f"{base}.md"
    html_path.write_text(render_html(report), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return RenderedStockEdgeReport(html_path=html_path, md_path=md_path)
