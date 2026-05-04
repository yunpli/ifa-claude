"""Tests for the regime transition matrix."""
from __future__ import annotations

from ifa.families.ta.regime.classifier import REGIMES
from ifa.families.ta.regime.transitions import build_from_sequence


class TestTransitionMatrix:
    def test_all_regimes_present(self):
        m = build_from_sequence(["trend_continuation"])
        # Every source regime gets a row, every target regime is in each row
        assert set(m.matrix.keys()) == set(REGIMES)
        for src in REGIMES:
            assert set(m.matrix[src].keys()) == set(REGIMES)

    def test_rows_sum_to_one(self):
        seq = ["trend_continuation", "early_risk_on", "trend_continuation",
               "cooldown", "trend_continuation"]
        m = build_from_sequence(seq)
        for src in REGIMES:
            row_sum = sum(m.matrix[src].values())
            assert abs(row_sum - 1.0) < 1e-9

    def test_observed_transition_dominates(self):
        # 5 transitions: continuation → early_risk_on every time
        seq = ["trend_continuation", "early_risk_on"] * 5
        # That's: cont→early, early→cont, cont→early, ... = 9 transitions
        m = build_from_sequence(seq)
        probs = m.predict("trend_continuation")
        # Most-likely next from cont should be early_risk_on
        winner, _ = m.most_likely_next("trend_continuation")
        assert winner == "early_risk_on"
        # Self-transition unseen in this sequence; with Laplace alpha=1 and
        # 9 regimes, denominator = 5 + 9 = 14, observed = 5/14 ≈ 0.357,
        # unobserved = 1/14 ≈ 0.071
        assert probs["early_risk_on"] > probs["trend_continuation"] * 3

    def test_unseen_regime_returns_uniform(self):
        seq = ["trend_continuation"]
        m = build_from_sequence(seq)
        # high_difficulty never observed as source; matrix still has the row
        # via Laplace smoothing — every target gets equal share.
        probs = m.predict("high_difficulty")
        # All probabilities should be equal under pure Laplace
        vals = list(probs.values())
        assert max(vals) - min(vals) < 1e-9
        assert abs(sum(vals) - 1.0) < 1e-9

    def test_samples_count(self):
        seq = ["a", "b", "c"]  # invalid regimes — should be filtered
        m = build_from_sequence(seq)
        assert m.samples == 0

        seq = ["trend_continuation", "cooldown", "trend_continuation"]
        m = build_from_sequence(seq)
        assert m.samples == 2  # 2 valid transitions
