"""Regime transition matrix — empirical + Laplace smoothing.

Reads the last `lookback_days` of `ta.regime_daily` and computes
P(next_regime | current_regime) using simple counts with α=1 Laplace
smoothing. Returns a dict[Regime, dict[Regime, float]].

This is intentionally simpler than smartmoney/transition_matrix.py:
  · Single time-series (one regime per day market-wide), not per-sector
  · No Bayesian per-sector prior (only one "sector" — the market)
  · No state-machine guards (any regime can transition to any other)

API:
  · build_transition_matrix(engine, lookback_days=120) → TransitionMatrix
  · matrix.predict(current_regime) → dict[Regime, float] (probabilities sum to 1)
  · matrix.most_likely_next(current_regime) → Regime
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.report.timezones import bjt_now
from ifa.families.ta.regime.classifier import REGIMES, Regime

log = logging.getLogger(__name__)


@dataclass
class TransitionMatrix:
    matrix: dict[Regime, dict[Regime, float]]   # P(next | current)
    counts: dict[Regime, dict[Regime, int]]     # raw counts (audit)
    lookback_days: int = 0
    samples: int = 0                             # total transitions seen

    def predict(self, current: Regime) -> dict[Regime, float]:
        """Returns P(next | current). Returns uniform if current is unseen."""
        if current not in self.matrix:
            return {r: 1.0 / len(REGIMES) for r in REGIMES}
        return dict(self.matrix[current])

    def most_likely_next(self, current: Regime) -> tuple[Regime, float]:
        probs = self.predict(current)
        winner = max(probs, key=probs.get)
        return (winner, probs[winner])  # type: ignore[return-value]

    def to_json(self) -> dict:
        return {
            "matrix": {r: dict(v) for r, v in self.matrix.items()},
            "lookback_days": self.lookback_days,
            "samples": self.samples,
        }


def build_transition_matrix(
    engine: Engine,
    *,
    lookback_days: int = 120,
    on_date: date | None = None,
    laplace_alpha: float = 1.0,
) -> TransitionMatrix:
    """Build P(next_regime | current_regime) from `ta.regime_daily`.

    Args:
        engine: SQLAlchemy engine.
        lookback_days: how far back to look for transitions.
        on_date: anchor date (default: today BJT). Useful for backtesting.
        laplace_alpha: smoothing parameter; 1.0 = standard Laplace.
    """
    on_date = on_date or bjt_now().date()
    cutoff = on_date - timedelta(days=lookback_days)

    sql = text("""
        SELECT trade_date, regime
        FROM ta.regime_daily
        WHERE trade_date >= :cutoff AND trade_date <= :on_date
        ORDER BY trade_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"cutoff": cutoff, "on_date": on_date}).fetchall()

    counts: dict[Regime, dict[Regime, int]] = defaultdict(lambda: defaultdict(int))
    samples = 0
    for i in range(1, len(rows)):
        prev = rows[i - 1][1]
        curr = rows[i][1]
        if prev in REGIMES and curr in REGIMES:
            counts[prev][curr] += 1
            samples += 1

    # Laplace-smoothed probabilities
    matrix: dict[Regime, dict[Regime, float]] = {}
    for src in REGIMES:
        src_counts = counts.get(src, {})
        total = sum(src_counts.values()) + laplace_alpha * len(REGIMES)
        matrix[src] = {
            tgt: (src_counts.get(tgt, 0) + laplace_alpha) / total
            for tgt in REGIMES
        }

    return TransitionMatrix(
        matrix=matrix,
        counts={r: dict(v) for r, v in counts.items()},
        lookback_days=lookback_days,
        samples=samples,
    )


def build_from_sequence(
    sequence: list[Regime],
    *,
    laplace_alpha: float = 1.0,
) -> TransitionMatrix:
    """Build matrix from an in-memory regime sequence (test / backtest helper)."""
    counts: dict[Regime, dict[Regime, int]] = defaultdict(lambda: defaultdict(int))
    samples = 0
    for i in range(1, len(sequence)):
        prev = sequence[i - 1]
        curr = sequence[i]
        if prev in REGIMES and curr in REGIMES:
            counts[prev][curr] += 1
            samples += 1

    matrix: dict[Regime, dict[Regime, float]] = {}
    for src in REGIMES:
        src_counts = counts.get(src, {})
        total = sum(src_counts.values()) + laplace_alpha * len(REGIMES)
        matrix[src] = {
            tgt: (src_counts.get(tgt, 0) + laplace_alpha) / total
            for tgt in REGIMES
        }

    return TransitionMatrix(
        matrix=matrix,
        counts={r: dict(v) for r, v in counts.items()},
        lookback_days=len(sequence),
        samples=samples,
    )
