"""Rank candidates by score; assign rank, star_rating, in_top_watchlist."""
from __future__ import annotations

from dataclasses import dataclass

from ifa.families.ta.setups.base import Candidate


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    rank: int               # 1-based, 1 = best
    star_rating: int        # 1-5
    in_top_watchlist: bool


def _stars(score: float) -> int:
    if score >= 0.85:
        return 5
    if score >= 0.75:
        return 4
    if score >= 0.65:
        return 3
    if score >= 0.55:
        return 2
    return 1


def rank(candidates: list[Candidate], top_n: int = 20) -> list[RankedCandidate]:
    """Sort descending by score, ties broken by setup_name then ts_code (deterministic)."""
    ordered = sorted(
        candidates,
        key=lambda c: (-c.score, c.setup_name, c.ts_code),
    )
    return [
        RankedCandidate(
            candidate=c,
            rank=i + 1,
            star_rating=_stars(c.score),
            in_top_watchlist=(i < top_n),
        )
        for i, c in enumerate(ordered)
    ]
