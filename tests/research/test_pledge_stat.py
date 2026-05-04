"""Regression: pledge_stat handling — distinguish "fetched-empty" (= no pledges,
healthy 0%) from "not-fetched" (= unknown). Bug: 001339.SZ rendered "缺失数据源
pledge_stat" because Tushare returns 0 rows for unencumbered stocks, but the
analyzer collapsed both states into "missing"."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from ifa.families.research.analyzer.balance import _compute_pledge_ratio
from ifa.families.research.analyzer.data import CompanyFinancialSnapshot
from ifa.families.research.analyzer.factors import FactorStatus
from ifa.families.research.resolver import CompanyRef


def _make_snap(pledge_rows: list[dict], status: str) -> CompanyFinancialSnapshot:
    company = CompanyRef(ts_code="001339.SZ", name="t", exchange="SZSE")
    snap = CompanyFinancialSnapshot(company=company, data_cutoff_date=date(2026, 5, 1))
    snap.latest_period = "20260331"
    snap.pledge_stat = pledge_rows
    snap.data_status["pledge_stat"] = status
    return snap


def test_fetched_empty_is_zero_percent_healthy():
    """Fetched 0 rows → 0% pledge ratio, GREEN — not UNKNOWN."""
    snap = _make_snap([], status="empty")
    fr = _compute_pledge_ratio(snap, {"warning_above": 30.0, "critical_above": 70.0})
    assert fr.value == Decimal("0")
    assert fr.status == FactorStatus.GREEN
    assert "0 行" in fr.notes[0]


def test_not_fetched_is_unknown():
    """No cache row → genuinely unknown."""
    snap = _make_snap([], status="missing")
    fr = _compute_pledge_ratio(snap, {"warning_above": 30.0, "critical_above": 70.0})
    assert fr.value is None
    assert fr.status == FactorStatus.UNKNOWN


def test_latest_picked_by_end_date_not_ann_date():
    """Tushare pledge_stat rows have end_date, not ann_date — sort must use it."""
    rows = [
        {"ts_code": "001339.SZ", "end_date": "20260101", "pledge_ratio": 5.0},
        {"ts_code": "001339.SZ", "end_date": "20260424", "pledge_ratio": 1.8},
        {"ts_code": "001339.SZ", "end_date": "20260301", "pledge_ratio": 3.2},
    ]
    snap = _make_snap(rows, status="ok")
    fr = _compute_pledge_ratio(snap, {"warning_above": 30.0, "critical_above": 70.0})
    assert fr.value == Decimal("1.8")  # latest by end_date
    assert fr.status == FactorStatus.GREEN


def test_pledged_high_ratio_is_warning():
    rows = [{"ts_code": "001339.SZ", "end_date": "20260424", "pledge_ratio": 45.0}]
    snap = _make_snap(rows, status="ok")
    fr = _compute_pledge_ratio(snap, {"warning_above": 30.0, "critical_above": 70.0})
    assert fr.value == Decimal("45.0")
    assert fr.status == FactorStatus.YELLOW
