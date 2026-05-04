"""Tests for §10 analyst coverage stats (rule-based, deterministic)."""
from __future__ import annotations

from datetime import date

from ifa.families.research.analyzer.analyst_coverage import compute_coverage


def _r(trade_date: str, inst: str = "中邮证券", title: str = "X") -> dict:
    return {"trade_date": trade_date, "inst_csname": inst, "title": title}


class TestCoverage:
    def test_empty_input(self):
        cov = compute_coverage([])
        assert cov.total_reports == 0
        assert cov.top_institutions == []

    def test_basic_count(self):
        cov = compute_coverage(
            [_r("20260408"), _r("20260403"), _r("20260315")],
            on_date=date(2026, 4, 30),
        )
        assert cov.total_reports == 3
        assert cov.latest_report_date == "2026-04-08"
        assert cov.days_since_latest == 22

    def test_coverage_gap_warning_fires_after_threshold(self):
        cov = compute_coverage(
            [_r("20251220")],   # ~120 days before May 1
            on_date=date(2026, 4, 30),
        )
        assert cov.coverage_gap_warning is True

    def test_no_gap_warning_when_recent(self):
        cov = compute_coverage(
            [_r("20260420")],
            on_date=date(2026, 4, 30),
        )
        assert cov.coverage_gap_warning is False

    def test_top_institutions_ordered(self):
        cov = compute_coverage([
            _r("20260408", inst="中邮证券"),
            _r("20260407", inst="中邮证券"),
            _r("20260406", inst="华泰证券"),
            _r("20260405", inst="国金证券"),
        ], on_date=date(2026, 4, 30))
        assert cov.top_institutions[0]["name"] == "中邮证券"
        assert cov.top_institutions[0]["count"] == 2

    def test_monthly_buckets_dense(self):
        cov = compute_coverage(
            [_r("20260408"), _r("20260103")],
            on_date=date(2026, 4, 30),
            months_back=6,
        )
        # Should have all 6 months represented even if zero count
        months = [m["month"] for m in cov.reports_by_month]
        assert "202604" in months
        assert "202601" in months
        # Counts should match
        m_apr = next(m for m in cov.reports_by_month if m["month"] == "202604")
        assert m_apr["count"] == 1
        m_jan = next(m for m in cov.reports_by_month if m["month"] == "202601")
        assert m_jan["count"] == 1

    def test_handles_malformed_dates(self):
        cov = compute_coverage([
            _r("20260408"),
            _r(""),                 # missing date
            _r("not-a-date"),       # garbage
            _r("20260315"),
        ], on_date=date(2026, 4, 30))
        # Bad rows are dropped silently
        assert cov.total_reports == 2

    def test_sort_stability_with_same_date(self):
        # Earlier bug: sort tried to compare dicts when dates tied.
        # Fixed by using key=lambda x: x[0]. This test guards regression.
        rows = [_r("20260408", inst="A"), _r("20260408", inst="B"),
                _r("20260408", inst="C")]
        cov = compute_coverage(rows, on_date=date(2026, 4, 30))
        assert cov.total_reports == 3  # didn't crash on same-date sort
