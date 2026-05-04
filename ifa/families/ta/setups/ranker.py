"""Rank candidates by score; assign rank, star_rating, in_top_watchlist.

Applies M5.3 governance + M8 winrate-based scoring:
  · Regime gating: +0.1 score boost when current regime is in setup's
    historical `suitable_regimes`.
  · Winrate scaling: setups with weak historical edge get score discount —
    score *= clip(winrate_60d / WINRATE_TARGET, 0.4, 1.0). At 30% winrate
    score is unchanged; at 15% score is halved; floor at 40% of raw.
  · Decay-based suspension:
      decay_score >= -10pp        → ACTIVE
      -15pp <= decay_score < -10  → OBSERVATION_ONLY (kept, never top)
      decay_score < -15pp         → SUSPENDED       (dropped)
  · Top-N diversification: at most TOP_DIVERSITY_CAP candidates from the
    same setup_name in top_watchlist (prevents one setup family flooding).
"""
from __future__ import annotations

from dataclasses import dataclass

from ifa.families.ta.regime.classifier import Regime
from ifa.families.ta.setups.base import Candidate

OBSERVATION_DECAY_FLOOR = -10.0
SUSPENSION_DECAY_FLOOR = -15.0
WINRATE_TARGET_PCT = 30.0       # setup that wins T+10 ≥ 5% at this rate gets full score
WINRATE_FLOOR_RATIO = 0.4       # never discount below 40% of raw score
TOP_DIVERSITY_CAP = 3            # max picks of same setup_name in top_watchlist


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    rank: int               # 1-based, 1 = best
    star_rating: int        # 1-5
    in_top_watchlist: bool
    governance_status: str  # 'active' | 'observation_only' | 'suspended'


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


def _governance_status(decay: float | None) -> str:
    if decay is None:
        return "active"
    if decay < SUSPENSION_DECAY_FLOOR:
        return "suspended"
    if decay < OBSERVATION_DECAY_FLOOR:
        return "observation_only"
    return "active"


def rank(
    candidates: list[Candidate],
    top_n: int = 20,
    *,
    current_regime: Regime | None = None,
    setup_metrics: dict[str, dict] | None = None,
) -> list[RankedCandidate]:
    """Sort descending by score; apply regime gating + decay-based suspension.

    Args:
        candidates: raw setup hits.
        top_n: how many ACTIVE candidates to mark in_top_watchlist.
        current_regime: today's regime (used for gating boost).
        setup_metrics: {setup_name: {decay_score, suitable_regimes}} from
            ta.setup_metrics_daily. Missing → setup treated as ACTIVE / no boost.
    """
    setup_metrics = setup_metrics or {}

    enriched: list[tuple[float, str, Candidate, str]] = []
    for c in candidates:
        m = setup_metrics.get(c.setup_name, {})
        decay = m.get("decay_score")
        status = _governance_status(decay)
        if status == "suspended":
            continue

        boost = 0.0
        suitable = m.get("suitable_regimes") or []
        if current_regime and current_regime in suitable:
            boost = 0.1

        adj_score = min(c.score + boost, 1.0)

        # Winrate scaling — discount weak-edge setups proportionally.
        winrate = m.get("winrate_60d")
        if winrate is not None:
            ratio = max(WINRATE_FLOOR_RATIO,
                        min(1.0, winrate / WINRATE_TARGET_PCT))
            adj_score *= ratio

        enriched.append((adj_score, c.setup_name, c, status))

    enriched.sort(key=lambda t: (-t[0], t[1], t[2].ts_code))

    # Top-watchlist with per-setup diversity cap
    out: list[RankedCandidate] = []
    n_top_assigned = 0
    per_setup_top = {}
    for i, (adj_score, _, c, status) in enumerate(enriched):
        eligible_for_top = (status == "active")
        if eligible_for_top and n_top_assigned < top_n:
            cnt = per_setup_top.get(c.setup_name, 0)
            in_top = cnt < TOP_DIVERSITY_CAP
        else:
            in_top = False
        if in_top:
            n_top_assigned += 1
            per_setup_top[c.setup_name] = per_setup_top.get(c.setup_name, 0) + 1
        out.append(RankedCandidate(
            candidate=c,
            rank=i + 1,
            star_rating=_stars(adj_score),
            in_top_watchlist=in_top,
            governance_status=status,
        ))
    return out
