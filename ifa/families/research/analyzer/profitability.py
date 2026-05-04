"""Family A · Profitability factors (盈利能力).

Six factors:
  · GPM       — 毛利率   (1 - oper_cost/revenue) × 100
  · NPM       — 净利率   n_income / revenue × 100
  · NPM_DEDT  — 扣非净利率 profit_dedt / revenue × 100
  · ROE       — 净资产收益率 (already from fina_indicator)
  · ROIC      — 投资回报 EBIT(1-T) / (debt+equity) ※ uses approximation
  · DUPONT    — 三因素分解 (NPM × Asset Turnover × Equity Multiplier)

CFO 视角：扣非与净利率的差距是识别"靠政府补贴/资产处置撑业绩"的第一道筛子.
GPM 加 yoy_drop 检测：连续同比下滑 ≥5pp 触发黄灯。

This module is the REFERENCE IMPLEMENTATION for the other four families.
Pattern:
  1. Static FactorSpec list at module top (registry)
  2. compute_<family>(snapshot, params) → list[FactorResult]
  3. One internal _compute_<factor> per factor — keeps a clean unit-tested boundary
  4. Use classify_higher_better / classify_lower_better / classify_in_band helpers
  5. None propagation: if any input is None → status=UNKNOWN (do NOT silently use 0)
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
)

FAMILY = "profitability"

SPECS: dict[str, FactorSpec] = {
    "GPM": FactorSpec(
        name="GPM",
        display_name_zh="毛利率",
        family=FAMILY,
        formula="(1 - oper_cost / revenue) × 100",
        unit="%",
        source_apis=("income",),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="毛利率 {value:.2f}%，{status_zh}。",
    ),
    "NPM": FactorSpec(
        name="NPM",
        display_name_zh="净利率",
        family=FAMILY,
        formula="n_income / revenue × 100",
        unit="%",
        source_apis=("income",),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="净利率 {value:.2f}%，{status_zh}。",
    ),
    "NPM_DEDT": FactorSpec(
        name="NPM_DEDT",
        display_name_zh="扣非净利率",
        family=FAMILY,
        formula="profit_dedt / revenue × 100",
        unit="%",
        source_apis=("income",),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="扣非净利率 {value:.2f}%，{status_zh}；与净利率差距 {gap_pct:.1f}%。",
    ),
    "ROE": FactorSpec(
        name="ROE",
        display_name_zh="净资产收益率",
        family=FAMILY,
        formula="n_income / avg(equity) × 100  (来自 fina_indicator)",
        unit="%",
        source_apis=("fina_indicator",),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="ROE {value:.2f}%，{status_zh}。",
    ),
    "ROIC": FactorSpec(
        name="ROIC",
        display_name_zh="投资回报率",
        family=FAMILY,
        formula="EBIT(1-t) / (debt + equity) × 100  (近似)",
        unit="%",
        source_apis=("income", "balancesheet"),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="ROIC {value:.2f}%，{status_zh}。",
    ),
    "DUPONT_NPM_GAP": FactorSpec(
        name="DUPONT_NPM_GAP",
        display_name_zh="DuPont 净利率分量",
        family=FAMILY,
        formula="NPM (used as DuPont leg)",
        unit="%",
        source_apis=("income", "balancesheet"),
        industry_sensitive=False,
        direction="higher_better",
        interpretation_template="DuPont 净利率分量 {value:.2f}%。",
    ),
}


# ─── Computation entry point ──────────────────────────────────────────────────

def compute_profitability(
    snapshot: CompanyFinancialSnapshot,
    params: dict,
) -> list[FactorResult]:
    """Compute all 6 profitability factors. Returns list ordered like SPECS."""
    p = params.get(FAMILY, {})
    return [
        _compute_gpm(snapshot, p.get("gpm", {})),
        _compute_npm(snapshot, p.get("npm", {})),
        _compute_npm_dedt(snapshot, p.get("npm_dedt", {})),
        _compute_roe(snapshot, p.get("roe", {})),
        _compute_roic(snapshot, p.get("roic", {})),
        _compute_dupont_npm(snapshot),
    ]


# ─── Individual factors ───────────────────────────────────────────────────────

def _to_pct(value: Decimal | None) -> Decimal | None:
    """Convert a fraction (0-1) to percentage (0-100). None passthrough."""
    if value is None:
        return None
    return value * 100


def _compute_gpm(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["GPM"]
    revenue = snap.revenue_yuan
    cost = snap.oper_cost_yuan
    notes: list[str] = []

    if revenue is None or cost is None or revenue == 0:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 income.total_revenue 或 income.oper_cost"],
        )

    gpm_pct = (Decimal(1) - cost / revenue) * 100
    status = classify_higher_better(
        gpm_pct,
        healthy_min=p.get("warning_below", 20.0),
        warning_below=p.get("warning_below", 20.0),
        critical_below=p.get("critical_below", 10.0),
    )

    # Build history sparkline from gpm_series
    history, periods = _series_pair(snap.gpm_series)

    # YoY drop check (same period last year)
    if len(history) >= 5:
        latest, prior_yr = history[-1], history[-5]
        if latest is not None and prior_yr is not None:
            drop = prior_yr - latest
            warn_pp = p.get("yoy_drop_warning_pp", 5.0)
            if drop >= warn_pp:
                notes.append(f"同比下降 {drop:.1f}pp（>{warn_pp:.0f}pp 警戒线）")
                if status == FactorStatus.GREEN:
                    status = FactorStatus.YELLOW

    return FactorResult(
        spec=spec, value=gpm_pct, status=status,
        period=snap.latest_period,
        history=history, history_periods=periods,
        notes=notes,
        raw_inputs={"revenue_yuan": str(revenue), "oper_cost_yuan": str(cost)},
    )


def _compute_npm(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["NPM"]
    if snap.revenue_yuan is None or snap.n_income_yuan is None or snap.revenue_yuan == 0:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period,
                            notes=["缺 income.total_revenue 或 income.n_income"])

    npm_pct = snap.n_income_yuan / snap.revenue_yuan * 100
    status = classify_higher_better(
        npm_pct,
        healthy_min=p.get("warning_below", 5.0),
        warning_below=p.get("warning_below", 5.0),
        critical_below=p.get("critical_below", 0.0),
    )
    history, periods = _series_pair(snap.npm_series)
    return FactorResult(
        spec=spec, value=npm_pct, status=status,
        period=snap.latest_period,
        history=history, history_periods=periods,
        raw_inputs={"revenue_yuan": str(snap.revenue_yuan),
                    "n_income_yuan": str(snap.n_income_yuan)},
    )


def _compute_npm_dedt(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["NPM_DEDT"]
    notes: list[str] = []
    if (snap.revenue_yuan is None or snap.profit_dedt_yuan is None
            or snap.revenue_yuan == 0):
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period,
                            notes=["缺 income.profit_dedt 或 total_revenue"])

    npm_dedt_pct = snap.profit_dedt_yuan / snap.revenue_yuan * 100
    status = classify_higher_better(
        npm_dedt_pct,
        healthy_min=p.get("warning_below", 4.0),
        warning_below=p.get("warning_below", 4.0),
        critical_below=p.get("critical_below", -1.0),
    )

    # Gap vs NPM check (核心识别政府补贴/非经常性损益依赖度)
    if snap.n_income_yuan is not None and snap.n_income_yuan != 0:
        gap_pct = abs(snap.n_income_yuan - snap.profit_dedt_yuan) / abs(snap.n_income_yuan) * 100
        gap_warn = p.get("npm_gap_pct_warning", 30.0)
        if gap_pct >= gap_warn:
            notes.append(
                f"扣非与净利差距 {gap_pct:.1f}%（>{gap_warn:.0f}%），"
                "可能依赖非经常性损益"
            )
            if status == FactorStatus.GREEN:
                status = FactorStatus.YELLOW
    else:
        gap_pct = None

    return FactorResult(
        spec=spec, value=npm_dedt_pct, status=status,
        period=snap.latest_period,
        notes=notes,
        raw_inputs={"profit_dedt_yuan": str(snap.profit_dedt_yuan),
                    "revenue_yuan": str(snap.revenue_yuan),
                    "gap_pct": str(gap_pct) if gap_pct is not None else None},
    )


def _compute_roe(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["ROE"]
    if snap.roe_pct is None:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period,
                            notes=["缺 fina_indicator.roe"])

    status = classify_higher_better(
        snap.roe_pct,
        healthy_min=p.get("warning_below", 10.0),
        warning_below=p.get("warning_below", 10.0),
        critical_below=p.get("critical_below", 5.0),
    )
    history, periods = _series_pair(snap.roe_series)
    return FactorResult(
        spec=spec, value=snap.roe_pct, status=status,
        period=snap.latest_period,
        history=history, history_periods=periods,
    )


def _compute_roic(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    """ROIC ≈ NOPAT / (debt + equity), with t=25% China statutory tax rate.

    NOPAT here is approximated as n_income (since EBIT not directly available
    without finer GP breakdown). For more accurate ROIC, add finance_exp and
    income_tax fields to fetcher; this is a P0 approximation.
    """
    spec = SPECS["ROIC"]
    if (snap.n_income_yuan is None
            or snap.total_liab_yuan is None
            or snap.total_hldr_eqy_inc_min_int_yuan is None):
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period,
                            notes=["缺 n_income / total_liab / total_hldr_eqy"])

    # Approximation: invested capital ≈ total_liab + equity (debt + equity)
    invested = snap.total_liab_yuan + snap.total_hldr_eqy_inc_min_int_yuan
    if invested <= 0:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period,
                            notes=["invested capital ≤ 0，无法计算"])

    roic_pct = snap.n_income_yuan / invested * 100
    status = classify_higher_better(
        roic_pct,
        healthy_min=p.get("warning_below", 8.0),
        warning_below=p.get("warning_below", 8.0),
        critical_below=p.get("critical_below", 5.0),
    )
    return FactorResult(
        spec=spec, value=roic_pct, status=status,
        period=snap.latest_period,
        notes=["近似计算：NOPAT≈n_income，未含财务费用调整"],
        raw_inputs={"invested_capital_yuan": str(invested),
                    "n_income_yuan": str(snap.n_income_yuan)},
    )


def _compute_dupont_npm(snap: CompanyFinancialSnapshot) -> FactorResult:
    """Return NPM as the DuPont decomposition NPM leg (for downstream DuPont diff analysis).

    Other DuPont legs (asset turnover, equity multiplier) are computed in
    section layer when needed; here we just expose NPM as the canonical leg.
    """
    spec = SPECS["DUPONT_NPM_GAP"]
    if snap.revenue_yuan is None or snap.n_income_yuan is None or snap.revenue_yuan == 0:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period)
    npm_pct = snap.n_income_yuan / snap.revenue_yuan * 100
    return FactorResult(
        spec=spec, value=npm_pct, status=FactorStatus.GREEN,
        period=snap.latest_period,
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _series_pair(ts: Any) -> tuple[list[float | None], list[str]]:
    """Extract (values, periods) from a TimeSeries object, gracefully handling None."""
    if ts is None:
        return ([], [])
    return (list(ts.values), list(ts.periods))
