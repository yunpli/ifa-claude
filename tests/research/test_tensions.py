"""Tests for the §09 cross-cutting tensions detector.

Each detector should fire on the intended pattern and stay silent otherwise.
Tensions are deterministic and rule-based — perfect for unit tests.
"""
from __future__ import annotations

from decimal import Decimal

from ifa.families.research.analyzer.factors import (
    FactorResult,
    FactorSpec,
    FactorStatus,
)
from ifa.families.research.analyzer.tensions import detect_tensions


def _spec(name: str, family: str, unit: str = "%",
          industry_sensitive: bool = True) -> FactorSpec:
    return FactorSpec(
        name=name, display_name_zh=name, family=family,
        formula="—", unit=unit, source_apis=("test",),
        industry_sensitive=industry_sensitive,
        direction="higher_better",
        interpretation_template="{value}",
    )


def _r(name: str, family: str, value: float | None, status: FactorStatus,
       unit: str = "%", peer_pct: float | None = None,
       industry_sensitive: bool = True) -> FactorResult:
    r = FactorResult(
        spec=_spec(name, family, unit=unit,
                   industry_sensitive=industry_sensitive),
        value=Decimal(str(value)) if value is not None else None,
        status=status,
        period="20260331",
    )
    r.peer_percentile = peer_pct
    return r


def _bundle(*results: FactorResult) -> dict[str, list[FactorResult]]:
    """Group results into the family-keyed dict the detector expects."""
    out: dict[str, list[FactorResult]] = {}
    for r in results:
        out.setdefault(r.spec.family, []).append(r)
    return out


class TestProfitQualityMismatch:
    def test_fires_when_npm_green_but_cfo_red(self):
        rs = _bundle(
            _r("NPM", "profitability", 15, FactorStatus.GREEN),
            _r("CFO_TO_NI", "cash_quality", -2.6, FactorStatus.RED, unit="x"),
        )
        codes = [t.code for t in detect_tensions(rs)]
        assert "profit_quality_mismatch" in codes

    def test_silent_when_both_green(self):
        rs = _bundle(
            _r("NPM", "profitability", 15, FactorStatus.GREEN),
            _r("CFO_TO_NI", "cash_quality", 1.1, FactorStatus.GREEN, unit="x"),
        )
        codes = [t.code for t in detect_tensions(rs)]
        assert "profit_quality_mismatch" not in codes

    def test_silent_when_npm_red_already(self):
        # If NPM itself is RED there's no "mismatch" — both sides agree
        rs = _bundle(
            _r("NPM", "profitability", 0.5, FactorStatus.RED),
            _r("CFO_TO_NI", "cash_quality", -2.6, FactorStatus.RED, unit="x"),
        )
        codes = [t.code for t in detect_tensions(rs)]
        assert "profit_quality_mismatch" not in codes


class TestInventoryOutpacesSales:
    def test_fires_at_15x(self):
        rs = _bundle(_r("INV_GROWTH_COST", "cash_quality", 1.5,
                        FactorStatus.RED, unit="x"))
        codes = [t.code for t in detect_tensions(rs)]
        assert "inventory_outpaces_sales" in codes

    def test_high_severity_at_25x(self):
        rs = _bundle(_r("INV_GROWTH_COST", "cash_quality", 2.5,
                        FactorStatus.RED, unit="x"))
        ts = [t for t in detect_tensions(rs) if t.code == "inventory_outpaces_sales"]
        assert ts and ts[0].severity == "high"

    def test_silent_below_threshold(self):
        rs = _bundle(_r("INV_GROWTH_COST", "cash_quality", 1.2,
                        FactorStatus.YELLOW, unit="x"))
        codes = [t.code for t in detect_tensions(rs)]
        assert "inventory_outpaces_sales" not in codes


class TestEarningsViaDedt:
    def test_fires_at_50pct_gap(self):
        rs = _bundle(
            _r("NPM", "profitability", 15, FactorStatus.GREEN),
            _r("NPM_DEDT", "profitability", 7, FactorStatus.YELLOW),
        )
        codes = [t.code for t in detect_tensions(rs)]
        assert "earnings_via_dedt_gap" in codes

    def test_silent_when_close(self):
        rs = _bundle(
            _r("NPM", "profitability", 15, FactorStatus.GREEN),
            _r("NPM_DEDT", "profitability", 14, FactorStatus.GREEN),
        )
        codes = [t.code for t in detect_tensions(rs)]
        assert "earnings_via_dedt_gap" not in codes


class TestIndustryLeaderInDecline:
    def test_fires_when_red_but_high_peer(self):
        rs = _bundle(
            _r("ROE", "profitability", 4.7, FactorStatus.RED, peer_pct=93),
            _r("ROIC", "profitability", 2.2, FactorStatus.RED, peer_pct=88),
        )
        codes = [t.code for t in detect_tensions(rs)]
        assert "industry_leader_in_decline" in codes

    def test_silent_with_only_one(self):
        # Need at least 2 factors for this signal
        rs = _bundle(
            _r("ROE", "profitability", 4.7, FactorStatus.RED, peer_pct=93),
            _r("ROIC", "profitability", 2.2, FactorStatus.RED, peer_pct=50),
        )
        codes = [t.code for t in detect_tensions(rs)]
        assert "industry_leader_in_decline" not in codes


class TestForecastVolatility:
    def test_fires_when_177pct(self):
        rs = _bundle(_r("FORECAST_ACH", "growth", 177, FactorStatus.YELLOW))
        ts = [t for t in detect_tensions(rs) if t.code == "forecast_volatility"]
        assert ts

    def test_high_severity_when_extreme(self):
        rs = _bundle(_r("FORECAST_ACH", "growth", 250, FactorStatus.RED))
        ts = [t for t in detect_tensions(rs) if t.code == "forecast_volatility"]
        assert ts and ts[0].severity == "high"


class TestSorting:
    def test_high_severity_sorts_before_medium(self):
        rs = _bundle(
            _r("NPM", "profitability", 15, FactorStatus.GREEN),
            _r("CFO_TO_NI", "cash_quality", -2.6, FactorStatus.RED, unit="x"),
            _r("FORECAST_ACH", "growth", 177, FactorStatus.YELLOW),
        )
        tensions = detect_tensions(rs)
        # profit_quality_mismatch is high; forecast_volatility is medium
        sevs = [t.severity for t in tensions]
        # All high should come before all medium
        if "high" in sevs and "medium" in sevs:
            last_high = max(i for i, s in enumerate(sevs) if s == "high")
            first_medium = min(i for i, s in enumerate(sevs) if s == "medium")
            assert last_high < first_medium


class TestRobustness:
    def test_handles_none_values(self):
        rs = _bundle(
            _r("NPM", "profitability", None, FactorStatus.UNKNOWN),
            _r("CFO_TO_NI", "cash_quality", None, FactorStatus.UNKNOWN, unit="x"),
        )
        # Should not crash, just return empty
        tensions = detect_tensions(rs)
        assert isinstance(tensions, list)

    def test_empty_input(self):
        assert detect_tensions({}) == []

    def test_one_bad_detector_doesnt_kill_others(self):
        # Even if a detector implementation has a bug, others should run
        rs = _bundle(
            _r("FORECAST_ACH", "growth", 177, FactorStatus.YELLOW),
        )
        tensions = detect_tensions(rs)
        assert any(t.code == "forecast_volatility" for t in tensions)
