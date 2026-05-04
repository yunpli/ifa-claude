"""Regression: stk_holdertrade DE/IN field detection (governance §)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from ifa.families.research.analyzer.data import CompanyFinancialSnapshot
from ifa.families.research.analyzer.factors import FactorStatus
from ifa.families.research.analyzer.governance import (
    _compute_holdertrade_count,
    _is_decrease,
)
from ifa.families.research.resolver import CompanyRef


def _make_snap(holdertrades: list[dict]) -> CompanyFinancialSnapshot:
    company = CompanyRef(ts_code="001339.SZ", name="t", exchange="SZSE")
    snap = CompanyFinancialSnapshot(
        company=company,
        data_cutoff_date=date(2026, 5, 1),
    )
    snap.latest_period = "20260331"
    snap.holdertrades = holdertrades
    snap.total_share = Decimal("124000000")
    return snap


def test_is_decrease_recognises_tushare_in_de_field():
    # Tushare stk_holdertrade returns in_de='DE' for 减持 / 'IN' for 增持
    assert _is_decrease({"in_de": "DE"}) is True
    assert _is_decrease({"in_de": "IN"}) is False
    # Legacy fields still work
    assert _is_decrease({"trade_type": "减持"}) is True


def test_holdertrade_count_surfaces_de_rows():
    # Three DE rows within 12-month window → count must be ≥1
    rows = [
        {"in_de": "DE", "ann_date": "20260425", "change_vol": 4_511_200},
        {"in_de": "DE", "ann_date": "20260120", "change_vol": 1_000_000},
        {"in_de": "DE", "ann_date": "20251015", "change_vol": 500_000},
        {"in_de": "IN", "ann_date": "20260301", "change_vol": 200_000},
    ]
    snap = _make_snap(rows)
    result = _compute_holdertrade_count(snap, {"warning_above": 2, "critical_above": 3})
    assert result.value == Decimal(3)
    assert result.status is not FactorStatus.UNKNOWN
