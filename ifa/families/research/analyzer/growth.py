"""Family B · Growth factors (增长).

Four factors:
  · REVENUE_YOY   — 营收同比增速 (%)
  · N_INCOME_YOY  — 净利同比增速 (%)
  · REVENUE_CAGR  — 营收3年CAGR (%)
  · FORECAST_ACH  — 业绩预告达成率 (%)

Note: forecast_achievement_pct is pre-computed in data.py (actual/forecast_mid × 100).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from ifa.families.research.analyzer.data import CompanyFinancialSnapshot
from ifa.families.research.analyzer.factors import (
    FactorResult,
    FactorSpec,
    FactorStatus,
    classify_higher_better,
    classify_in_band,
    classify_lower_better,
)

FAMILY = "growth"

SPECS: dict[str, FactorSpec] = {
    "REVENUE_YOY": FactorSpec(
        name="REVENUE_YOY",
        display_name_zh="营收同比增速",
        family=FAMILY,
        formula="(revenue_t - revenue_t-4) / |revenue_t-4| × 100",
        unit="%",
        source_apis=("income",),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="营收同比 {value:.1f}%，{status_zh}。",
    ),
    "N_INCOME_YOY": FactorSpec(
        name="N_INCOME_YOY",
        display_name_zh="净利同比增速",
        family=FAMILY,
        formula="(n_income_t - n_income_t-4) / |n_income_t-4| × 100",
        unit="%",
        source_apis=("income",),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="净利同比 {value:.1f}%，{status_zh}。",
    ),
    "REVENUE_CAGR": FactorSpec(
        name="REVENUE_CAGR",
        display_name_zh="营收3年CAGR",
        family=FAMILY,
        formula="(revenue_latest / revenue_3yr_ago) ^ (1/3) - 1, × 100",
        unit="%",
        source_apis=("income",),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="营收3年CAGR {value:.1f}%，{status_zh}。",
    ),
    "FORECAST_ACH": FactorSpec(
        name="FORECAST_ACH",
        display_name_zh="业绩预告达成率",
        family=FAMILY,
        formula="actual_n_income / forecast_mid × 100",
        unit="%",
        source_apis=("forecast", "express", "income"),
        industry_sensitive=False,
        direction="in_band",
        interpretation_template="业绩预告达成率 {value:.1f}%，{status_zh}。",
    ),
}


# ─── Computation entry point ──────────────────────────────────────────────────

def compute_growth(
    snapshot: CompanyFinancialSnapshot,
    params: dict,
) -> list[FactorResult]:
    """Compute all 4 growth factors. Returns list ordered like SPECS."""
    p = params.get(FAMILY, {})
    return [
        _compute_revenue_yoy(snapshot, p.get("revenue_yoy_pct", {})),
        _compute_n_income_yoy(snapshot, p.get("n_income_yoy_pct", {})),
        _compute_revenue_cagr(snapshot, p.get("revenue_cagr_3y_pct", {})),
        _compute_forecast_ach(snapshot, p.get("forecast_achievement_pct", {})),
    ]


# ─── Individual factors ───────────────────────────────────────────────────────

def _compute_revenue_yoy(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["REVENUE_YOY"]
    ts = snap.revenue_series
    notes: list[str] = []

    if ts is None or ts.latest_yoy is None:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 income 营收序列或不足 5 期（需 YoY）"],
        )

    yoy = Decimal(str(ts.latest_yoy))
    status = classify_higher_better(
        yoy,
        healthy_min=p.get("warning_below", 0.0),
        warning_below=p.get("warning_below", 0.0),
        critical_below=p.get("critical_below", -10.0),
    )

    # Consecutive negative quarters check
    consec_red = p.get("consec_negative_quarters_red", 2)
    if ts.yoy_values and len(ts.yoy_values) >= consec_red:
        recent = [v for v in ts.yoy_values[-consec_red:] if v is not None]
        if len(recent) == consec_red and all(v < 0 for v in recent):
            notes.append(f"连续 {consec_red} 季营收同比为负")
            if status == FactorStatus.YELLOW:
                status = FactorStatus.RED

    history, periods = _series_pair(ts)
    return FactorResult(
        spec=spec, value=yoy, status=status,
        period=snap.latest_period,
        history=history, history_periods=periods,
        notes=notes,
    )


def _compute_n_income_yoy(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["N_INCOME_YOY"]
    ts = snap.n_income_series
    notes: list[str] = []

    if ts is None or ts.latest_yoy is None:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 income 净利序列或不足 5 期"],
        )

    yoy = Decimal(str(ts.latest_yoy))
    status = classify_higher_better(
        yoy,
        healthy_min=p.get("warning_below", 0.0),
        warning_below=p.get("warning_below", 0.0),
        critical_below=p.get("critical_below", -20.0),
    )

    # Leverage drop: n_income YoY < revenue YoY × 0.5 → warning
    rev_ts = snap.revenue_series
    lev_threshold = p.get("leverage_drop_warning", 0.5)
    if (rev_ts is not None and rev_ts.latest_yoy is not None
            and rev_ts.latest_yoy > 0):
        rev_yoy = float(rev_ts.latest_yoy)
        ni_yoy = float(yoy)
        if ni_yoy < rev_yoy * lev_threshold:
            notes.append(
                f"净利增速 {ni_yoy:.1f}% < 营收增速 {rev_yoy:.1f}% × {lev_threshold}，"
                "利润杠杆弱化"
            )
            if status == FactorStatus.GREEN:
                status = FactorStatus.YELLOW

    history, periods = _series_pair(ts)
    return FactorResult(
        spec=spec, value=yoy, status=status,
        period=snap.latest_period,
        history=history, history_periods=periods,
        notes=notes,
    )


def _compute_revenue_cagr(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["REVENUE_CAGR"]
    ts = snap.revenue_series

    if ts is None or not ts.values or len(ts.values) < 13:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["营收序列不足 13 期（3年×4季+1），无法计算 CAGR"],
        )

    # Use last period vs 12-period-ago (3 years back, quarterly) for annual CAGR
    vals = ts.values
    latest = vals[-1]
    base = vals[-13]  # 3 years back (12 quarters = index -13)

    if latest is None or base is None or base <= 0 or latest <= 0:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["营收基期或当期为 None / ≤0，无法计算 CAGR"],
        )

    cagr_pct = (Decimal(str(latest)) / Decimal(str(base))) ** (Decimal("1") / Decimal("3")) - 1
    cagr_pct *= 100

    status = classify_higher_better(
        cagr_pct,
        healthy_min=p.get("warning_below", 5.0),
        warning_below=p.get("warning_below", 5.0),
        critical_below=p.get("critical_below", 0.0),
    )
    history, periods = _series_pair(ts)
    return FactorResult(
        spec=spec, value=cagr_pct, status=status,
        period=snap.latest_period,
        history=history, history_periods=periods,
        raw_inputs={"revenue_latest": str(latest), "revenue_3yr_ago": str(base)},
    )


def _compute_forecast_ach(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["FORECAST_ACH"]

    if snap.forecast_achievement_pct is None:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["无业绩预告或预告期实际值缺失"],
        )

    val = snap.forecast_achievement_pct
    status = classify_in_band(
        val,
        healthy_low=p.get("healthy_low", 90.0),
        healthy_high=p.get("healthy_high", 110.0),
    )

    # Hard overrides for extremes
    if float(val) < p.get("critical_below", 60.0):
        status = FactorStatus.RED
    elif float(val) > p.get("critical_above", 200.0):
        status = FactorStatus.RED
    elif float(val) < p.get("warning_below", 80.0):
        status = FactorStatus.YELLOW
    elif float(val) > p.get("warning_above", 130.0):
        status = FactorStatus.YELLOW

    return FactorResult(
        spec=spec, value=val, status=status,
        period=snap.latest_period,
        raw_inputs={"forecast_achievement_pct": str(val)},
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _series_pair(ts: Any) -> tuple[list[float | None], list[str]]:
    if ts is None:
        return ([], [])
    return (list(ts.values), list(ts.periods))
