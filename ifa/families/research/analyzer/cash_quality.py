"""Family C · Cash Quality factors (现金质量).

Five factors:
  · CFO_TO_NI      — 经营现金流 / 净利润 (ratio, in-band 0.8-1.2)
  · FCF            — 自由现金流 (元, higher_better / 0 boundary)
  · AR_GROWTH_REV  — 应收账款增速 / 营收增速 (ratio, lower_better)
  · INV_GROWTH_COST — 存货增速 / 营业成本增速 (ratio, lower_better)
  · CCC_CHANGE     — 现金转换周期同比变化 (天, lower_better)
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

FAMILY = "cash_quality"

SPECS: dict[str, FactorSpec] = {
    "CFO_TO_NI": FactorSpec(
        name="CFO_TO_NI",
        display_name_zh="经营现金流/净利",
        family=FAMILY,
        formula="n_cashflow_act / n_income",
        unit="x",
        source_apis=("cashflow", "income"),
        industry_sensitive=False,
        direction="in_band",
        interpretation_template="CFO/NI {value:.2f}x，{status_zh}。",
    ),
    "FCF": FactorSpec(
        name="FCF",
        display_name_zh="自由现金流",
        family=FAMILY,
        formula="n_cashflow_act - c_pay_acq_const_fiolta",
        unit="元",
        source_apis=("cashflow",),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="FCF {value:.0f}元，{status_zh}。",
    ),
    "AR_GROWTH_REV": FactorSpec(
        name="AR_GROWTH_REV",
        display_name_zh="应收账款增速/营收增速",
        family=FAMILY,
        formula="yoy(accounts_receiv) / yoy(revenue)",
        unit="x",
        source_apis=("balancesheet", "income"),
        industry_sensitive=True,
        direction="lower_better",
        interpretation_template="应收增速/营收增速 {value:.2f}x，{status_zh}。",
    ),
    "INV_GROWTH_COST": FactorSpec(
        name="INV_GROWTH_COST",
        display_name_zh="存货增速/成本增速",
        family=FAMILY,
        formula="yoy(inventories) / yoy(oper_cost)",
        unit="x",
        source_apis=("balancesheet", "income"),
        industry_sensitive=True,
        direction="lower_better",
        interpretation_template="存货增速/成本增速 {value:.2f}x，{status_zh}。",
    ),
    "CCC_CHANGE": FactorSpec(
        name="CCC_CHANGE",
        display_name_zh="CCC同比变化",
        family=FAMILY,
        formula="CCC_t - CCC_t-4, CCC=DSO+DIO-DPO",
        unit="天",
        source_apis=("balancesheet", "income"),
        industry_sensitive=True,
        direction="lower_better",
        interpretation_template="CCC同比变化 {value:.1f}天，{status_zh}。",
    ),
}


# ─── Computation entry point ──────────────────────────────────────────────────

def compute_cash_quality(
    snapshot: CompanyFinancialSnapshot,
    params: dict,
) -> list[FactorResult]:
    """Compute all 5 cash quality factors. Returns list ordered like SPECS."""
    p = params.get(FAMILY, {})
    return [
        _compute_cfo_to_ni(snapshot, p.get("cfo_to_ni", {})),
        _compute_fcf(snapshot, p.get("fcf_yuan", {})),
        _compute_ar_growth_rev(snapshot, p.get("ar_growth_to_revenue_growth", {})),
        _compute_inv_growth_cost(snapshot, p.get("inventory_growth_to_cost_growth", {})),
        _compute_ccc_change(snapshot, p.get("ccc_days_yoy_change", {})),
    ]


# ─── Individual factors ───────────────────────────────────────────────────────

def _compute_cfo_to_ni(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["CFO_TO_NI"]
    notes: list[str] = []

    if snap.n_cashflow_act_yuan is None or snap.n_income_yuan is None:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 cashflow.n_cashflow_act 或 income.n_income"],
        )
    if snap.n_income_yuan == 0:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["净利润为 0，CFO/NI 无意义"],
        )

    ratio = snap.n_cashflow_act_yuan / snap.n_income_yuan
    status = classify_in_band(
        ratio,
        healthy_low=p.get("healthy_low", 0.8),
        healthy_high=p.get("healthy_high", 1.2),
    )

    # Hard red: CFO < 0 but NI > 0
    if float(snap.n_cashflow_act_yuan) < 0 and float(snap.n_income_yuan) > 0:
        status = FactorStatus.RED
        notes.append("经营现金流为负但净利润为正，利润质量低")

    # Consecutive low quarters from series
    consec_red = p.get("consec_low_quarters_red", 4)
    warn_threshold = p.get("warning_below", 0.5)
    cfo_ts = snap.cfo_series
    ni_ts = snap.n_income_series
    if (cfo_ts is not None and ni_ts is not None
            and len(cfo_ts.values) >= consec_red
            and len(ni_ts.values) >= consec_red):
        recent_ratios = []
        for i in range(-consec_red, 0):
            cfo_v = cfo_ts.values[i]
            ni_v = ni_ts.values[i]
            if cfo_v is not None and ni_v is not None and ni_v != 0:
                recent_ratios.append(cfo_v / ni_v)
        if (len(recent_ratios) == consec_red
                and all(r < warn_threshold for r in recent_ratios)):
            notes.append(f"连续 {consec_red} 季 CFO/NI < {warn_threshold}")
            if status != FactorStatus.RED:
                status = FactorStatus.RED

    return FactorResult(
        spec=spec, value=ratio, status=status,
        period=snap.latest_period,
        notes=notes,
        raw_inputs={
            "n_cashflow_act_yuan": str(snap.n_cashflow_act_yuan),
            "n_income_yuan": str(snap.n_income_yuan),
        },
    )


def _compute_fcf(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["FCF"]

    if snap.n_cashflow_act_yuan is None:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 cashflow.n_cashflow_act"],
        )

    # capex may be missing; FCF = CFO if capex unavailable
    capex = snap.c_pay_acq_const_fiolta_yuan or Decimal(0)
    fcf = snap.n_cashflow_act_yuan - capex

    # FCF < 0 → yellow; from series: consecutive negative quarters → red
    if float(fcf) < p.get("warning_below_yuan", 0):
        status = FactorStatus.YELLOW
    else:
        status = FactorStatus.GREEN

    # Consecutive negative check from CFO series as approximation
    consec_red = p.get("critical_consec_negative_quarters", 3)
    cfo_ts = snap.cfo_series
    notes: list[str] = []
    if cfo_ts is not None and len(cfo_ts.values) >= consec_red:
        recent = [v for v in cfo_ts.values[-consec_red:] if v is not None]
        if len(recent) == consec_red and all(v < 0 for v in recent):
            notes.append(f"CFO 连续 {consec_red} 季为负")
            status = FactorStatus.RED

    return FactorResult(
        spec=spec, value=fcf, status=status,
        period=snap.latest_period,
        notes=notes,
        raw_inputs={
            "n_cashflow_act_yuan": str(snap.n_cashflow_act_yuan),
            "capex_yuan": str(capex),
        },
    )


def _compute_ar_growth_rev(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["AR_GROWTH_REV"]

    rev_ts = snap.revenue_series
    ar_ts = snap.accounts_receiv_series
    if rev_ts is None or rev_ts.latest_yoy is None:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period, notes=["缺营收同比"])
    if ar_ts is None or ar_ts.latest_yoy is None:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period, notes=["缺应收账款同比（需 ≥5 期 BS）"])

    rev_yoy = float(rev_ts.latest_yoy)
    ar_yoy = float(ar_ts.latest_yoy)
    if rev_yoy == 0:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period, notes=["营收同比为 0，比率无意义"])

    # If both negative, ratio loses meaning; mark UNKNOWN with note instead of producing noise.
    if rev_yoy < 0 and ar_yoy < 0:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period,
                            notes=[f"营收/应收均同比下降（rev {rev_yoy:.1f}%, AR {ar_yoy:.1f}%），比率无意义"])

    ratio = Decimal(str(ar_yoy)) / Decimal(str(rev_yoy))
    status = classify_lower_better(
        ratio,
        warning_above=float(p.get("warning_above", 1.2)),
        critical_above=float(p.get("critical_above", 1.5)),
    )
    return FactorResult(
        spec=spec, value=ratio, status=status, period=snap.latest_period,
        raw_inputs={"ar_yoy_pct": str(ar_yoy), "rev_yoy_pct": str(rev_yoy)},
    )


def _compute_inv_growth_cost(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["INV_GROWTH_COST"]
    cost_ts = snap.oper_cost_series
    inv_ts = snap.inventories_series
    if cost_ts is None or cost_ts.latest_yoy is None:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period, notes=["缺营业成本同比"])
    if inv_ts is None or inv_ts.latest_yoy is None:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period, notes=["缺存货同比（需 ≥5 期 BS）"])

    cost_yoy = float(cost_ts.latest_yoy)
    inv_yoy = float(inv_ts.latest_yoy)
    if cost_yoy == 0:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period, notes=["成本同比为 0，比率无意义"])
    if cost_yoy < 0 and inv_yoy < 0:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period,
                            notes=[f"成本/存货均同比下降（cost {cost_yoy:.1f}%, INV {inv_yoy:.1f}%），比率无意义"])

    ratio = Decimal(str(inv_yoy)) / Decimal(str(cost_yoy))
    status = classify_lower_better(
        ratio,
        warning_above=float(p.get("warning_above", 1.2)),
        critical_above=float(p.get("critical_above", 1.5)),
    )
    return FactorResult(
        spec=spec, value=ratio, status=status, period=snap.latest_period,
        raw_inputs={"inv_yoy_pct": str(inv_yoy), "cost_yoy_pct": str(cost_yoy)},
    )


def _compute_ccc_change(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    """CCC ≈ DSO + DIO (DPO not in snapshot).  YoY change = CCC_t - CCC_t-4 (天)."""
    spec = SPECS["CCC_CHANGE"]

    rev_ts = snap.revenue_series
    cost_ts = snap.oper_cost_series
    ar_ts = snap.accounts_receiv_series
    inv_ts = snap.inventories_series

    if not all([rev_ts, cost_ts, ar_ts, inv_ts]):
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period,
                            notes=["缺 income/balancesheet 多期序列"])

    def _ccc_at(idx: int) -> float | None:
        rev = rev_ts.values[idx] if abs(idx) <= len(rev_ts.values) else None
        cost = cost_ts.values[idx] if abs(idx) <= len(cost_ts.values) else None
        ar = ar_ts.values[idx] if abs(idx) <= len(ar_ts.values) else None
        inv = inv_ts.values[idx] if abs(idx) <= len(inv_ts.values) else None
        if None in (rev, cost, ar, inv) or rev == 0 or cost == 0:
            return None
        return ar / rev * 90 + inv / cost * 90

    if min(len(rev_ts.values), len(cost_ts.values),
           len(ar_ts.values), len(inv_ts.values)) < 5:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period,
                            notes=["序列不足 5 期，无法计算 YoY"])

    ccc_now = _ccc_at(-1)
    ccc_yoy = _ccc_at(-5)
    if ccc_now is None or ccc_yoy is None:
        return FactorResult(spec=spec, value=None, status=FactorStatus.UNKNOWN,
                            period=snap.latest_period,
                            notes=["当期或同期 CCC 计算失败（基期为 0 或 None）"])

    delta_days = Decimal(str(ccc_now - ccc_yoy))
    delta_pct = (ccc_now - ccc_yoy) / ccc_yoy * 100 if ccc_yoy else 0.0

    warn_pct = float(p.get("warning_above_pct", 20))
    crit_pct = float(p.get("critical_above_pct", 50))
    if delta_pct > crit_pct:
        status = FactorStatus.RED
    elif delta_pct > warn_pct:
        status = FactorStatus.YELLOW
    else:
        status = FactorStatus.GREEN

    return FactorResult(
        spec=spec, value=delta_days, status=status, period=snap.latest_period,
        notes=[f"CCC: {ccc_yoy:.1f}天 → {ccc_now:.1f}天（{delta_pct:+.1f}%）"],
        raw_inputs={"ccc_now_days": str(ccc_now), "ccc_yoy_days": str(ccc_yoy)},
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _series_pair(ts: Any) -> tuple[list[float | None], list[str]]:
    if ts is None:
        return ([], [])
    return (list(ts.values), list(ts.periods))
