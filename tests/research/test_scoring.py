"""Tests for the 5-dim scoring aggregation."""
from __future__ import annotations

from decimal import Decimal

from ifa.families.research.analyzer.factors import (
    FactorResult,
    FactorSpec,
    FactorStatus,
)
from ifa.families.research.analyzer.scoring import (
    STATUS_BASE_SCORE,
    score_results,
)


def _spec(name: str, family: str, direction: str = "higher_better",
          industry_sensitive: bool = True) -> FactorSpec:
    return FactorSpec(
        name=name, display_name_zh=name, family=family,
        formula="—", unit="%", source_apis=("test",),
        industry_sensitive=industry_sensitive,
        direction=direction,
        interpretation_template="{value}",
    )


def _result(name: str, family: str, value: float, status: FactorStatus,
            peer_pct: float | None = None) -> FactorResult:
    r = FactorResult(
        spec=_spec(name, family),
        value=Decimal(str(value)),
        status=status,
        period="20260331",
    )
    r.peer_percentile = peer_pct
    return r


class TestStatusBaseScores:
    def test_green_is_80(self):
        assert STATUS_BASE_SCORE[FactorStatus.GREEN] == 80.0

    def test_red_is_20(self):
        assert STATUS_BASE_SCORE[FactorStatus.RED] == 20.0

    def test_unknown_is_none(self):
        assert STATUS_BASE_SCORE[FactorStatus.UNKNOWN] is None


class TestScoreResults:
    def _params_with_weights(self, family: str, weights: dict) -> dict:
        return {"scoring": {family: weights}}

    def test_all_unknown_family_score_is_none(self):
        results = [_result("X", "profitability", 0, FactorStatus.UNKNOWN)]
        params = self._params_with_weights("profitability", {"x": 1.0})
        sc = score_results({"profitability": results}, params)
        assert sc.families["profitability"].score is None

    def test_pure_status_no_peer(self):
        # GPM=GREEN(80), NPM=RED(20), each weight 0.5 → score = 50
        results = [
            _result("GPM", "profitability", 30, FactorStatus.GREEN),
            _result("NPM", "profitability", 1, FactorStatus.RED),
        ]
        params = self._params_with_weights("profitability", {"gpm": 0.5, "npm": 0.5})
        sc = score_results({"profitability": results}, params)
        assert abs(sc.families["profitability"].score - 50.0) < 0.01

    def test_blend_with_peer(self):
        # ROE: RED (base=20), peer_pct=100 → final = 0.5*20 + 0.5*100 = 60
        results = [_result("ROE", "profitability", 4, FactorStatus.RED, peer_pct=100)]
        params = self._params_with_weights("profitability", {"roe": 1.0})
        sc = score_results({"profitability": results}, params)
        assert abs(sc.families["profitability"].score - 60.0) < 0.01

    def test_missing_factor_does_not_dilute(self):
        # Two factors: one with full weight + value, one UNKNOWN with partial weight
        results = [
            _result("GPM", "profitability", 30, FactorStatus.GREEN),
            _result("NPM_DEDT", "profitability", 0, FactorStatus.UNKNOWN),
        ]
        params = self._params_with_weights("profitability", {"gpm": 0.5, "npm_dedt": 0.5})
        sc = score_results({"profitability": results}, params, min_weight_coverage=0.4)
        # GPM contributes 80; coverage = 0.5/1.0 = 0.5 ≥ 0.4 → score uses only GPM
        assert sc.families["profitability"].score == 80.0
        assert abs(sc.families["profitability"].weight_coverage - 0.5) < 0.01

    def test_below_min_coverage_yields_none(self):
        results = [
            _result("GPM", "profitability", 30, FactorStatus.GREEN),
            _result("NPM_DEDT", "profitability", 0, FactorStatus.UNKNOWN),
        ]
        # Weights 0.1 / 0.9 — only the small one contributed → coverage 10% < 50% threshold
        params = self._params_with_weights("profitability", {"gpm": 0.1, "npm_dedt": 0.9})
        sc = score_results({"profitability": results}, params, min_weight_coverage=0.5)
        assert sc.families["profitability"].score is None

    def test_overall_is_average_of_family_scores(self):
        # Two families, one scores 80, the other 40 → overall = 60
        results = {
            "profitability": [_result("X", "profitability", 1, FactorStatus.GREEN)],
            "growth":        [_result("Y", "growth", 1, FactorStatus.RED)],
        }
        params = {
            "scoring": {
                "profitability": {"x": 1.0},
                "growth": {"y": 1.0},
            }
        }
        sc = score_results(results, params)
        assert abs(sc.overall_score - 50.0) < 0.01

    def test_status_thresholds(self):
        # ≥70 GREEN, 50-70 YELLOW, <50 RED
        from ifa.families.research.analyzer.scoring import _verdict_status

        assert _verdict_status(80) == FactorStatus.GREEN
        assert _verdict_status(70) == FactorStatus.GREEN
        assert _verdict_status(60) == FactorStatus.YELLOW
        assert _verdict_status(50) == FactorStatus.YELLOW
        assert _verdict_status(40) == FactorStatus.RED
        assert _verdict_status(None) == FactorStatus.UNKNOWN
