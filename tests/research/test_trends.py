"""Tests for the 5-level trend classifier."""
from __future__ import annotations

from ifa.families.research.analyzer.trends import (
    TrendLevel,
    classify_trend,
    classify_trend_from_params,
)


class TestClassifyTrend:
    def test_too_few_points_returns_unknown(self):
        r = classify_trend([1.0, 2.0], min_periods=4)
        assert r.level == TrendLevel.UNKNOWN
        assert r.n_periods == 2

    def test_all_none_returns_unknown(self):
        r = classify_trend([None, None, None, None, None])
        assert r.level == TrendLevel.UNKNOWN

    def test_zero_mean_returns_unknown(self):
        # Zeros mean we cannot normalize the slope
        r = classify_trend([0.0, 0.0, 0.0, 0.0, 0.0])
        assert r.level == TrendLevel.UNKNOWN

    def test_strong_uptrend_is_rapid_up(self):
        # +20% per period
        values = [100, 120, 144, 173, 207, 248]
        r = classify_trend(values, flat_band_pct=5, rapid_threshold_pct=15, min_periods=4)
        assert r.level == TrendLevel.RAPID_UP
        assert r.slope_pct_per_period > 15

    def test_mild_uptrend_is_steady_up(self):
        # +8% per period
        values = [100, 108, 117, 126, 136, 147]
        r = classify_trend(values, flat_band_pct=5, rapid_threshold_pct=15, min_periods=4)
        assert r.level == TrendLevel.STEADY_UP

    def test_flat_series_is_flat(self):
        # Tiny noise around 100
        values = [100, 101, 99, 100, 102, 100]
        r = classify_trend(values, flat_band_pct=5, rapid_threshold_pct=15, min_periods=4)
        assert r.level == TrendLevel.FLAT

    def test_steady_decline_is_steady_down(self):
        values = [100, 92, 85, 78, 72, 66]
        r = classify_trend(values, flat_band_pct=5, rapid_threshold_pct=15, min_periods=4)
        assert r.level == TrendLevel.STEADY_DOWN

    def test_rapid_decline_is_rapid_down(self):
        values = [100, 80, 64, 51, 41, 33]
        r = classify_trend(values, flat_band_pct=5, rapid_threshold_pct=15, min_periods=4)
        assert r.level == TrendLevel.RAPID_DOWN

    def test_none_in_middle_doesnt_break(self):
        values = [100, 110, None, 130, 145, 162]
        r = classify_trend(values, min_periods=4)
        assert r.level in (TrendLevel.RAPID_UP, TrendLevel.STEADY_UP)
        assert r.n_periods == 5

    def test_last_n_window_clip(self):
        # 10 ascending points; last_n=4 should ignore the early stable plateau
        values = [100, 100, 100, 100, 100, 100, 100, 110, 120, 130]
        r = classify_trend(values, last_n=4, min_periods=4)
        # last_n=4 sees [100, 110, 120, 130] → strong uptrend
        assert r.level in (TrendLevel.RAPID_UP, TrendLevel.STEADY_UP)


class TestParamsAdapter:
    def test_reads_thresholds_from_dict(self):
        params = {"trends": {"flat_band_pct": 2, "rapid_threshold_pct": 10, "min_periods": 3}}
        r = classify_trend_from_params([100, 105, 110, 115, 120], params)
        assert r.level in (TrendLevel.STEADY_UP, TrendLevel.RAPID_UP)

    def test_falls_back_to_defaults_when_block_missing(self):
        # Empty params → defaults (flat=5, rapid=15, min=4)
        r = classify_trend_from_params([100, 110, 120, 130, 140], {})
        assert r.level != TrendLevel.UNKNOWN
