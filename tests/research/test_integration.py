"""End-to-end smoke test using cached data (no Tushare API calls).

Skipped when:
  · The DB is unreachable
  · No cached api_cache rows exist for the test stock (run bootstrap first)

These tests catch regressions across the full snapshot → factor → score →
render pipeline. They don't validate specific values (those drift quarterly);
they assert structural integrity and that no factor crashes.
"""
from __future__ import annotations

from datetime import date

import pytest

# Reusable test stock — must have cached api_cache populated.
TEST_TS_CODE = "001339.SZ"


@pytest.fixture(scope="module")
def engine():
    try:
        from ifa.core.db import get_engine
        eng = get_engine()
        # Ping
        with eng.connect() as c:
            c.execute(__import__("sqlalchemy").text("SELECT 1"))
        return eng
    except Exception as e:
        pytest.skip(f"DB unavailable: {e}")


@pytest.fixture(scope="module")
def cached_snap(engine):
    from sqlalchemy import text

    from ifa.families.research.analyzer.data import load_company_snapshot
    from ifa.families.research.resolver import resolve

    with engine.connect() as c:
        n = c.execute(
            text("SELECT COUNT(*) FROM research.api_cache WHERE ts_code = :tc"),
            {"tc": TEST_TS_CODE},
        ).scalar()
    if not n:
        pytest.skip(f"No cached api_cache rows for {TEST_TS_CODE} — run bootstrap")

    company = resolve(TEST_TS_CODE, engine)
    return load_company_snapshot(engine, company, data_cutoff_date=date.today())


class TestSnapshotLoad:
    def test_company_resolved(self, cached_snap):
        assert cached_snap.company.ts_code == TEST_TS_CODE
        assert cached_snap.company.name  # non-empty

    def test_has_latest_period(self, cached_snap):
        assert cached_snap.latest_period
        # Should look like YYYYMMDD
        assert cached_snap.latest_period.isdigit()
        assert len(cached_snap.latest_period) == 8

    def test_revenue_series_populated(self, cached_snap):
        assert cached_snap.revenue_series is not None
        assert len(cached_snap.revenue_series.values) > 0


class TestFactorCompute:
    def test_all_5_families_dont_crash(self, cached_snap):
        from ifa.families.research.analyzer.balance import compute_balance
        from ifa.families.research.analyzer.cash_quality import compute_cash_quality
        from ifa.families.research.analyzer.factors import load_params
        from ifa.families.research.analyzer.governance import compute_governance
        from ifa.families.research.analyzer.growth import compute_growth
        from ifa.families.research.analyzer.profitability import compute_profitability

        params = load_params()
        # Each family must produce a non-empty list of FactorResult
        assert len(compute_profitability(cached_snap, params)) == 6
        assert len(compute_growth(cached_snap, params)) == 4
        assert len(compute_cash_quality(cached_snap, params)) == 5
        assert len(compute_balance(cached_snap, params)) == 6
        assert len(compute_governance(cached_snap, params)) == 7

    def test_no_factor_returns_negative_count_or_inf(self, cached_snap):
        # Smoke: no NaN / inf leaks through despite real data noise
        import math

        from ifa.families.research.analyzer.factors import load_params
        from ifa.families.research.analyzer.profitability import compute_profitability

        params = load_params()
        for r in compute_profitability(cached_snap, params):
            if r.value is not None:
                v = float(r.value)
                assert not math.isnan(v), f"{r.spec.name} returned NaN"
                assert not math.isinf(v), f"{r.spec.name} returned Inf"


class TestTiers:
    """Three-tier reports: quick / standard / deep should select sections correctly."""

    def _build_results(self, snap):
        from ifa.families.research.analyzer.balance import compute_balance
        from ifa.families.research.analyzer.cash_quality import compute_cash_quality
        from ifa.families.research.analyzer.factors import load_params
        from ifa.families.research.analyzer.governance import compute_governance
        from ifa.families.research.analyzer.growth import compute_growth
        from ifa.families.research.analyzer.profitability import compute_profitability
        from ifa.families.research.analyzer.scoring import score_results

        params = load_params()
        results = {
            "profitability": compute_profitability(snap, params),
            "growth": compute_growth(snap, params),
            "cash_quality": compute_cash_quality(snap, params),
            "balance": compute_balance(snap, params),
            "governance": compute_governance(snap, params),
        }
        return results, params, score_results(results, params)

    def test_invalid_tier_raises(self, cached_snap):
        from ifa.families.research.report import build_research_report
        results, params, scoring = self._build_results(cached_snap)
        import pytest
        with pytest.raises(ValueError, match="tier must be one of"):
            build_research_report(cached_snap, results, scoring, params,
                                  tier="ultra")  # invalid

    def test_quick_excludes_trends_and_timeline(self, cached_snap):
        from ifa.families.research.report import build_research_report
        results, params, scoring = self._build_results(cached_snap)
        report = build_research_report(cached_snap, results, scoring, params,
                                       tier="quick")
        types = [s["type"] for s in report["sections"]]
        assert "research_trend_grid" not in types
        assert "research_timeline" not in types
        assert "research_watchpoints" not in types
        # But always include core
        assert "research_overview" in types
        assert "research_radar" in types
        assert "research_disclaimer" in types

    def test_standard_includes_trends_and_timeline(self, cached_snap):
        from ifa.families.research.report import build_research_report
        results, params, scoring = self._build_results(cached_snap)
        report = build_research_report(cached_snap, results, scoring, params,
                                       tier="standard")
        types = [s["type"] for s in report["sections"]]
        assert "research_trend_grid" in types
        assert "research_timeline" in types
        # No watchpoints without augmenter
        assert "research_watchpoints" not in types

    def test_deep_without_augmenter_skips_watchpoints_gracefully(self, cached_snap):
        from ifa.families.research.report import build_research_report
        results, params, scoring = self._build_results(cached_snap)
        # Deep tier without augmenter — should not crash, just no watchpoints
        report = build_research_report(cached_snap, results, scoring, params,
                                       tier="deep", augmenter=None)
        types = [s["type"] for s in report["sections"]]
        assert "research_watchpoints" not in types

    def test_quick_ignores_augmenter_for_cost_savings(self, cached_snap):
        from ifa.families.research.report import build_research_report
        results, params, scoring = self._build_results(cached_snap)

        class _MockAugmenter:
            called = False
            def narratives_for_report(self, *a, **kw):
                _MockAugmenter.called = True
                return {}

        mock = _MockAugmenter()
        build_research_report(cached_snap, results, scoring, params,
                              tier="quick", augmenter=mock)
        assert not _MockAugmenter.called, \
            "Quick tier must NOT call augmenter (saves API cost)"


class TestScoreAndRender:
    def test_full_pipeline_renders_html(self, cached_snap):
        from ifa.families.research.analyzer.balance import compute_balance
        from ifa.families.research.analyzer.cash_quality import compute_cash_quality
        from ifa.families.research.analyzer.factors import load_params
        from ifa.families.research.analyzer.governance import compute_governance
        from ifa.families.research.analyzer.growth import compute_growth
        from ifa.families.research.analyzer.profitability import compute_profitability
        from ifa.families.research.analyzer.scoring import score_results
        from ifa.families.research.report import build_research_report
        from ifa.families.research.report.html import HtmlRenderer
        from ifa.families.research.report.markdown import render_markdown

        params = load_params()
        results_by_family = {
            "profitability": compute_profitability(cached_snap, params),
            "growth": compute_growth(cached_snap, params),
            "cash_quality": compute_cash_quality(cached_snap, params),
            "balance": compute_balance(cached_snap, params),
            "governance": compute_governance(cached_snap, params),
        }
        scoring = score_results(results_by_family, params)
        report = build_research_report(cached_snap, results_by_family,
                                       scoring, params)

        # Markdown must be non-empty
        md = render_markdown(report)
        assert "综合评分" in md
        assert TEST_TS_CODE in md or cached_snap.company.name in md

        # HTML must be valid-looking
        html = HtmlRenderer().render(report=report)
        assert html.startswith("<!doctype html>")
        assert html.endswith("</html>\n") or html.endswith("</html>")
        # Each section type renders something
        assert "5 维评分" in html
        assert "公司概况" in html
