"""Tests for TA-M3 regime classifier (rule-based, deterministic)."""
from __future__ import annotations

from datetime import date

import pytest

from ifa.families.ta.regime.classifier import (
    REGIMES,
    RegimeContext,
    classify_regime,
)


def _ctx(**overrides) -> RegimeContext:
    """Build a context with safe defaults; tests override what they care about."""
    base = RegimeContext(trade_date=date(2026, 4, 30))
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


class TestTrendContinuation:
    def test_classic_trend(self):
        ctx = _ctx(
            sse_close=3500.0,
            sse_ma5=3450.0, sse_ma20=3400.0, sse_ma20_prev=3380.0,
            n_up=3000, n_down=1500,
            market_amount_yuan=1.0e12, market_amount_yuan_ma20=1.0e12,
        )
        result = classify_regime(ctx)
        assert result.regime == "trend_continuation"
        assert result.confidence >= 0.6

    def test_falling_ma20_kills_continuation(self):
        ctx = _ctx(
            sse_close=3500.0,
            sse_ma5=3450.0, sse_ma20=3400.0, sse_ma20_prev=3420.0,  # falling
            n_up=3000, n_down=1500,
        )
        result = classify_regime(ctx)
        # 20MA falling → not continuation
        assert result.regime != "trend_continuation"


class TestEarlyRiskOn:
    def test_limit_up_surge_with_rising_ma(self):
        ctx = _ctx(
            sse_ma20=3400.0, sse_ma20_prev=3390.0,
            n_limit_up=80, n_limit_up_prev=40,        # +100%
            consecutive_lb_high=4,
            hsgt_net_pct_60d=70.0,
        )
        result = classify_regime(ctx)
        assert result.regime == "early_risk_on"


class TestEmotionalClimax:
    def test_high_climax(self):
        ctx = _ctx(
            sse_ma20=3500.0, sse_ma20_prev=3480.0,
            sse_ma5=3550.0,
            n_limit_up=150, n_limit_up_prev=100,
            consecutive_lb_high=10,
            hsgt_net_pct_60d=95.0,
            market_amount_yuan=1.5e12, market_amount_yuan_ma20=1.0e12,
            n_up=4000, n_down=500,
        )
        result = classify_regime(ctx)
        assert result.regime == "emotional_climax"


class TestCooldown:
    def test_typical_cooldown(self):
        ctx = _ctx(
            sse_ma5=3300.0, sse_ma20=3400.0,
            n_limit_up=15, n_limit_up_prev=40,    # -62%
            n_limit_down=30,
            n_up=1500, n_down=3500,
        )
        result = classify_regime(ctx)
        assert result.regime == "cooldown"


class TestRangeBound:
    def test_low_volatility_intertwined_ma(self):
        ctx = _ctx(
            sse_ma5=3402.0, sse_ma20=3400.0,    # very close
            sse_ma20_prev=3398.0,
            sse_volatility_20d_pct=5.5,         # < 8%
            market_amount_yuan=9.5e11, market_amount_yuan_ma20=1.0e12,
            n_up=2400, n_down=2300,
        )
        result = classify_regime(ctx)
        assert result.regime == "range_bound"


class TestSectorRotation:
    def test_high_sector_dispersion(self):
        ctx = _ctx(
            sse_ma20=3400.0, sse_ma20_prev=3402.0,   # not trending
            sector_pct_change_std=2.5,                 # high dispersion
            market_amount_yuan=1.2e12, market_amount_yuan_ma20=1.0e12,
            n_up=2200, n_down=2200,
        )
        result = classify_regime(ctx)
        assert result.regime == "sector_rotation"


class TestDistributionRisk:
    def test_top_pattern(self):
        ctx = _ctx(
            sse_ma5=3380.0, sse_ma20=3400.0,    # 5 below 20
            sse_ma20_prev=3380.0,                # 20 still rising overall
            n_up=1800, n_down=2400,
            market_amount_yuan=1.3e12, market_amount_yuan_ma20=1.0e12,
            hsgt_net_pct_60d=15.0,
        )
        result = classify_regime(ctx)
        assert result.regime == "distribution_risk"


class TestHighDifficultyFallback:
    def test_no_strong_signal_falls_back(self):
        # Almost everything missing → no detector scores high
        ctx = _ctx()
        result = classify_regime(ctx)
        assert result.regime == "high_difficulty"
        assert result.confidence == 0.5

    def test_weak_signals_fall_back(self):
        ctx = _ctx(n_up=2100, n_down=2000)   # barely up but no other signal
        result = classify_regime(ctx)
        # Either weak_rebound or high_difficulty; confidence shouldn't be high
        assert result.confidence < 0.7


class TestEvidenceCapture:
    def test_evidence_includes_all_scores(self):
        ctx = _ctx(
            sse_ma5=3450.0, sse_ma20=3400.0, sse_ma20_prev=3380.0,
            n_up=3000, n_down=1500,
        )
        result = classify_regime(ctx)
        scores = result.evidence["scores"]
        # All 8 scoreable regimes (high_difficulty is implicit)
        assert len(scores) == 8
        for r in scores:
            assert 0.0 <= scores[r] <= 1.0


class TestRegimeNamesStable:
    def test_all_names_present_in_REGIMES(self):
        # Locks in the canonical 9 regimes; failure means someone renamed one
        expected = {
            "trend_continuation", "early_risk_on", "weak_rebound",
            "range_bound", "sector_rotation", "emotional_climax",
            "distribution_risk", "cooldown", "high_difficulty",
        }
        assert set(REGIMES) == expected
