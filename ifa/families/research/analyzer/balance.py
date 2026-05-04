"""Family D · Balance Sheet factors (资产负债结构).

Six factors:
  · DEBT_TO_ASSETS — 资产负债率 (%)
  · CURRENT_RATIO  — 流动比率 (x)
  · QUICK_RATIO    — 速动比率 (x)
  · GOODWILL_EQ    — 商誉/净资产 (%)
  · PLEDGE_RATIO   — 大股东质押率 (%)
  · IBD_SHARE      — 有息负债占比同比变化 (pp)
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
    classify_lower_better,
)

FAMILY = "balance"

SPECS: dict[str, FactorSpec] = {
    "DEBT_TO_ASSETS": FactorSpec(
        name="DEBT_TO_ASSETS",
        display_name_zh="资产负债率",
        family=FAMILY,
        formula="total_liab / total_assets × 100",
        unit="%",
        source_apis=("balancesheet", "fina_indicator"),
        industry_sensitive=True,
        direction="lower_better",
        interpretation_template="资产负债率 {value:.1f}%，{status_zh}。",
    ),
    "CURRENT_RATIO": FactorSpec(
        name="CURRENT_RATIO",
        display_name_zh="流动比率",
        family=FAMILY,
        formula="total_cur_assets / total_cur_liab",
        unit="x",
        source_apis=("balancesheet", "fina_indicator"),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="流动比率 {value:.2f}x，{status_zh}。",
    ),
    "QUICK_RATIO": FactorSpec(
        name="QUICK_RATIO",
        display_name_zh="速动比率",
        family=FAMILY,
        formula="(total_cur_assets - inventories) / total_cur_liab",
        unit="x",
        source_apis=("balancesheet", "fina_indicator"),
        industry_sensitive=True,
        direction="higher_better",
        interpretation_template="速动比率 {value:.2f}x，{status_zh}。",
    ),
    "GOODWILL_EQ": FactorSpec(
        name="GOODWILL_EQ",
        display_name_zh="商誉/净资产",
        family=FAMILY,
        formula="goodwill / total_hldr_eqy × 100",
        unit="%",
        source_apis=("balancesheet",),
        industry_sensitive=False,
        direction="lower_better",
        interpretation_template="商誉/净资产 {value:.1f}%，{status_zh}。",
    ),
    "PLEDGE_RATIO": FactorSpec(
        name="PLEDGE_RATIO",
        display_name_zh="大股东质押率",
        family=FAMILY,
        formula="pledge_count / total_share × 100 (from pledge_stat)",
        unit="%",
        source_apis=("pledge_stat",),
        industry_sensitive=False,
        direction="lower_better",
        interpretation_template="大股东质押率 {value:.1f}%，{status_zh}。",
    ),
    "IBD_SHARE_YOY": FactorSpec(
        name="IBD_SHARE_YOY",
        display_name_zh="有息负债占比同比变化",
        family=FAMILY,
        formula="ibd_share_t - ibd_share_t-4 (pp)",
        unit="pp",
        source_apis=("balancesheet",),
        industry_sensitive=True,
        direction="lower_better",
        interpretation_template="有息负债占比同比变化 {value:.1f}pp，{status_zh}。",
    ),
}


# ─── Computation entry point ──────────────────────────────────────────────────

def compute_balance(
    snapshot: CompanyFinancialSnapshot,
    params: dict,
) -> list[FactorResult]:
    """Compute all 6 balance sheet factors. Returns list ordered like SPECS."""
    p = params.get(FAMILY, {})
    return [
        _compute_debt_to_assets(snapshot, p.get("debt_to_assets_pct", {})),
        _compute_current_ratio(snapshot, p.get("current_ratio", {})),
        _compute_quick_ratio(snapshot, p.get("quick_ratio", {})),
        _compute_goodwill_eq(snapshot, p.get("goodwill_to_equity_pct", {})),
        _compute_pledge_ratio(snapshot, p.get("pledge_ratio_pct", {})),
        _compute_ibd_share_yoy(snapshot, p),
    ]


# ─── Individual factors ───────────────────────────────────────────────────────

def _compute_debt_to_assets(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["DEBT_TO_ASSETS"]
    notes: list[str] = []

    # Prefer fina_indicator pre-computed value
    if snap.debt_to_assets_pct is not None:
        val = snap.debt_to_assets_pct
    elif snap.total_liab_yuan is not None and snap.total_assets_yuan is not None:
        if snap.total_assets_yuan == 0:
            return FactorResult(
                spec=spec, value=None, status=FactorStatus.UNKNOWN,
                period=snap.latest_period,
                notes=["total_assets = 0，无法计算"],
            )
        val = snap.total_liab_yuan / snap.total_assets_yuan * 100
    else:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 fina_indicator.debt_to_assets 或 balancesheet 字段"],
        )

    status = classify_lower_better(
        val,
        warning_above=p.get("warning_above", 60.0),
        critical_above=p.get("critical_above", 75.0),
    )

    yoy_warn = p.get("yoy_rise_warning_pp", 10.0)
    # YoY check not available without series — add note if we later want to add it
    return FactorResult(
        spec=spec, value=val, status=status,
        period=snap.latest_period,
        notes=notes,
    )


def _compute_current_ratio(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["CURRENT_RATIO"]

    # Prefer fina_indicator
    if snap.current_ratio is not None:
        val = snap.current_ratio
    elif snap.total_cur_assets_yuan is not None and snap.total_cur_liab_yuan is not None:
        if snap.total_cur_liab_yuan == 0:
            return FactorResult(
                spec=spec, value=None, status=FactorStatus.UNKNOWN,
                period=snap.latest_period,
                notes=["total_cur_liab = 0，无法计算"],
            )
        val = snap.total_cur_assets_yuan / snap.total_cur_liab_yuan
    else:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 fina_indicator.current_ratio 或 balancesheet 字段"],
        )

    status = classify_higher_better(
        val,
        healthy_min=p.get("warning_below", 1.5),
        warning_below=p.get("warning_below", 1.5),
        critical_below=p.get("critical_below", 1.0),
    )
    return FactorResult(
        spec=spec, value=val, status=status,
        period=snap.latest_period,
    )


def _compute_quick_ratio(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["QUICK_RATIO"]

    if snap.quick_ratio is not None:
        val = snap.quick_ratio
    elif (snap.total_cur_assets_yuan is not None
          and snap.inventories_yuan is not None
          and snap.total_cur_liab_yuan is not None):
        if snap.total_cur_liab_yuan == 0:
            return FactorResult(
                spec=spec, value=None, status=FactorStatus.UNKNOWN,
                period=snap.latest_period,
                notes=["total_cur_liab = 0，无法计算"],
            )
        val = (snap.total_cur_assets_yuan - snap.inventories_yuan) / snap.total_cur_liab_yuan
    else:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 fina_indicator.quick_ratio 或 balancesheet 字段"],
        )

    status = classify_higher_better(
        val,
        healthy_min=p.get("warning_below", 1.0),
        warning_below=p.get("warning_below", 1.0),
        critical_below=p.get("critical_below", 0.7),
    )
    return FactorResult(
        spec=spec, value=val, status=status,
        period=snap.latest_period,
    )


def _compute_goodwill_eq(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["GOODWILL_EQ"]

    if snap.goodwill_yuan is None or snap.total_hldr_eqy_inc_min_int_yuan is None:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 balancesheet.goodwill 或 total_hldr_eqy_inc_min_int"],
        )
    if snap.total_hldr_eqy_inc_min_int_yuan <= 0:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["净资产 ≤ 0，商誉比率无意义"],
        )

    val = snap.goodwill_yuan / snap.total_hldr_eqy_inc_min_int_yuan * 100
    status = classify_lower_better(
        val,
        warning_above=p.get("warning_above", 30.0),
        critical_above=p.get("critical_above", 50.0),
    )
    return FactorResult(
        spec=spec, value=val, status=status,
        period=snap.latest_period,
        raw_inputs={
            "goodwill_yuan": str(snap.goodwill_yuan),
            "equity_yuan": str(snap.total_hldr_eqy_inc_min_int_yuan),
        },
    )


def _compute_pledge_ratio(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    """Pledge ratio from pledge_stat list.

    pledge_stat rows contain: ts_code, ann_date, holder_name, pledge_count,
    holding_amount, pledge_ratio (0-100), etc.
    We use the latest row's pledge_ratio if available, otherwise sum.
    """
    spec = SPECS["PLEDGE_RATIO"]

    if not snap.pledge_stat:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 pledge_stat 数据"],
        )

    # Sort by ann_date desc, take latest
    sorted_ps = sorted(snap.pledge_stat, key=lambda r: r.get("ann_date") or "", reverse=True)
    latest = sorted_ps[0]
    ratio_raw = latest.get("pledge_ratio")

    if ratio_raw is None:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["pledge_stat.pledge_ratio 为 None"],
        )

    try:
        val = Decimal(str(ratio_raw))
    except Exception:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=[f"pledge_ratio 无法解析: {ratio_raw!r}"],
        )

    status = classify_lower_better(
        val,
        warning_above=p.get("warning_above", 30.0),
        critical_above=p.get("critical_above", 70.0),
    )
    return FactorResult(
        spec=spec, value=val, status=status,
        period=snap.latest_period,
        raw_inputs={"pledge_ratio_pct": str(val)},
    )


def _compute_ibd_share_yoy(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    """资产负债率同比变化 (pp).

    P0 approximation: snapshot doesn't break out 有息负债 (long-term loans,
    short-term loans, bonds payable) — we proxy with total_liab/total_assets
    YoY change. When 有息负债 fields are added later, swap the numerator.
    """
    spec = SPECS["IBD_SHARE_YOY"]
    warn_pp = float(p.get("interest_bearing_debt_share_yoy_pp_warn", 15.0))

    liab_ts = snap.total_liab_series
    asset_ts = snap.total_assets_series
    if (liab_ts is None or asset_ts is None
            or len(liab_ts.values) < 5 or len(asset_ts.values) < 5):
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["balancesheet 序列不足 5 期"],
        )

    def _ratio(idx: int) -> float | None:
        a = asset_ts.values[idx]
        l = liab_ts.values[idx]
        if a in (None, 0) or l is None:
            return None
        return l / a * 100

    now = _ratio(-1)
    yoy = _ratio(-5)
    if now is None or yoy is None:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["当期或同期 ratio 计算失败"],
        )

    delta_pp = Decimal(str(now - yoy))
    if float(delta_pp) > warn_pp:
        status = FactorStatus.YELLOW
    else:
        status = FactorStatus.GREEN

    return FactorResult(
        spec=spec, value=delta_pp, status=status,
        period=snap.latest_period,
        notes=[f"近似(总负债/总资产): {yoy:.1f}% → {now:.1f}%（{float(delta_pp):+.1f}pp，未拆有息负债）"],
        raw_inputs={"ratio_now_pct": str(now), "ratio_yoy_pct": str(yoy)},
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _series_pair(ts: Any) -> tuple[list[float | None], list[str]]:
    if ts is None:
        return ([], [])
    return (list(ts.values), list(ts.periods))
