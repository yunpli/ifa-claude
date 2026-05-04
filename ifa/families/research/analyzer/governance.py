"""Family E · Governance / Disclosure factors (治理/披露).

Six factors:
  · HOLDERTRADE_COUNT  — 12月内减持次数 (次)
  · HOLDERTRADE_SHARE  — 12月内减持占总股本 (%)
  · AUDIT_STANDARD     — 审计意见是否非标 (categorical → RED if non-standard)
  · AUDIT_CHANGE       — 年内更换审计机构 (categorical → YELLOW if changed)
  · MANAGER_TURNOVER   — 12月内管理层离职率 (%)
  · IRM_REPLY_RATE     — 互动易未回复率 (%)
  · DISCLOSURE_DELAY   — 定期报告披露延迟天数 (天)
"""
from __future__ import annotations

from datetime import date, timedelta
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

FAMILY = "governance"

SPECS: dict[str, FactorSpec] = {
    "HOLDERTRADE_COUNT": FactorSpec(
        name="HOLDERTRADE_COUNT",
        display_name_zh="12月减持次数",
        family=FAMILY,
        formula="COUNT(holdertrades WHERE direction='减持' AND ann_date >= T-365)",
        unit="次",
        source_apis=("stk_holdertrade",),
        industry_sensitive=False,
        direction="lower_better",
        interpretation_template="12月内减持 {value:.0f} 次，{status_zh}。",
    ),
    "HOLDERTRADE_SHARE": FactorSpec(
        name="HOLDERTRADE_SHARE",
        display_name_zh="12月减持占总股本",
        family=FAMILY,
        formula="SUM(holdertrades.vol WHERE 减持) / total_share × 100",
        unit="%",
        source_apis=("stk_holdertrade",),
        industry_sensitive=False,
        direction="lower_better",
        interpretation_template="12月内减持占总股本 {value:.2f}%，{status_zh}。",
    ),
    "AUDIT_STANDARD": FactorSpec(
        name="AUDIT_STANDARD",
        display_name_zh="审计意见类型",
        family=FAMILY,
        formula="fina_audit.audit_result != '标准无保留意见' → RED",
        unit="categorical",
        source_apis=("fina_audit",),
        industry_sensitive=False,
        direction="lower_better",
        interpretation_template="审计意见：{status_zh}。",
    ),
    "AUDIT_CHANGE": FactorSpec(
        name="AUDIT_CHANGE",
        display_name_zh="审计机构年内更换",
        family=FAMILY,
        formula="distinct audit agencies within same fiscal year",
        unit="categorical",
        source_apis=("fina_audit",),
        industry_sensitive=False,
        direction="lower_better",
        interpretation_template="审计机构：{status_zh}。",
    ),
    "MANAGER_TURNOVER": FactorSpec(
        name="MANAGER_TURNOVER",
        display_name_zh="管理层12月离职率",
        family=FAMILY,
        formula="离职人数 / 管理层总人数 × 100",
        unit="%",
        source_apis=("stk_managers",),
        industry_sensitive=False,
        direction="lower_better",
        interpretation_template="管理层12月离职率 {value:.1f}%，{status_zh}。",
    ),
    "IRM_REPLY_RATE": FactorSpec(
        name="IRM_REPLY_RATE",
        display_name_zh="互动易未回复率",
        family=FAMILY,
        formula="未回复问题数 / 总问题数 × 100",
        unit="%",
        source_apis=("irm_qa",),
        industry_sensitive=False,
        direction="lower_better",
        interpretation_template="互动易未回复率 {value:.1f}%，{status_zh}。",
    ),
    "DISCLOSURE_DELAY": FactorSpec(
        name="DISCLOSURE_DELAY",
        display_name_zh="披露延迟天数",
        family=FAMILY,
        formula="actual_date - deadline_date (天)",
        unit="天",
        source_apis=("disclosure_date",),
        industry_sensitive=False,
        direction="lower_better",
        interpretation_template="披露延迟 {value:.0f} 天，{status_zh}。",
    ),
}


# ─── Computation entry point ──────────────────────────────────────────────────

def compute_governance(
    snapshot: CompanyFinancialSnapshot,
    params: dict,
) -> list[FactorResult]:
    """Compute all governance factors. Returns list ordered like SPECS."""
    p = params.get(FAMILY, {})
    return [
        _compute_holdertrade_count(snapshot, p.get("holdertrade_decreasing_count_12m", {})),
        _compute_holdertrade_share(snapshot, p.get("holdertrade_decreasing_share_pct", {})),
        _compute_audit_standard(snapshot),
        _compute_audit_change(snapshot),
        _compute_manager_turnover(snapshot, p.get("manager_turnover_12m_pct", {})),
        _compute_irm_reply_rate(snapshot, p.get("irm_no_reply_rate_pct", {})),
        _compute_disclosure_delay(snapshot, p.get("disclosure_delay_days", {})),
    ]


# ─── Individual factors ───────────────────────────────────────────────────────

def _compute_holdertrade_count(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["HOLDERTRADE_COUNT"]

    if not snap.holdertrades:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 stk_holdertrade 数据"],
        )

    cutoff = (snap.data_cutoff_date - timedelta(days=365)).isoformat().replace("-", "")
    decreasing = [
        r for r in snap.holdertrades
        if _is_decrease(r) and (r.get("ann_date") or "") >= cutoff
    ]
    count = len(decreasing)

    status = classify_lower_better(
        Decimal(count),
        warning_above=float(p.get("warning_above", 2)),
        critical_above=float(p.get("critical_above", 3)),
    )
    return FactorResult(
        spec=spec, value=Decimal(count), status=status,
        period=snap.latest_period,
        raw_inputs={"count_12m": str(count)},
    )


def _compute_holdertrade_share(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["HOLDERTRADE_SHARE"]

    if not snap.holdertrades or snap.total_share is None or snap.total_share == 0:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 stk_holdertrade 数据或总股本"],
        )

    cutoff = (snap.data_cutoff_date - timedelta(days=365)).isoformat().replace("-", "")
    total_vol = Decimal(0)
    for r in snap.holdertrades:
        if _is_decrease(r) and (r.get("ann_date") or "") >= cutoff:
            vol = r.get("vol") or r.get("change_vol")
            if vol is not None:
                try:
                    total_vol += Decimal(str(vol))
                except Exception:
                    pass

    pct = total_vol / snap.total_share * 100
    status = classify_lower_better(
        pct,
        warning_above=float(p.get("warning_above", 3.0)),
        critical_above=float(p.get("critical_above", 5.0)),
    )
    return FactorResult(
        spec=spec, value=pct, status=status,
        period=snap.latest_period,
        raw_inputs={"decrease_vol": str(total_vol), "total_share": str(snap.total_share)},
    )


def _compute_audit_standard(snap: CompanyFinancialSnapshot) -> FactorResult:
    spec = SPECS["AUDIT_STANDARD"]

    if not snap.audit_records:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 fina_audit 数据"],
        )

    sorted_audits = sorted(snap.audit_records, key=lambda r: r.get("end_date") or "", reverse=True)
    latest = sorted_audits[0]
    audit_result = latest.get("audit_result") or ""

    # Standard unqualified opinion variants
    standard_keywords = ("标准无保留", "无保留意见", "标准意见")
    is_standard = any(kw in audit_result for kw in standard_keywords)

    if not audit_result:
        status = FactorStatus.UNKNOWN
        notes = ["audit_result 字段为空"]
    elif is_standard:
        status = FactorStatus.GREEN
        notes = []
    else:
        status = FactorStatus.RED
        notes = [f"非标审计意见: {audit_result}"]

    return FactorResult(
        spec=spec, value=None, status=status,
        period=snap.latest_period,
        notes=notes,
        raw_inputs={"audit_result": audit_result, "end_date": str(latest.get("end_date") or "")},
    )


def _compute_audit_change(snap: CompanyFinancialSnapshot) -> FactorResult:
    """Year-within-year audit agency change: if same fiscal year has >1 distinct audit agency → YELLOW."""
    spec = SPECS["AUDIT_CHANGE"]

    if not snap.audit_records:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 fina_audit 数据"],
        )

    # Group by fiscal year
    by_year: dict[str, set[str]] = {}
    for r in snap.audit_records:
        end_date = str(r.get("end_date") or "")
        if len(end_date) >= 4:
            year = end_date[:4]
            agency = str(r.get("audit_agency") or r.get("audit_org") or "")
            if agency:
                by_year.setdefault(year, set()).add(agency)

    # Check most recent year(s)
    changed_years = [yr for yr, agencies in by_year.items() if len(agencies) > 1]

    if not changed_years:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.GREEN,
            period=snap.latest_period,
        )

    changed_years.sort(reverse=True)
    return FactorResult(
        spec=spec, value=None, status=FactorStatus.YELLOW,
        period=snap.latest_period,
        notes=[f"年内更换审计机构: {', '.join(changed_years[:3])}"],
    )


def _compute_manager_turnover(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    spec = SPECS["MANAGER_TURNOVER"]

    if not snap.managers:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 stk_managers 数据"],
        )

    cutoff = (snap.data_cutoff_date - timedelta(days=365)).isoformat().replace("-", "")
    total = len(snap.managers)
    departed = sum(
        1 for r in snap.managers
        if (r.get("end_date") or r.get("leave_date") or "") >= cutoff
        and (r.get("end_date") or r.get("leave_date"))
    )

    if total == 0:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["管理层人数为 0"],
        )

    pct = Decimal(departed) / Decimal(total) * 100
    status = classify_lower_better(
        pct,
        warning_above=float(p.get("warning_above", 20.0)),
        critical_above=float(p.get("critical_above", 30.0)),
    )
    return FactorResult(
        spec=spec, value=pct, status=status,
        period=snap.latest_period,
        raw_inputs={"departed_12m": str(departed), "total_managers": str(total)},
    )


def _compute_irm_reply_rate(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    """Unreply rate = questions with no reply / total questions × 100."""
    spec = SPECS["IRM_REPLY_RATE"]

    if not snap.irm_qa:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 irm_qa 数据"],
        )

    total = len(snap.irm_qa)
    # Tushare irm_qa uses short field 'a' for the answer; alias variants
    # are kept for forward compatibility with other sources.
    unreplied = sum(
        1 for r in snap.irm_qa
        if not (r.get("a") or r.get("reply") or r.get("answer")
                or r.get("reply_content") or "").strip()
    )

    if total == 0:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["互动易问题数为 0"],
        )

    pct = Decimal(unreplied) / Decimal(total) * 100
    status = classify_lower_better(
        pct,
        warning_above=float(p.get("warning_above", 10.0)),
        critical_above=float(p.get("critical_above", 20.0)),
    )
    return FactorResult(
        spec=spec, value=pct, status=status,
        period=snap.latest_period,
        raw_inputs={"unreplied": str(unreplied), "total": str(total)},
    )


def _compute_disclosure_delay(snap: CompanyFinancialSnapshot, p: dict) -> FactorResult:
    """Average delay days: actual_date - deadline_date for periodic reports."""
    spec = SPECS["DISCLOSURE_DELAY"]

    if not snap.disclosure_dates:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["缺 disclosure_date 数据"],
        )

    delays: list[int] = []
    for r in snap.disclosure_dates:
        actual = r.get("actual_date") or r.get("disclosure_date")
        # Tushare disclosure_date: pre_date = 预约披露日, actual_date = 实际披露日.
        # 延迟天数 = actual - pre.  end_date is the *period end*, not a deadline.
        deadline = r.get("pre_date") or r.get("deadline")
        if actual and deadline:
            try:
                actual_d = _parse_date(str(actual))
                deadline_d = _parse_date(str(deadline))
                if actual_d and deadline_d:
                    delays.append((actual_d - deadline_d).days)
            except Exception:
                pass

    if not delays:
        return FactorResult(
            spec=spec, value=None, status=FactorStatus.UNKNOWN,
            period=snap.latest_period,
            notes=["无法解析 disclosure_date 字段"],
        )

    avg_delay = Decimal(sum(delays)) / Decimal(len(delays))
    status = classify_lower_better(
        avg_delay,
        warning_above=float(p.get("warning_above", 3)),
        critical_above=float(p.get("critical_above", 7)),
    )
    return FactorResult(
        spec=spec, value=avg_delay, status=status,
        period=snap.latest_period,
        raw_inputs={"avg_delay_days": str(avg_delay), "n_records": str(len(delays))},
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_decrease(trade: dict) -> bool:
    direction = str(trade.get("trade_type") or trade.get("direction") or "").lower()
    return "减持" in direction or "sell" in direction or "decrease" in direction


def _parse_date(s: str) -> date | None:
    s = s.strip()
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    try:
        parts = s.split("-")
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        pass
    return None
