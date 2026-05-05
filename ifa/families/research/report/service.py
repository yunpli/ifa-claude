"""Programmatic Research report generation/reuse service.

Stock Edge and other families should call this layer instead of shelling out to
`ifa research report`. It keeps the same local-first reuse semantics as the CLI
while making Research deep reports available as durable dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from ifa.config import Settings, get_settings
from ifa.core.report.output import output_dir_for_family
from ifa.core.report.timezones import bjt_now
from ifa.families.research.analyzer.balance import compute_balance
from ifa.families.research.analyzer.cash_quality import compute_cash_quality
from ifa.families.research.analyzer.data import load_company_snapshot
from ifa.families.research.analyzer.factors import load_params
from ifa.families.research.analyzer.governance import compute_governance
from ifa.families.research.analyzer.growth import compute_growth
from ifa.families.research.analyzer.peer import attach_peer_ranks
from ifa.families.research.analyzer.persistence import persist_all_families
from ifa.families.research.analyzer.profitability import compute_profitability
from ifa.families.research.analyzer.scoring import score_results
from ifa.families.research.fetcher.client import fetch_all, fetch_stock_basic
from ifa.families.research.memory import find_reusable_report, record_report_asset
from ifa.families.research.report import build_research_report, render_markdown
from ifa.families.research.report.html import HtmlRenderer
from ifa.families.research.resolver import CompanyNotFoundError, CompanyRef, resolve, upsert_company_identity


@dataclass(frozen=True)
class EnsuredResearchReport:
    ts_code: str
    name: str
    analysis_type: str
    tier: str
    reused: bool
    status: str
    html_path: str | None
    md_path: str | None = None
    run_id: str | None = None
    latest_period: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "analysis_type": self.analysis_type,
            "tier": self.tier,
            "reused": self.reused,
            "status": self.status,
            "html_path": self.html_path,
            "md_path": self.md_path,
            "run_id": self.run_id,
            "latest_period": self.latest_period,
            "message": self.message,
        }


def ensure_research_report(
    engine: Engine,
    *,
    ts_code: str,
    analysis_type: str,
    tier: str = "deep",
    cutoff_date: date | None = None,
    settings: Settings | None = None,
    reuse: bool = True,
    llm: bool = True,
    llm_timeout_seconds: float = 45.0,
    pdf: bool = False,
    triggered_by: str = "stock_edge",
) -> EnsuredResearchReport:
    """Ensure one Research report exists and return its durable asset path."""
    if analysis_type not in {"annual", "quarterly"}:
        raise ValueError(f"analysis_type must be annual|quarterly, got {analysis_type!r}")
    if tier not in {"quick", "standard", "deep"}:
        raise ValueError(f"tier must be quick|standard|deep, got {tier!r}")
    settings = settings or get_settings()
    cutoff = cutoff_date or bjt_now().date()
    company = _resolve_or_bootstrap(ts_code, engine)
    analysis = _analyze_company(engine, company, cutoff)
    snap = analysis["snap"]
    latest_period = _latest_period_for_lens(snap, analysis_type)

    if reuse:
        reusable = find_reusable_report(
            engine,
            ts_code=company.ts_code,
            analysis_type=analysis_type,
            tier=tier,
            latest_period=latest_period,
        )
        if reusable and reusable.get("output_html_path") and Path(str(reusable["output_html_path"])).exists():
            scope = reusable.get("scope_json") or {}
            return EnsuredResearchReport(
                ts_code=company.ts_code,
                name=company.name,
                analysis_type=analysis_type,
                tier=tier,
                reused=True,
                status="reused",
                html_path=str(reusable["output_html_path"]),
                md_path=str(scope.get("md_path")) if scope.get("md_path") else None,
                run_id=str(reusable.get("run_id")) if reusable.get("run_id") else None,
                latest_period=scope.get("latest_period") or latest_period,
                message="reused existing Research report",
            )

    augmenter = None
    if llm and tier != "quick":
        from ifa.core.llm.client import LLMClient
        from ifa.families.research.report.llm_aug import LLMAugmenter

        augmenter = LLMAugmenter(client=LLMClient(request_timeout=llm_timeout_seconds), cache_engine=engine)
    report = build_research_report(
        snap,
        analysis["results_by_family"],
        analysis["scoring"],
        analysis["params"],
        tier=tier,
        analysis_type=analysis_type,
        augmenter=augmenter,
        engine=engine,
        run_mode=settings.run_mode.value,
    )
    out_dir = output_dir_for_family(settings, "research", bjt_now().date())
    suffix = f"-{analysis_type}-{tier}"
    stamp = bjt_now().strftime("%Y%m%d")
    base = f"Stock-Analysis-{company.ts_code}-{stamp}{suffix}"
    html_path = out_dir / f"{base}.html"
    md_path = out_dir / f"{base}.md"
    html_path.write_text(HtmlRenderer().render(report=report), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    pdf_path: Path | None = None
    if pdf:
        from ifa.core.render.pdf import html_to_pdf

        pdf_path = html_to_pdf(html_path)
    run_id = record_report_asset(
        engine,
        report=report,
        html_path=str(html_path),
        md_path=str(md_path),
        pdf_path=str(pdf_path) if pdf_path else None,
        triggered_by=triggered_by,
    )
    return EnsuredResearchReport(
        ts_code=company.ts_code,
        name=company.name,
        analysis_type=analysis_type,
        tier=tier,
        reused=False,
        status="generated",
        html_path=str(html_path),
        md_path=str(md_path),
        run_id=run_id,
        latest_period=latest_period,
        message="generated Research report",
    )


def _resolve_or_bootstrap(ts_code: str, engine: Engine) -> CompanyRef:
    try:
        return resolve(ts_code, engine)
    except CompanyNotFoundError:
        rows = fetch_stock_basic(engine, ts_code)
        if not rows:
            raise
        info = rows[0]
        upsert_company_identity(
            engine,
            ts_code=ts_code,
            name=str(info.get("name") or ""),
            exchange=str(info.get("exchange") or ""),
            market=info.get("market"),
            list_status=info.get("list_status"),
        )
        return resolve(ts_code, engine)


def _analyze_company(engine: Engine, company: CompanyRef, cutoff_date: date) -> dict[str, Any]:
    fetch_all(engine, company.ts_code, company.exchange, verbose=False)
    snap = load_company_snapshot(engine, company, data_cutoff_date=cutoff_date)
    params = load_params()
    results_by_family = {
        "profitability": compute_profitability(snap, params),
        "growth": compute_growth(snap, params),
        "cash_quality": compute_cash_quality(snap, params),
        "balance": compute_balance(snap, params),
        "governance": compute_governance(snap, params),
    }
    persist_all_families(engine, company.ts_code, results_by_family)
    for results in results_by_family.values():
        attach_peer_ranks(engine, results, snap)
    persist_all_families(engine, company.ts_code, results_by_family)
    scoring = score_results(results_by_family, params)
    return {
        "snap": snap,
        "params": params,
        "results_by_family": results_by_family,
        "scoring": scoring,
    }


def _latest_period_for_lens(snap: Any, analysis_type: str) -> str | None:
    periods: set[str] = set()
    for ts in (
        getattr(snap, "revenue_series", None),
        getattr(snap, "n_income_series", None),
        getattr(snap, "cfo_series", None),
        getattr(snap, "roe_series", None),
        getattr(snap, "gpm_series", None),
    ):
        if ts is not None:
            periods.update(str(p) for p in ts.periods)
    if analysis_type == "annual":
        periods = {p for p in periods if p.endswith("1231")}
    if periods:
        return sorted(periods)[-1]
    return getattr(snap, "latest_period", None)
