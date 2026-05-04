"""Research family report layer — builds and renders a per-company research report.

Pipeline:
  CompanyFinancialSnapshot + factor results + scoring + timeline
       │
       ▼  builder.build_research_report()
  ResearchReport  (typed dict, fully rendered-data-ready)
       │
       ├──▶ markdown.render(report) → str (terminal preview)
       └──▶ html.HtmlRenderer().render(report) → str (deliverable)

The builder is rules-only. LLM commentary is opt-in via report/llm_aug.py
(not yet implemented).
"""
from ifa.families.research.report.builder import ResearchReport, build_research_report
from ifa.families.research.report.markdown import render_markdown

__all__ = ["ResearchReport", "build_research_report", "render_markdown"]
