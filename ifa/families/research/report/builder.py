"""Research report builder — turns analyzer outputs into a renderable dict.

The output is a plain dict (`ResearchReport` is just a TypedDict alias for
documentation). This keeps it serializable for caching / API responses and
agnostic to the eventual renderer.

Section types produced:
  · overview     — company name / SW classification / data status
  · radar        — 5-dim scores + overall verdict
  · factor_table — one per family, with values/status/peer rank/notes
  · trend_grid   — 5 key series with arrow + slope
  · red_flags    — auto-extracted RED + select YELLOW notes
  · timeline     — recent events (chronological, newest first)
"""
from __future__ import annotations

from typing import Any

from ifa.core.report.disclaimer import (
    DISCLAIMER_PARAGRAPHS_EN,
    DISCLAIMER_PARAGRAPHS_ZH,
    FOOTER_SHORT_EN,
    FOOTER_SHORT_ZH,
)
from ifa.core.report.timezones import bjt_now, fmt_bjt
from ifa.families.research.analyzer.data import CompanyFinancialSnapshot
from ifa.families.research.analyzer.factors import FactorResult, FactorStatus
from ifa.families.research.analyzer.scoring import (
    FAMILY_LABEL_ZH,
    FAMILY_ORDER,
    ScoringResult,
)
from ifa.families.research.analyzer.timeline import TimelineEvent, build_timeline
from ifa.families.research.analyzer.trends import (
    TrendResult,
    classify_trend_from_params,
)
from ifa.families.research.report.sparkline import render_sparkline

# Public typed alias — the actual return type is dict[str, Any]
ResearchReport = dict[str, Any]


_VALID_TIERS = ("quick", "standard", "deep")


def build_research_report(
    snap: CompanyFinancialSnapshot,
    results_by_family: dict[str, list[FactorResult]],
    scoring: ScoringResult,
    params: dict,
    *,
    tier: str = "standard",
    timeline_limit: int = 10,
    template_version: str = "research_v2.2",
    run_mode: str = "manual",
    augmenter: object | None = None,  # LLMAugmenter (avoids hard import cycle)
    engine: object | None = None,     # SQLAlchemy Engine; enables event_memory enrichment
) -> ResearchReport:
    """Assemble all sections into a single report dict ready for rendering.

    Tier matrix (controls section inclusion + LLM usage):
        ┌─────────────────┬───────┬──────────┬──────┐
        │ Section         │ quick │ standard │ deep │
        ├─────────────────┼───────┼──────────┼──────┤
        │ overview        │   ✓   │    ✓     │  ✓   │
        │ radar           │   ✓   │    ✓     │  ✓   │
        │ factor tables   │   ✓   │    ✓     │  ✓   │
        │ red_flags       │   ✓   │    ✓     │  ✓   │
        │ disclaimer      │   ✓   │    ✓     │  ✓   │
        │ trend_grid      │       │    ✓     │  ✓   │
        │ timeline        │       │    ✓     │  ✓   │
        │ LLM narratives  │       │    *     │  *   │
        │ watchpoints     │       │          │  *   │
        └─────────────────┴───────┴──────────┴──────┘
    * = only when augmenter is provided. Quick always ignores augmenter
        (saves API cost; quick is meant to be ~5s deterministic snapshot).
    """
    if tier not in _VALID_TIERS:
        raise ValueError(f"tier must be one of {_VALID_TIERS}, got {tier!r}")

    # Always render report timestamps in Beijing time, regardless of where
    # the machine is located. The system stores UTC and displays BJT.
    now_bjt_str = fmt_bjt(bjt_now())
    cutoff = snap.data_cutoff_date.isoformat()

    include_trends = tier in ("standard", "deep")
    include_timeline = tier in ("standard", "deep")
    include_watchpoints = tier == "deep"
    # Quick tier never invokes LLM regardless of augmenter — saves API cost.
    effective_augmenter = augmenter if tier != "quick" else None

    # Compute all narratives in one batched parallel call (6× speedup vs
    # sequential when narratives are not cached).
    narratives: dict[str, Any] = {}
    if effective_augmenter is not None:
        try:
            narratives = effective_augmenter.narratives_for_report(
                snap.company.ts_code, scoring, results_by_family,
            )
        except Exception:
            narratives = {}

    sections: list[dict] = [
        _section_overview(snap),
        _section_radar(scoring, narratives.get("overall", "")),
    ]

    for fam in FAMILY_ORDER:
        results = results_by_family.get(fam, [])
        if not results:
            continue
        sections.append(_section_factor_table(
            fam, results, scoring, narratives.get(fam, ""),
        ))

    if include_trends:
        sections.append(_section_trend_grid(snap, params))
    sections.append(_section_red_flags(results_by_family))
    if include_watchpoints:
        watchpoints = narratives.get("watchpoints", [])
        if watchpoints:
            sections.append(_section_watchpoints(watchpoints))
    if include_timeline:
        sections.append(_section_timeline(snap, timeline_limit, engine=engine))
    sections.append(_section_disclaimer())

    return {
        "title": f"个股研究报告 · {snap.company.name or snap.company.ts_code}",
        "subtitle_en": f"Equity Research · {snap.company.ts_code}",
        "ts_code": snap.company.ts_code,
        "company_name": snap.company.name,
        "tier": tier,
        "report_date_bjt": now_bjt_str,
        "data_cutoff_bjt": cutoff,
        "template_version": template_version,
        "run_mode": run_mode,
        "overall_score": scoring.overall_score,
        "overall_status": scoring.overall_status.value,
        "overall_label_zh": scoring.overall_label_zh,
        "footer_short_zh": FOOTER_SHORT_ZH,
        "footer_short_en": FOOTER_SHORT_EN,
        "sections": sections,
    }


# ─── Sections ─────────────────────────────────────────────────────────────────

def _section_overview(snap: CompanyFinancialSnapshot) -> dict:
    return {
        "type": "research_overview",
        "ts_code": snap.company.ts_code,
        "name": snap.company.name,
        "exchange": snap.company.exchange,
        "industry": snap.industry,
        "sw_l1": _format_sw(snap.sw_l1_code, snap.sw_l1_name),
        "sw_l2": _format_sw(snap.sw_l2_code, snap.sw_l2_name),
        "list_date": snap.list_date.isoformat() if snap.list_date else None,
        "main_business": (snap.main_business or "")[:300],
        "introduction": (snap.introduction or "")[:500],
        "employees": snap.employees,
        "latest_period": snap.latest_period,
        "missing_apis": list(snap.missing_apis),
    }


def _section_radar(scoring: ScoringResult, narrative: str = "") -> dict:
    return {
        "type": "research_radar",
        "overall_score": scoring.overall_score,
        "overall_status": scoring.overall_status.value,
        "overall_label_zh": scoring.overall_label_zh,
        "narrative": narrative,
        "families": [
            {
                "key": fam,
                "label_zh": fs.label_zh,
                "score": fs.score,
                "status": fs.status.value,
                "weight_coverage": fs.weight_coverage,
            }
            for fam, fs in scoring.families.items()
        ],
        "radar_points": [
            {"label": label, "score": score}
            for label, score in scoring.radar.items()
        ],
    }


def _section_factor_table(
    family: str,
    results: list[FactorResult],
    scoring: ScoringResult,
    narrative: str = "",
) -> dict:
    fs = scoring.families.get(family)
    rows = []
    for r in results:
        rows.append({
            "name": r.spec.name,
            "name_zh": r.spec.display_name_zh,
            "value": _format_value(r.value, r.spec.unit),
            "raw_value": _to_jsonable(r.value),
            "unit": r.spec.unit,
            "status": r.status.value,
            "period": r.period,
            "peer_rank": r.peer_rank,
            "peer_percentile": r.peer_percentile,
            "notes": list(r.notes),
            "history": list(r.history),
            "history_periods": list(r.history_periods),
        })
    return {
        "type": "research_factor_table",
        "family": family,
        "family_label_zh": FAMILY_LABEL_ZH.get(family, family),
        "family_score": fs.score if fs else None,
        "family_status": fs.status.value if fs else "unknown",
        "weight_coverage": fs.weight_coverage if fs else 0.0,
        "narrative": narrative,
        "rows": rows,
    }


def _section_trend_grid(snap: CompanyFinancialSnapshot, params: dict) -> dict:
    targets = [
        ("营收", snap.revenue_series),
        ("净利", snap.n_income_series),
        ("ROE", snap.roe_series),
        ("毛利率", snap.gpm_series),
        ("CFO", snap.cfo_series),
        ("应收账款", snap.accounts_receiv_series),
        ("存货", snap.inventories_series),
    ]
    entries = []
    for label, ts in targets:
        if ts is None or not ts.values:
            entries.append({"label": label, "level": "unknown",
                            "label_zh": "无数据", "arrow": "?",
                            "slope_pct_per_period": None, "n_periods": 0,
                            "history": [], "history_periods": [],
                            "sparkline_svg": ""})
            continue
        tr: TrendResult = classify_trend_from_params(ts.values, params)
        entries.append({
            "label": label,
            "level": tr.level.value,
            "label_zh": tr.label_zh,
            "arrow": tr.arrow,
            "slope_pct_per_period": tr.slope_pct_per_period,
            "n_periods": tr.n_periods,
            "history": list(ts.values),
            "history_periods": list(ts.periods),
            "sparkline_svg": render_sparkline(ts.values, width=120, height=28),
        })
    return {"type": "research_trend_grid", "entries": entries}


def _section_red_flags(
    results_by_family: dict[str, list[FactorResult]],
) -> dict:
    """Aggregate every RED + concerning YELLOW into a single risk list."""
    flags: list[dict] = []
    for fam, results in results_by_family.items():
        for r in results:
            if r.status == FactorStatus.RED or (r.status == FactorStatus.YELLOW and r.notes):
                flags.append({
                    "family": fam,
                    "family_label_zh": FAMILY_LABEL_ZH.get(fam, fam),
                    "factor": r.spec.name,
                    "factor_zh": r.spec.display_name_zh,
                    "status": r.status.value,
                    "value": _format_value(r.value, r.spec.unit),
                    "notes": list(r.notes),
                })
    # RED first, then YELLOW
    flags.sort(key=lambda f: (0 if f["status"] == "red" else 1, f["family"]))
    return {"type": "research_red_flags", "flags": flags, "count": len(flags)}


def _section_watchpoints(items: list[dict]) -> dict:
    """LLM-synthesized actionable observations (3-5 items, severity-ranked).

    NOTE: dict key MUST NOT be 'items' — Jinja resolves obj.items as the
    dict.items() method, shadowing key lookup. Use 'entries' (same lesson
    learned in trend_grid).
    """
    return {
        "type": "research_watchpoints",
        "entries": items,
        "count": len(items),
    }


def _section_disclaimer() -> dict:
    """Full bilingual disclaimer (10 paragraphs ZH + 10 EN), required for V2.2 GA.

    Pulled from `ifa.core.report.disclaimer` so all family reports stay
    in sync when legal text is updated.
    """
    return {
        "type": "research_disclaimer",
        "paragraphs_zh": list(DISCLAIMER_PARAGRAPHS_ZH),
        "paragraphs_en": list(DISCLAIMER_PARAGRAPHS_EN),
    }


def _section_timeline(
    snap: CompanyFinancialSnapshot,
    limit: int,
    engine: object | None = None,
) -> dict:
    events: list[TimelineEvent] = build_timeline(snap, engine=engine)[:limit]
    return {
        "type": "research_timeline",
        "events": [
            {
                "event_type": e.event_type,
                "publish_time": e.publish_time,
                "title": e.title,
                "summary": e.summary,
                "source_url": e.source_url,
                "polarity": e.polarity,
                "importance": e.importance,
                "is_extracted": e.is_extracted,
            }
            for e in events
        ],
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _format_sw(code: str | None, name: str | None) -> str | None:
    if not code and not name:
        return None
    if code and name:
        return f"{name}（{code}）"
    return name or code


def _format_value(value: Any, unit: str) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit == "元":
        # Render in 亿元 if magnitude warrants; preserves sign.
        if abs(v) >= 1e8:
            return f"{v / 1e8:.2f} 亿元"
        if abs(v) >= 1e4:
            return f"{v / 1e4:.2f} 万元"
        return f"{v:.0f} 元"
    if unit == "%" or unit == "pp":
        return f"{v:+.2f}{unit}" if unit == "pp" else f"{v:.2f}%"
    if unit == "x":
        return f"{v:.2f}x"
    if unit == "天":
        return f"{v:+.1f} 天" if unit == "天" and v < 0 else f"{v:.1f} 天"
    if unit == "次":
        return f"{int(v)} 次"
    return f"{v:.2f}"


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)
