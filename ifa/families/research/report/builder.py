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

import re
import hashlib
from typing import Any

from ifa.core.report.disclaimer import (
    DISCLAIMER_PARAGRAPHS_EN,
    DISCLAIMER_PARAGRAPHS_ZH,
)
from ifa.core.report.timezones import bjt_now, fmt_bjt
from ifa.families.research.analyzer.data import CompanyFinancialSnapshot
from ifa.families.research.analyzer.factors import FactorResult, FactorStatus
from ifa.families.research.analyzer.scoring import (
    FAMILY_LABEL_ZH,
    FAMILY_ORDER,
    ScoringResult,
)
from ifa.families.research.analyzer.analyst_coverage import compute_coverage
from ifa.families.research.analyzer.tensions import detect_tensions
from ifa.families.research.analyzer.timeline import TimelineEvent, build_timeline
from ifa.families.research.analyzer.trends import (
    TrendResult,
    classify_trend_from_params,
)
from ifa.families.research.report.sparkline import render_sparkline
from ifa.families.research.fetcher.pdf import fetch_and_extract
from ifa.families.research.fetcher.cache import computed_get, computed_set
from ifa.families.research.memory import (
    load_pdf_extract,
    stable_hash,
    upsert_pdf_extract,
    upsert_period_factor_decomposition,
)

# Public typed alias — the actual return type is dict[str, Any]
ResearchReport = dict[str, Any]


_VALID_TIERS = ("quick", "standard", "deep")
_VALID_ANALYSIS_TYPES = ("quarterly", "annual")
_ANALYSIS_TYPE_LABELS = {
    "quarterly": "季报分析",
    "annual": "年报分析",
}
_REPORT_TITLE_LABELS = {
    ("quarterly", "quick"): "季报速览",
    ("quarterly", "standard"): "季报分析",
    ("quarterly", "deep"): "季报深度对比分析",
    ("annual", "quick"): "年报速览",
    ("annual", "standard"): "年报分析",
    ("annual", "deep"): "年报深度对比分析",
}
_SUBTITLE_LABELS = {
    ("quarterly", "quick"): "Quarterly Quick Financial Review",
    ("quarterly", "standard"): "Quarterly Financial Analysis",
    ("quarterly", "deep"): "Quarterly Deep Comparative Analysis",
    ("annual", "quick"): "Annual Quick Financial Review",
    ("annual", "standard"): "Annual Financial Analysis",
    ("annual", "deep"): "Annual Deep Comparative Analysis",
}
_TIER_LABELS = {
    "quick": "Quick",
    "standard": "Standard",
    "deep": "Deep",
}


def build_research_report(
    snap: CompanyFinancialSnapshot,
    results_by_family: dict[str, list[FactorResult]],
    scoring: ScoringResult,
    params: dict,
    *,
    tier: str = "standard",
    analysis_type: str = "annual",
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
    if analysis_type not in _VALID_ANALYSIS_TYPES:
        raise ValueError(
            f"analysis_type must be one of {_VALID_ANALYSIS_TYPES}, got {analysis_type!r}"
        )

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
                irm_qa=snap.irm_qa if include_watchpoints else None,
                research_reports=snap.research_reports if include_watchpoints else None,
            )
        except Exception:
            narratives = {}

    sections: list[dict] = [
        _section_overview(snap),
        _section_radar(scoring, narratives.get("overall", "")),
        _section_financial_dashboard(snap, results_by_family, scoring, analysis_type, tier, engine=engine),
        _section_period_analysis(snap, analysis_type, tier),
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
    # Cross-cutting tensions: rule-based, deterministic, always run.
    # Sits between red_flags (single-factor) and watchpoints (LLM-synthesized).
    tensions_section = _section_tensions(results_by_family)
    if tensions_section:
        sections.append(tensions_section)
    sections.append(_section_red_flags(results_by_family))
    if include_watchpoints:
        watchpoints = narratives.get("watchpoints", [])
        if watchpoints:
            sections.append(_section_watchpoints(watchpoints))
        ic_themes = narratives.get("investor_concerns", [])
        if ic_themes:
            sections.append(_section_investor_concerns(ic_themes))
    # Analyst coverage (rule-based, always; LLM themes added when available)
    if include_timeline:
        sec = _section_analyst_coverage(
            snap, narratives.get("analyst_themes", []),
            include_pdf_extracts=tier == "deep",
            engine=engine,
        )
        if sec:
            sections.append(sec)
    if include_timeline:
        sections.append(_section_timeline(snap, timeline_limit, engine=engine))
    sections.append(_section_disclaimer())

    return {
        "title": (
            f"{_REPORT_TITLE_LABELS[(analysis_type, tier)]} · "
            f"{snap.company.name or snap.company.ts_code}"
        ),
        "subtitle_en": f"{_SUBTITLE_LABELS[(analysis_type, tier)]} · {snap.company.ts_code}",
        "ts_code": snap.company.ts_code,
        "company_name": snap.company.name,
        "tier": tier,
        "tier_label": _TIER_LABELS[tier],
        "analysis_type": analysis_type,
        "analysis_type_label": _ANALYSIS_TYPE_LABELS[analysis_type],
        "report_date_bjt": now_bjt_str,
        "data_cutoff_bjt": cutoff,
        "template_version": template_version,
        "run_mode": run_mode,
        "overall_score": scoring.overall_score,
        "overall_status": scoring.overall_status.value,
        "overall_label_zh": scoring.overall_label_zh,
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


def _section_financial_dashboard(
    snap: CompanyFinancialSnapshot,
    results_by_family: dict[str, list[FactorResult]],
    scoring: ScoringResult,
    analysis_type: str,
    tier: str,
    engine: object | None = None,
) -> dict:
    """PM-facing financial dashboard.

    This is the bridge between the 5-dim radar and the factor tables: it turns
    raw factor rows into an investment-workflow view — what is healthy, what is
    fragile, what evidence supports that view, and what to verify next.
    """
    key_metrics = [
        _metric_from_factor("营收增长", _find_result(results_by_family, "REVENUE_YOY")),
        _metric_from_factor("净利增长", _find_result(results_by_family, "N_INCOME_YOY")),
        _metric_from_factor("ROE", _find_result(results_by_family, "ROE")),
        _metric_from_factor("CFO/NI", _find_result(results_by_family, "CFO_TO_NI")),
        _metric_from_factor("资产负债率", _find_result(results_by_family, "DEBT_TO_ASSETS")),
        _metric_from_factor("治理", _find_result(results_by_family, "AUDIT_STANDARD")),
    ]
    key_metrics = [m for m in key_metrics if m]

    cards = []
    for fam in FAMILY_ORDER:
        fs = scoring.families.get(fam)
        rows = results_by_family.get(fam, [])
        worst = _worst_results(rows, limit=2)
        best = _best_results(rows, limit=1)
        spark = _family_sparkline_payload(fam, snap, engine=engine)
        cards.append({
            "family": fam,
            "label": FAMILY_LABEL_ZH.get(fam, fam),
            "score": fs.score if fs else None,
            "status": fs.status.value if fs else "unknown",
            "verdict": _family_verdict(fam, fs.status.value if fs else "unknown", worst, best),
            "evidence": [_evidence_line(r) for r in worst] or [_evidence_line(r) for r in best],
            "next_watch": _next_watch(fam, worst),
            "sparkline_label": spark["label"],
            "sparkline_svg": spark["svg"] if tier == "deep" else "",
            "sparkline_note": spark["note"],
        })

    flags: list[dict] = []
    for fam, rows in results_by_family.items():
        for r in rows:
            if r.status in (FactorStatus.RED, FactorStatus.YELLOW):
                flags.append({
                    "family_label": FAMILY_LABEL_ZH.get(fam, fam),
                    "factor": r.spec.display_name_zh,
                    "status": r.status.value,
                    "value": _format_value(r.value, r.spec.unit),
                    "peer": _format_peer_rank(r),
                    "note": " · ".join(r.notes[:2]) if r.notes else _default_factor_note(r),
                })
    flags.sort(key=lambda f: (0 if f["status"] == "red" else 1, f["family_label"], f["factor"]))

    return {
        "type": "research_financial_dashboard",
        "analysis_type": analysis_type,
        "analysis_type_label": _ANALYSIS_TYPE_LABELS[analysis_type],
        "tier": tier,
        "tier_label": _TIER_LABELS[tier],
        "lens_intro": _analysis_lens_intro(analysis_type, tier),
        "latest_period": snap.latest_period,
        "key_metrics": key_metrics,
        "cards": cards,
        "dimension_charts": _dimension_charts(snap, scoring, engine=engine) if tier == "deep" else [],
        "flags": flags[:8],
        "data_gaps": list(snap.missing_apis)[:8],
    }


def _analysis_lens_intro(analysis_type: str, tier: str) -> str:
    if analysis_type == "quarterly" and tier == "quick":
        return "季报 Quick 只读取最新一个季度，判断收入、利润、现金流和营运资产在本期是否同向。"
    if analysis_type == "annual" and tier == "quick":
        return "年报 Quick 只读取最新一份年报，聚焦年度盈利质量、现金兑现和资产负债结构。"
    if analysis_type == "quarterly":
        return "季报 Deep 最多读取三年季度序列，逐季比较同比与环比，识别边际改善、恶化和季节性噪声。"
    return "年报 Deep 最多读取三年年报序列，比较年度同比与上一年变化，验证增长、盈利质量和治理信号是否稳定。"


def _section_period_analysis(
    snap: CompanyFinancialSnapshot,
    analysis_type: str,
    tier: str,
) -> dict:
    rows = _period_rows(snap, analysis_type=analysis_type, tier=tier)
    latest = rows[-1] if rows else None
    return {
        "type": "research_period_analysis",
        "analysis_type": analysis_type,
        "analysis_type_label": _ANALYSIS_TYPE_LABELS[analysis_type],
        "tier": tier,
        "tier_label": _TIER_LABELS[tier],
        "title": _period_section_title(analysis_type, tier),
        "period_change_label": "环比" if analysis_type == "quarterly" else "较上年",
        "coverage_note": _period_coverage_note(analysis_type, tier, len(rows)),
        "rows": rows,
        "latest": latest,
        "charts": _period_charts(snap, [r["raw_period"] for r in rows]) if tier == "deep" else [],
    }


def _period_section_title(analysis_type: str, tier: str) -> str:
    if analysis_type == "quarterly" and tier == "quick":
        return "最新季报核心数字"
    if analysis_type == "annual" and tier == "quick":
        return "最新年报核心数字"
    if analysis_type == "quarterly":
        return "三年季度序列对比"
    return "三年年报序列对比"


def _period_coverage_note(analysis_type: str, tier: str, n_rows: int) -> str:
    if tier == "quick":
        return "Quick 口径：仅展示最新一个报告期。"
    if analysis_type == "quarterly":
        return f"Deep 口径：最多展示最近 12 个季度；当前可用 {n_rows} 个季度。"
    return f"Deep 口径：最多展示最近 3 份年报；当前可用 {n_rows} 份年报。"


def _period_rows(
    snap: CompanyFinancialSnapshot,
    *,
    analysis_type: str,
    tier: str,
) -> list[dict]:
    source = _period_union(snap)
    if analysis_type == "annual":
        periods = [p for p in source if p.endswith("1231")]
        max_rows = 1 if tier == "quick" else 3
    else:
        periods = list(source)
        max_rows = 1 if tier == "quick" else 12
    periods = periods[-max_rows:]
    return [
        _period_row(
            period,
            revenue=snap.revenue_series,
            n_income=snap.n_income_series,
            cfo=snap.cfo_series,
            roe=snap.roe_series,
            gpm=snap.gpm_series,
            ordered_periods=periods,
            analysis_type=analysis_type,
        )
        for period in periods
    ]


def _period_union(snap: CompanyFinancialSnapshot) -> list[str]:
    periods: set[str] = set()
    for ts in (
        snap.revenue_series,
        snap.n_income_series,
        snap.cfo_series,
        snap.roe_series,
        snap.gpm_series,
    ):
        if ts is not None:
            periods.update(str(p) for p in ts.periods)
    return sorted(periods)


def _period_row(
    period: str,
    *,
    revenue: Any,
    n_income: Any,
    cfo: Any,
    roe: Any,
    gpm: Any,
    ordered_periods: list[str],
    analysis_type: str,
) -> dict:
    prev_period = ordered_periods[ordered_periods.index(period) - 1] if ordered_periods.index(period) > 0 else None
    yoy_period = _same_quarter_last_year(period) if analysis_type == "quarterly" else prev_period
    return {
        "period": _format_period(period),
        "raw_period": period,
        "revenue": _format_series_value(revenue, period),
        "revenue_yoy": _format_pct(_series_change(revenue, period, yoy_period)),
        "revenue_period_change": _format_pct(_series_change(revenue, period, prev_period)),
        "n_income": _format_series_value(n_income, period),
        "n_income_yoy": _format_pct(_series_change(n_income, period, yoy_period)),
        "n_income_period_change": _format_pct(_series_change(n_income, period, prev_period)),
        "cfo": _format_series_value(cfo, period),
        "roe": _format_series_value(roe, period),
        "gpm": _format_series_value(gpm, period),
    }


def _same_quarter_last_year(period: str) -> str | None:
    if len(period) != 8 or not period[:4].isdigit():
        return None
    return f"{int(period[:4]) - 1}{period[4:]}"


def _series_map(ts: Any) -> dict[str, float | None]:
    if ts is None:
        return {}
    return {str(p): v for p, v in zip(ts.periods, ts.values)}


def _series_change(ts: Any, period: str, base_period: str | None) -> float | None:
    if not base_period:
        return None
    values = _series_map(ts)
    v = values.get(period)
    base = values.get(base_period)
    if v is None or base in (None, 0):
        return None
    return (v - base) / abs(base) * 100


def _format_series_value(ts: Any, period: str) -> str:
    values = _series_map(ts)
    value = values.get(period)
    unit = getattr(ts, "unit", "") if ts is not None else ""
    return _format_value(value, unit)


def _format_period(period: str) -> str:
    if len(period) == 8:
        return f"{period[:4]}-{period[4:6]}-{period[6:]}"
    return period


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def _period_charts(snap: CompanyFinancialSnapshot, periods: list[str]) -> list[dict]:
    specs = [
        ("营收", snap.revenue_series, "bar"),
        ("净利润", snap.n_income_series, "bar"),
        ("CFO", snap.cfo_series, "bar"),
        ("ROE", snap.roe_series, "line"),
        ("毛利率", snap.gpm_series, "line"),
    ]
    charts = []
    for label, ts, kind in specs:
        if ts is None:
            continue
        values = [_series_map(ts).get(p) for p in periods]
        if len([v for v in values if v is not None]) < 2:
            continue
        unit = getattr(ts, "unit", "")
        scaled_values, scaled_unit = _scale_chart_values(values, unit)
        charts.append({
            "label": label,
            "unit": scaled_unit,
            "kind": kind,
            "svg": _render_period_chart(scaled_values, periods, kind=kind),
            "latest": _format_value(values[-1], unit),
            "change": _format_pct(_change_pct(values)),
        })
    return charts


def _scale_chart_values(values: list[float | None], unit: str) -> tuple[list[float | None], str]:
    if unit == "元":
        return ([None if v is None else v / 1e8 for v in values], "亿元")
    return values, unit


def _change_pct(values: list[float | None]) -> float | None:
    finite = [v for v in values if v is not None]
    if len(finite) < 2 or finite[0] == 0:
        return None
    return (finite[-1] - finite[0]) / abs(finite[0]) * 100


def _render_period_chart(
    values: list[float | None],
    periods: list[str],
    *,
    kind: str,
    width: int = 260,
    height: int = 118,
) -> str:
    finite = [v for v in values if v is not None]
    if len(finite) < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 28, 8, 12, 24
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b
    vmin = min(0, min(finite)) if kind == "bar" else min(finite)
    vmax = max(finite)
    span = (vmax - vmin) or 1.0

    def x(i: int) -> float:
        return pad_l + inner_w * i / max(1, len(values) - 1)

    def y(v: float) -> float:
        return pad_t + inner_h * (1 - (v - vmin) / span)

    first = finite[0]
    last = finite[-1]
    stroke = "#991b1b" if first and (last - first) / abs(first) * 100 > 2 else "#166534" if first and (last - first) / abs(first) * 100 < -2 else "#64748b"
    zero_y = y(0) if vmin <= 0 <= vmax else pad_t + inner_h
    labels = [p[2:4] + "/" + p[4:6] for p in periods]
    parts = [
        f'<svg class="period-chart-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="趋势图">',
        f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{width-pad_r}" y2="{zero_y:.1f}" stroke="#d8dde6" stroke-width="1"/>',
    ]
    if kind == "bar":
        bar_w = max(5, inner_w / max(1, len(values)) * 0.58)
        for i, v in enumerate(values):
            if v is None:
                continue
            xi = x(i) - bar_w / 2
            yi = min(y(v), zero_y)
            h = abs(zero_y - y(v))
            fill = "#991b1b" if v >= 0 else "#166534"
            parts.append(f'<rect x="{xi:.1f}" y="{yi:.1f}" width="{bar_w:.1f}" height="{max(h, 1):.1f}" rx="1.5" fill="{fill}" opacity="0.82"/>')
    else:
        path = []
        pen = False
        for i, v in enumerate(values):
            if v is None:
                pen = False
                continue
            path.append(("L" if pen else "M") + f"{x(i):.1f},{y(v):.1f}")
            pen = True
        parts.append(f'<path d="{" ".join(path)}" fill="none" stroke="{stroke}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        for i, v in enumerate(values):
            if v is not None:
                parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.2" fill="{stroke}"/>')
    for idx in sorted({0, len(labels) - 1}):
        if 0 <= idx < len(labels):
            anchor = "start" if idx == 0 else "end"
            parts.append(f'<text x="{x(idx):.1f}" y="{height-6}" text-anchor="{anchor}" font-size="9" fill="#64748b">{labels[idx]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _family_sparkline_payload(
    family: str,
    snap: CompanyFinancialSnapshot,
    *,
    engine: object | None = None,
) -> dict:
    """Return a representative historical series for the 5-dim comparison.

    Use canonical financial series instead of FactorResult.history because some
    factors store raw source histories while their latest value is a derived
    ratio. This keeps labels, numbers, and colors aligned.
    """
    compute_key = f"period_factor_decomposition:{family}:v1"
    inputs_hash = stable_hash({
        "family": family,
        "latest_period": snap.latest_period,
        "revenue": _ts_payload(snap.revenue_series),
        "n_income": _ts_payload(snap.n_income_series),
        "cfo": _ts_payload(snap.cfo_series),
        "roe": _ts_payload(snap.roe_series),
        "liab": _ts_payload(snap.total_liab_series),
        "assets": _ts_payload(snap.total_assets_series),
        "audit": [
            {"end_date": r.get("end_date"), "audit_result": r.get("audit_result")}
            for r in snap.audit_records
        ],
    })
    if engine is not None:
        cached = computed_get(engine, snap.company.ts_code, compute_key, inputs_hash)
        if cached is not None:
            _persist_period_factor_payload(engine, snap.company.ts_code, family, cached, inputs_hash)
            return cached

    if family == "profitability":
        payload = _payload_from_ts("ROE", snap.roe_series)
    elif family == "growth":
        payload = _payload_from_ts("营收同比增速", snap.revenue_series, use_yoy=True)
    elif family == "cash_quality":
        payload = _payload_from_aligned_ratio("CFO/NI", snap.cfo_series, snap.n_income_series)
    elif family == "balance":
        payload = _payload_from_aligned_ratio(
            "资产负债率", snap.total_liab_series, snap.total_assets_series, multiplier=100
        )
    elif family == "governance":
        payload = _payload_from_audit_records(snap.audit_records)
    else:
        payload = _empty_payload("可比历史不足")

    if engine is not None:
        computed_set(engine, snap.company.ts_code, compute_key, inputs_hash, payload)
        _persist_period_factor_payload(engine, snap.company.ts_code, family, payload, inputs_hash)
    return payload


def _dimension_charts(
    snap: CompanyFinancialSnapshot,
    scoring: ScoringResult,
    *,
    engine: object | None = None,
) -> list[dict]:
    charts = []
    for fam in FAMILY_ORDER:
        payload = _family_sparkline_payload(fam, snap, engine=engine)
        fs = scoring.families.get(fam)
        charts.append({
            "family": fam,
            "label": FAMILY_LABEL_ZH.get(fam, fam),
            "score": fs.score if fs else None,
            "status": fs.status.value if fs else "unknown",
            "factor": payload["label"],
            "periods": payload["periods"],
            "sparkline_svg": render_sparkline(payload["values"], width=150, height=34),
            "note": payload["note"],
        })
    return charts


def _empty_payload(label: str, note: str = "") -> dict:
    return {"label": label, "periods": "", "raw_periods": [], "values": [], "svg": "", "note": note}


def _payload_from_ts(label: str, ts: Any, *, use_yoy: bool = False) -> dict:
    if ts is None:
        return _empty_payload(label, "数据库无该序列")
    values = list(ts.yoy_values if use_yoy else ts.values)
    periods = list(ts.periods)
    periods, values = _dedupe_period_values(periods, values)
    if len([v for v in values if v is not None]) < 2:
        return _empty_payload(label, "可比历史不足")
    return {
        "label": label,
        "periods": _period_range_label(periods),
        "raw_periods": periods,
        "values": values,
        "svg": render_sparkline(values, width=128, height=30),
        "note": "YoY 序列" if use_yoy else "原始序列",
    }


def _payload_from_aligned_ratio(
    label: str,
    numerator_ts: Any,
    denominator_ts: Any,
    *,
    multiplier: float = 1.0,
) -> dict:
    if numerator_ts is None or denominator_ts is None:
        return _empty_payload(label, "数据库无该序列")
    numerator = _series_map(numerator_ts)
    denominator = _series_map(denominator_ts)
    periods = sorted(set(numerator) & set(denominator))
    values: list[float | None] = []
    for p in periods:
        n = numerator.get(p)
        d = denominator.get(p)
        values.append(None if n is None or d in (None, 0) else n / d * multiplier)
    periods, values = _dedupe_period_values(periods, values)
    if len([v for v in values if v is not None]) < 2:
        return _empty_payload(label, "可比历史不足")
    return {
        "label": label,
        "periods": _period_range_label(periods),
        "raw_periods": periods,
        "values": values,
        "svg": render_sparkline(values, width=128, height=30),
        "note": "派生序列",
    }


def _payload_from_audit_records(records: list[dict]) -> dict:
    parsed: list[tuple[str, float]] = []
    for r in records:
        period = str(r.get("end_date") or "")
        if not period:
            continue
        audit_result = str(r.get("audit_result") or "")
        standard = any(kw in audit_result for kw in ("标准无保留", "无保留意见", "标准意见"))
        parsed.append((period, 100.0 if standard else 0.0))
    parsed.sort(key=lambda x: x[0])
    periods, values = _dedupe_period_values([p for p, _ in parsed], [v for _, v in parsed])
    periods = periods[-6:]
    values = values[-6:]
    if len([v for v in values if v is not None]) < 2:
        return _empty_payload("审计意见稳定性", "治理为事件型数据，连续历史不足")
    return {
        "label": "审计意见稳定性",
        "periods": _period_range_label(periods),
        "raw_periods": periods,
        "values": values,
        "svg": render_sparkline(values, width=128, height=30),
        "note": "100=标准意见",
    }


def _dedupe_period_values(
    periods: list[str],
    values: list[float | None],
) -> tuple[list[str], list[float | None]]:
    by_period: dict[str, float | None] = {}
    for p, v in zip(periods, values):
        by_period[str(p)] = v
    keys = sorted(by_period)
    return keys, [by_period[k] for k in keys]


def _ts_payload(ts: Any) -> dict:
    if ts is None:
        return {"periods": [], "values": [], "yoy": []}
    return {
        "periods": list(ts.periods),
        "values": list(ts.values),
        "yoy": list(ts.yoy_values),
    }


def _persist_period_factor_payload(
    engine: object,
    ts_code: str,
    family: str,
    payload: dict,
    source_hash: str,
) -> None:
    periods = payload.get("raw_periods") or []
    values = payload.get("values") or []
    if not periods or not values:
        return
    factor_name = str(payload.get("label") or "")
    unit = str(payload.get("note") or "")
    upsert_period_factor_decomposition(
        engine,
        ts_code=ts_code,
        factor_family=family,
        factor_name=factor_name,
        periods=[str(p) for p in periods],
        values=values,
        payload=payload,
        source_hash=source_hash,
        source="financial_statement_history",
        unit=unit,
    )


def _period_range_label(periods: list[str]) -> str:
    if not periods:
        return ""
    return f"{_format_period(periods[0])} → {_format_period(periods[-1])}"


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
            "peer_label": _format_peer_rank(r),
            "interpretation": _factor_interpretation(r),
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
                            "sparkline_svg": "", "period_range": ""})
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
            "period_range": _period_range_label(list(ts.periods)),
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


def _section_tensions(results_by_family: dict[str, list[FactorResult]]) -> dict | None:
    """Cross-cutting tensions detector (§09 narrative consistency).

    Returns None when no tensions found — the section is omitted from the
    report (no point showing an empty list).
    """
    tensions = detect_tensions(results_by_family)
    if not tensions:
        return None
    return {
        "type": "research_tensions",
        "entries": [t.to_dict() for t in tensions],
        "count": len(tensions),
    }


def _section_analyst_coverage(
    snap: CompanyFinancialSnapshot,
    llm_themes: list[dict],
    *,
    include_pdf_extracts: bool = False,
    engine: object | None = None,
) -> dict | None:
    """§10 analyst coverage: deterministic stats + optional LLM theme clustering.

    Returns None if no research reports — section is omitted to avoid noise.
    """
    if not snap.research_reports:
        return None
    cov = compute_coverage(snap.research_reports)
    if cov.total_reports == 0:
        return None
    recent_reports = cov.recent_reports
    if include_pdf_extracts:
        recent_reports = _enrich_recent_reports_with_pdf(
            recent_reports, limit=2, engine=engine, ts_code=snap.company.ts_code,
        )
    return {
        "type": "research_analyst_coverage",
        "total_reports": cov.total_reports,
        "reports_by_month": cov.reports_by_month,
        "top_institutions": cov.top_institutions,
        "recent_reports": recent_reports,
        "latest_report_date": cov.latest_report_date,
        "days_since_latest": cov.days_since_latest,
        "coverage_gap_warning": cov.coverage_gap_warning,
        "themes": llm_themes,
    }


def _enrich_recent_reports_with_pdf(
    reports: list[dict],
    *,
    limit: int,
    engine: object | None,
    ts_code: str,
) -> list[dict]:
    enriched: list[dict] = []
    for idx, report in enumerate(reports):
        item = dict(report)
        if idx < limit and item.get("url"):
            cached_row = load_pdf_extract(engine, str(item["url"])) if engine is not None else None
            cached = dict(cached_row["extract_json"]) if cached_row else None
            if cached is None:
                pdf = fetch_and_extract(str(item["url"]))
                cached = {
                    "pdf_pages": pdf.page_count,
                    "pdf_extractable": pdf.extractable,
                    "pdf_error": pdf.error,
                    "pdf_key_points": _extract_pdf_key_points(pdf.text) if pdf.extractable else [],
                    "text_hash": hashlib.sha256(pdf.text.encode("utf-8")).hexdigest() if pdf.text else None,
                }
                if engine is not None:
                    upsert_pdf_extract(
                        engine,
                        ts_code=ts_code,
                        source_url=str(item["url"]),
                        title=item.get("title"),
                        source_date=item.get("date"),
                        page_count=int(cached.get("pdf_pages") or 0),
                        extractable=bool(cached.get("pdf_extractable")),
                        text_hash=cached.get("text_hash"),
                        extract_json=cached,
                    )
            item.update(cached)
        enriched.append(item)
    return enriched


_PDF_DROP_RE = re.compile(
    r"(请务必阅读|免责声明|风险提示|评级说明|证券研究报告|本报告仅供|法律声明|目录|图表目录)"
)


def _extract_pdf_key_points(text: str, *, limit: int = 3) -> list[str]:
    points: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip(" -·•\t")
        if len(line) < 12 or _PDF_DROP_RE.search(line):
            continue
        if not re.search(r"(AI|增长|盈利|产能|业务|战略|投资|算力|端侧|客户|订单|份额|景气|收入|利润)", line):
            continue
        line = line[:90]
        if line not in points:
            points.append(line)
        if len(points) >= limit:
            break
    return points


def _section_investor_concerns(themes: list[dict]) -> dict:
    """LLM-clustered IRM themes — 'what investors keep asking'."""
    return {
        "type": "research_investor_concerns",
        "entries": themes,
        "count": len(themes),
    }


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


def _find_result(
    results_by_family: dict[str, list[FactorResult]],
    factor_name: str,
) -> FactorResult | None:
    for rows in results_by_family.values():
        for r in rows:
            if r.spec.name == factor_name:
                return r
    return None


def _metric_from_factor(label: str, r: FactorResult | None) -> dict | None:
    if r is None:
        return None
    return {
        "label": label,
        "value": _format_value(r.value, r.spec.unit),
        "status": r.status.value,
        "peer": _format_peer_rank(r),
    }


def _status_rank(status: FactorStatus) -> int:
    return {
        FactorStatus.RED: 0,
        FactorStatus.YELLOW: 1,
        FactorStatus.UNKNOWN: 2,
        FactorStatus.GREEN: 3,
    }.get(status, 4)


def _worst_results(rows: list[FactorResult], *, limit: int) -> list[FactorResult]:
    ranked = sorted(
        rows,
        key=lambda r: (
            _status_rank(r.status),
            -(len(r.notes)),
            r.peer_percentile if r.peer_percentile is not None else 50,
            r.spec.name,
        ),
    )
    return [r for r in ranked if r.status != FactorStatus.UNKNOWN][:limit]


def _best_results(rows: list[FactorResult], *, limit: int) -> list[FactorResult]:
    ranked = sorted(
        rows,
        key=lambda r: (
            0 if r.status == FactorStatus.GREEN else 1,
            -(r.peer_percentile if r.peer_percentile is not None else -1),
            r.spec.name,
        ),
    )
    return [r for r in ranked if r.value is not None][:limit]


def _format_peer_rank(r: FactorResult) -> str:
    if not r.peer_rank:
        return "—"
    rank, total = r.peer_rank
    if r.peer_percentile is None:
        return f"{rank}/{total}"
    return f"{rank}/{total} · P{r.peer_percentile:.0f}"


def _evidence_line(r: FactorResult) -> str:
    peer = _format_peer_rank(r)
    peer_part = "" if peer == "—" else f"；同业 {peer}"
    note = f"；{r.notes[0]}" if r.notes else ""
    return f"{r.spec.display_name_zh} {_format_value(r.value, r.spec.unit)}{peer_part}{note}"


def _default_factor_note(r: FactorResult) -> str:
    if r.status == FactorStatus.RED:
        return "规则层判定为高风险，需要优先核实原因。"
    if r.status == FactorStatus.YELLOW:
        return "处于预警区间，需结合下一期数据确认是否恶化。"
    if r.status == FactorStatus.GREEN:
        return "当前处于健康区间。"
    return "数据不足，结论可信度受限。"


def _factor_interpretation(r: FactorResult) -> str:
    note = "；".join(r.notes[:2]) if r.notes else _default_factor_note(r)
    peer = _format_peer_rank(r)
    if peer != "—":
        return f"{note} 同业位置：{peer}。"
    return note


def _family_verdict(
    family: str,
    status: str,
    worst: list[FactorResult],
    best: list[FactorResult],
) -> str:
    subject = FAMILY_LABEL_ZH.get(family, family)
    if status == "red":
        if worst:
            return f"{subject}为主要短板，优先解释 {worst[0].spec.display_name_zh} 的恶化。"
        return f"{subject}处于高风险区间。"
    if status == "yellow":
        if worst:
            return f"{subject}存在预警信号，核心观察项是 {worst[0].spec.display_name_zh}。"
        return f"{subject}需要谨慎跟踪。"
    if status == "green":
        if best:
            return f"{subject}当前构成支撑，代表指标是 {best[0].spec.display_name_zh}。"
        return f"{subject}当前整体健康。"
    return f"{subject}数据覆盖不足，暂不做强判断。"


def _next_watch(family: str, worst: list[FactorResult]) -> str:
    if worst:
        name = worst[0].spec.display_name_zh
        return f"下一期财报继续跟踪 {name} 是否改善，并核对公告解释是否匹配。"
    return {
        "profitability": "下一期重点看毛利率、扣非净利率与 ROE 是否延续当前方向。",
        "growth": "下一期重点看营收与净利增速是否同向，预告是否兑现。",
        "cash_quality": "下一期重点看 CFO/NI、FCF 与应收/存货扩张是否同步改善。",
        "balance": "下一期重点看负债率、流动性、商誉和质押压力。",
        "governance": "持续跟踪减持、审计意见、披露延期和互动回复质量。",
    }.get(family, "下一期继续跟踪核心财务指标。")


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)
