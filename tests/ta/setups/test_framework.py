"""Tests for TA-M4 setups framework + T1 breakout reference setup."""
from __future__ import annotations

from datetime import date

from ifa.families.ta.setups import SETUPS, Candidate, SetupContext
from ifa.families.ta.setups.t1_breakout import T1_BREAKOUT


def _ctx(closes: list[float], **overrides) -> SetupContext:
    """Build a context with safe defaults; closes is the price series."""
    base = dict(
        ts_code="000001.SZ",
        trade_date=date(2026, 4, 30),
        closes=tuple(closes),
        close_today=closes[-1] if closes else None,
        ma_qfq_20=sum(closes[-20:]) / 20 if len(closes) >= 20 else None,
        ma_qfq_60=sum(closes[-60:]) / len(closes[-60:]) if closes else None,
        regime="trend_continuation",
        volume_ratio=1.2,
    )
    base.update(overrides)
    return SetupContext(**base)


class TestRegistry:
    def test_t1_registered(self):
        assert "T1_BREAKOUT" in SETUPS
        assert SETUPS["T1_BREAKOUT"] is T1_BREAKOUT


class TestT1Breakout:
    def test_classic_breakout_triggers(self):
        # Steady uptrend: each day +1, today breaks 20d high
        closes = [100 + i for i in range(60)]   # 100..159
        ctx = _ctx(closes)
        result = T1_BREAKOUT(ctx)
        assert result is not None
        assert result.setup_name == "T1_BREAKOUT"
        assert "20d_breakout" in result.triggers
        # regime bonus moved to ranker (M9) — no longer a setup-level trigger
        assert result.score >= 0.5

    def test_volume_boost_score(self):
        closes = [100 + i for i in range(60)]
        ctx_low_vol = _ctx(closes, volume_ratio=1.0)
        ctx_high_vol = _ctx(closes, volume_ratio=2.0)
        r_low = T1_BREAKOUT(ctx_low_vol)
        r_high = T1_BREAKOUT(ctx_high_vol)
        assert r_high.score > r_low.score
        assert "volume_confirmation" in r_high.triggers
        assert "volume_confirmation" not in r_low.triggers

    def test_below_ma20_does_not_trigger(self):
        # close way below MA20
        closes = [100 + i for i in range(59)] + [50.0]
        ctx = _ctx(closes)
        assert T1_BREAKOUT(ctx) is None

    def test_ma20_below_ma60_does_not_trigger(self):
        # downtrend: prices falling — ma20 will be below ma60
        closes = [200 - i for i in range(60)]
        ctx = _ctx(closes, close_today=closes[-1])
        # force ma stack to confirm downtrend
        ctx_dt = _ctx(closes,
                      ma_qfq_20=sum(closes[-20:]) / 20,
                      ma_qfq_60=sum(closes) / 60)
        assert ctx_dt.ma_qfq_20 < ctx_dt.ma_qfq_60
        assert T1_BREAKOUT(ctx_dt) is None

    def test_no_new_high_does_not_trigger(self):
        # Sideways with a peak 10 days ago, today below that peak
        closes = [100.0] * 50 + [120.0] + [110.0] * 9   # peak at -10, today=110
        # all MAs are ~100-105, close=110>ma20, but not new 20d high
        ctx = _ctx(closes)
        assert T1_BREAKOUT(ctx) is None

    def test_missing_data_returns_none(self):
        # Too few closes
        ctx = _ctx([100.0] * 10)
        assert T1_BREAKOUT(ctx) is None

    def test_no_regime_still_fires(self):
        # Regime bonus moved to ranker; setup itself is regime-agnostic now.
        closes = [100 + i for i in range(60)]
        ctx = _ctx(closes, regime=None)
        result = T1_BREAKOUT(ctx)
        assert result is not None
        assert "regime_tailwind" not in result.triggers

    def test_score_bounded(self):
        closes = [100 + i for i in range(60)]
        ctx = _ctx(closes, volume_ratio=10.0)
        result = T1_BREAKOUT(ctx)
        assert 0.0 <= result.score <= 1.0


class TestCandidateShape:
    def test_evidence_keys_present(self):
        closes = [100 + i for i in range(60)]
        result = T1_BREAKOUT(_ctx(closes))
        assert isinstance(result, Candidate)
        for key in ("close", "ma20", "ma60", "close_20d_ago",
                    "prior_20d_high", "gain_20d_pct", "regime"):
            assert key in result.evidence
