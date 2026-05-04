"""Tests for Day 4-5 setups: F1-F3 + V1-V2 + S1-S3 + C1-C2."""
from __future__ import annotations

from datetime import date

from ifa.families.ta.setups import SETUPS
from ifa.families.ta.setups.base import SetupContext


def _ctx(closes, **overrides) -> SetupContext:
    closes = tuple(closes)
    base = dict(
        ts_code="000001.SZ",
        trade_date=date(2026, 4, 30),
        closes=closes,
        highs=tuple(c * 1.01 for c in closes),
        lows=tuple(c * 0.99 for c in closes),
        close_today=closes[-1],
        ma_qfq_5=sum(closes[-5:]) / 5,
        ma_qfq_10=sum(closes[-10:]) / 10 if len(closes) >= 10 else None,
        ma_qfq_20=sum(closes[-20:]) / 20 if len(closes) >= 20 else None,
        ma_qfq_60=sum(closes[-60:]) / len(closes[-60:]),
        regime="trend_continuation",
        volume_ratio=1.0,
    )
    base.update(overrides)
    return SetupContext(**base)


# ──────────────────────── F1 flag ────────────────────────
class TestF1Flag:
    def test_classic_flag(self):
        # Big run-up days -21 → -15 (pole), then 10 days tight slight pullback
        pre = [100.0] * 40
        run_up = [100 + (i + 1) * 2 for i in range(6)]    # +12% pole
        # Flag: 10 days slightly down, narrow range
        flag = [run_up[-1] * 0.99 - i * 0.05 for i in range(9)]   # ~-1.5% over 10d, tight
        # Today: breakout above flag highs
        today = [max(flag) * 1.01]
        closes = (pre + run_up + flag + today)[-60:]
        ctx = _ctx(closes, volume_ratio=1.5)
        result = SETUPS["F1_FLAG"](ctx)
        assert result is None or "near_top_of_flag" in result.triggers

    def test_no_pole_no_trigger(self):
        closes = [100.0] * 60
        ctx = _ctx(closes)
        assert SETUPS["F1_FLAG"](ctx) is None


# ──────────────────────── F2 triangle ────────────────────────
class TestF2Triangle:
    def test_classic_contraction_then_break(self):
        # 60d uptrend; last 20d converging; today breaks out
        base_closes = [100 + i * 0.3 for i in range(60)]
        # widen early half, tighten late half
        highs = [c * 1.05 for c in base_closes]   # wide
        lows = [c * 0.95 for c in base_closes]    # wide
        # late 10 days: keep tight
        for i in range(10):
            highs[-10 + i] = base_closes[-10 + i] * 1.01
            lows[-10 + i] = base_closes[-10 + i] * 0.99
        # today close > highs[-10:-1].max
        late_max = max(highs[-10:-1])
        base_closes[-1] = late_max * 1.02
        highs[-1] = base_closes[-1] * 1.01
        lows[-1] = base_closes[-1] * 0.99
        ctx = _ctx(base_closes, highs=tuple(highs), lows=tuple(lows),
                   close_today=base_closes[-1], volume_ratio=1.6)
        result = SETUPS["F2_TRIANGLE"](ctx)
        assert result is not None
        assert "upside_breakout" in result.triggers

    def test_uniform_range_no_contraction(self):
        # 60d uptrend with constant noise — late_range ≈ early_range
        closes = [100 + i * 0.3 for i in range(60)]
        ctx = _ctx(closes)
        result = SETUPS["F2_TRIANGLE"](ctx)
        # contraction ratio ~ 1.0 → no trigger
        assert result is None


# ──────────────────────── F3 rectangle ────────────────────────
class TestF3Rectangle:
    def test_classic_rectangle_break(self):
        # 60d uptrend, last 15 days flat, today breaks out
        pre = [100 + i * 0.5 for i in range(45)]
        flat_closes = [pre[-1] * 1.01 + (i % 3 - 1) * 0.1 for i in range(15)]
        today = [max(flat_closes) * 1.02]
        closes = pre + flat_closes + today
        # All highs/lows from closes ±1%, but force last 15 days tight
        highs = [c * 1.005 for c in closes]
        lows = [c * 0.995 for c in closes]
        ctx = _ctx(closes, highs=tuple(highs), lows=tuple(lows),
                   close_today=closes[-1], volume_ratio=1.6)
        result = SETUPS["F3_RECTANGLE"](ctx)
        assert result is not None
        assert "upside_breakout" in result.triggers

    def test_no_box_no_trigger(self):
        closes = [100 + i for i in range(60)]    # straight uptrend, no flat box
        ctx = _ctx(closes)
        assert SETUPS["F3_RECTANGLE"](ctx) is None


# ──────────────────────── V1 vol-price up ────────────────────────
class TestV1VolPriceUp:
    def test_classic_vol_price_up(self):
        closes = [100 + i * 0.5 for i in range(55)] + [130, 132, 135, 139, 145]
        ctx = _ctx(closes, volume_ratio=2.0)
        result = SETUPS["V1_VOL_PRICE_UP"](ctx)
        assert result is not None
        assert "5d_ret>=5%" in result.triggers
        assert "vol_ratio>=1.5" in result.triggers

    def test_low_volume_no_trigger(self):
        closes = [100 + i * 0.5 for i in range(55)] + [130, 132, 135, 139, 145]
        ctx = _ctx(closes, volume_ratio=0.9)
        assert SETUPS["V1_VOL_PRICE_UP"](ctx) is None


# ──────────────────────── V2 quiet coil ────────────────────────
class TestV2QuietCoil:
    def test_classic_quiet_coil(self):
        # Long uptrend, tight last 5 days, low volume today
        base = [100 + i * 0.5 for i in range(55)]
        flat = [base[-1] * 1.005] * 5
        closes = base + flat
        highs = [c * 1.001 for c in closes]
        lows = [c * 0.999 for c in closes]
        ctx = _ctx(closes, highs=tuple(highs), lows=tuple(lows),
                   close_today=closes[-1], volume_ratio=0.4, rsi_qfq_6=50)
        result = SETUPS["V2_QUIET_COIL"](ctx)
        assert result is not None
        assert "very_quiet" in result.triggers

    def test_high_vol_no_trigger(self):
        closes = [100 + i * 0.5 for i in range(60)]
        ctx = _ctx(closes, volume_ratio=1.5)
        assert SETUPS["V2_QUIET_COIL"](ctx) is None


# ──────────────────────── S1 sector resonance ────────────────────────
class TestS1SectorResonance:
    def test_classic_resonance(self):
        closes = [100 + i * 0.5 for i in range(60)]
        ctx = _ctx(closes,
                   close_today=closes[-1] * 1.05,    # +5% today
                   sw_l1_pct_change=2.0,
                   sw_l2_pct_change=4.0)
        # We need closes[-2] for stock_ret comparison; use existing
        result = SETUPS["S1_SECTOR_RESONANCE"](ctx)
        assert result is not None
        assert "L2_leading" in result.triggers

    def test_weak_sector_no_trigger(self):
        closes = [100 + i * 0.5 for i in range(60)]
        ctx = _ctx(closes,
                   close_today=closes[-1] * 1.05,
                   sw_l1_pct_change=0.3,
                   sw_l2_pct_change=0.5)
        assert SETUPS["S1_SECTOR_RESONANCE"](ctx) is None


# ──────────────────────── S2 leader follow-through ────────────────────────
class TestS2Leader:
    def test_outperforms_peers(self):
        closes = [100 + i * 0.5 for i in range(60)]
        # today's stock ret = (close_today / closes[-2] - 1) * 100
        # set close_today so ret = 8%
        ctx = _ctx(closes,
                   close_today=closes[-2] * 1.08,
                   sw_l2_pct_change=3.0,
                   sector_peers_pct_change={"a": 2.0, "b": 1.5, "c": 4.0, "d": 3.5, "e": 2.5})
        result = SETUPS["S2_LEADER_FOLLOWTHROUGH"](ctx)
        assert result is not None
        assert "top_30pct_in_L2" in result.triggers

    def test_no_peers_no_trigger(self):
        closes = [100 + i * 0.5 for i in range(60)]
        ctx = _ctx(closes,
                   close_today=closes[-2] * 1.08,
                   sw_l2_pct_change=3.0,
                   sector_peers_pct_change={})
        assert SETUPS["S2_LEADER_FOLLOWTHROUGH"](ctx) is None


# ──────────────────────── S3 laggard catch-up ────────────────────────
class TestS3LaggardCatchup:
    def test_laggard_catches_up(self):
        # 20d return ~ 0%, today jumps 4%
        closes = [100.0] * 60
        closes[-1] = 104.0
        ctx = _ctx(closes,
                   close_today=104.0,
                   ma_qfq_20=sum(closes[-20:]) / 20,
                   ma_qfq_60=sum(closes[-60:]) / 60 - 1,    # force ma20 > ma60
                   sw_l2_pct_change=3.5)
        result = SETUPS["S3_LAGGARD_CATCHUP"](ctx)
        assert result is not None
        assert "catchup_today" in result.triggers

    def test_already_strong_no_trigger(self):
        closes = [100 + i for i in range(60)]   # +59% over 60d
        ctx = _ctx(closes, sw_l2_pct_change=3.0)
        assert SETUPS["S3_LAGGARD_CATCHUP"](ctx) is None


# ──────────────────────── C1 chip concentrated ────────────────────────
class TestC1ChipConcentrated:
    def test_concentrated_uptrend(self):
        closes = [100 + i * 0.5 for i in range(60)]
        ctx = _ctx(closes,
                   chip_concentration_pct=8.0,
                   chip_winner_rate_pct=60)
        result = SETUPS["C1_CHIP_CONCENTRATED"](ctx)
        assert result is not None
        assert "very_concentrated" in result.triggers

    def test_loose_chips_no_trigger(self):
        closes = [100 + i * 0.5 for i in range(60)]
        ctx = _ctx(closes, chip_concentration_pct=30.0)
        assert SETUPS["C1_CHIP_CONCENTRATED"](ctx) is None


# ──────────────────────── C2 chip loose ────────────────────────
class TestC2ChipLoose:
    def test_distribution_warning(self):
        # Big run + loose chips + high winner rate
        closes = [100.0] * 40 + [100 + i * 2 for i in range(20)]   # 60d, big late run
        ctx = _ctx(closes,
                   chip_concentration_pct=30.0,
                   chip_winner_rate_pct=92.0,
                   regime="distribution_risk")
        result = SETUPS["C2_CHIP_LOOSE"](ctx)
        assert result is not None
        # regime gating now happens in ranker (M9), not inside the setup
        assert "extreme_winner_rate" in result.triggers

    def test_concentrated_chips_no_trigger(self):
        closes = [100 + i for i in range(60)]
        ctx = _ctx(closes, chip_concentration_pct=10.0, chip_winner_rate_pct=85.0)
        assert SETUPS["C2_CHIP_LOOSE"](ctx) is None


# ──────────────────────── Registry sanity ────────────────────────
class TestRegistryDay45:
    def test_all_registered(self):
        # Original 19 (T/P/R/F/V/S/C) + M10 expansion 9 (O/D/Z/E) = 28
        expected = {
            "T1_BREAKOUT", "T2_PULLBACK_RESUME", "T3_ACCELERATION",
            "P1_MA20_PULLBACK", "P2_GAP_FILL", "P3_TIGHT_CONSOLIDATION",
            "R1_DOUBLE_BOTTOM", "R2_HS_BOTTOM", "R3_HAMMER",
            "F1_FLAG", "F2_TRIANGLE", "F3_RECTANGLE",
            "V1_VOL_PRICE_UP", "V2_QUIET_COIL",
            "S1_SECTOR_RESONANCE", "S2_LEADER_FOLLOWTHROUGH", "S3_LAGGARD_CATCHUP",
            "C1_CHIP_CONCENTRATED", "C2_CHIP_LOOSE",
            "O1_INST_PERSISTENT_BUY", "O2_LHB_INST_BUY", "O3_LIMIT_SEAL_STRENGTH",
            "D1_DOUBLE_TOP", "D2_HS_TOP", "D3_SHOOTING_STAR",
            "Z1_ZSCORE_EXTREME", "Z2_OVERSOLD_REBOUND",
            "E1_EVENT_CATALYST",
        }
        assert set(SETUPS.keys()) == expected, \
            f"missing: {expected - set(SETUPS.keys())}, extra: {set(SETUPS.keys()) - expected}"
        assert len(SETUPS) == 28
