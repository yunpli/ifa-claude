"""Rank candidates by score; assign rank, star_rating, in_top_watchlist.

Applies M5.3 governance + M8 winrate scoring + M9 multi-strategy resonance:
  · Regime gating: +0.1 score boost when current regime is in setup's
    historical `suitable_regimes`.
  · Winrate scaling: setups with weak historical edge get score discount —
    score *= clip(winrate_60d / WINRATE_TARGET, 0.4, 1.0). At 30% winrate
    score is unchanged; at 15% score is halved; floor at 40% of raw.
  · Decay-based suspension:
      decay_score >= -10pp        → ACTIVE
      -15pp <= decay_score < -10  → OBSERVATION_ONLY (kept, never top)
      decay_score < -15pp         → SUSPENDED       (dropped)
  · **Multi-strategy resonance** (M9 — per-stock aggregation):
      stock_score = MAX(per-strategy adj_score)
                  + 0.05 × min(extra_distinct_families, 3)
      Different SETUP FAMILIES (T/P/R/F/V/S/C) confirming the same stock add
      conviction; different setups in the same family don't (avoids R1+R2
      double-counting the same morphology). Cap at +0.15 (3 extra families).
  · Star rating now applies to per-stock score, NOT per candidate.
  · in_top_watchlist marks the top-N **stocks** (not rows). All rows for
    those stocks inherit the flag.
  · Top diversity: max TOP_DIVERSITY_CAP candidate ROWS per setup_name in
    in_top_watchlist (prevents one setup flooding the audit table).
"""
from __future__ import annotations

from dataclasses import dataclass

from ifa.families.ta.params import load_params
from ifa.families.ta.regime.classifier import Regime
from ifa.families.ta.setups.base import Candidate


def _ranker_params() -> dict:
    return load_params()["ranker"]


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    rank: int                # 1-based per-stock rank (rows for same stock share)
    star_rating: int         # 1-5 (based on stock_score)
    in_top_watchlist: bool   # legacy — True if Tier A (重点)
    governance_status: str   # 'active' | 'observation_only' | 'suspended'
    stock_score: float       # per-stock aggregate (after sector_factor)
    raw_stock_score: float   # before sector_factor applied — for audit
    sector_factor: float     # M9.7 multiplier ∈ [quality_min, quality_max]
    sector_role: str | None  # SmartMoney sector role (主线/中军/...)
    sector_cycle_phase: str | None
    resonance_count: int     # # of distinct families confirming this stock
    resonance_families: tuple[str, ...]   # e.g. ("T", "V", "S")
    tier: str                # 'A' (重点 top10) | 'B' (候选 next20) | 'C' (观察 next100) | ''


# Setup name → family (first letter is the family code)
def _family_of(setup_name: str) -> str:
    return setup_name[0] if setup_name else ""


RESONANCE_BONUS_PER_FAMILY = 0.05
RESONANCE_BONUS_CAP = 0.15

# Natural ceiling of stock_score. Used to map internal scores → 0-99.999 display.
# Components: raw 0.8 + regime 0.10 + winrate ≤1.0 multiplier + resonance 0.15.
DISPLAY_MAX = 1.05


def _stars(score: float) -> int:
    """Star rating based on display percentage (0-99.999).

    5★ if display ≥ 85, 4★ ≥ 75, 3★ ≥ 65, 2★ ≥ 55, else 1★.
    """
    pct = score / DISPLAY_MAX
    if pct >= 0.85:
        return 5
    if pct >= 0.75:
        return 4
    if pct >= 0.65:
        return 3
    if pct >= 0.55:
        return 2
    return 1


def _governance_status(decay: float | None) -> str:
    if decay is None:
        return "active"
    p = _ranker_params()["decay"]
    if decay < p["suspension_floor_pp"]:
        return "suspended"
    if decay < p["observation_floor_pp"]:
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
    rp = _ranker_params()
    boost_pp = rp["regime_boost"]
    wr_target = rp["winrate"]["target_pct"]
    wr_floor = rp["winrate"]["floor_ratio"]

    # Pass 1: per-row score adjustment + governance filter
    # Continuous regime boost (M9.6): if regime_winrates JSONB exposes this
    # regime's winrate vs the setup's overall winrate, boost ∝ (regime_wr/overall - 1)
    # in [-0.05, +0.20]. Falls back to legacy boolean +regime_boost when JSONB
    # is empty (cold start / not enough samples).
    enriched: list[tuple[float, Candidate, str, float | None]] = []
    for c in candidates:
        m = setup_metrics.get(c.setup_name, {})
        decay = m.get("decay_score")
        status = _governance_status(decay)
        if status == "suspended":
            continue

        boost = 0.0
        regime_winrates = m.get("regime_winrates") or {}
        overall_wr = m.get("winrate_60d")
        suitable = m.get("suitable_regimes") or []
        if current_regime:
            regime_wr = regime_winrates.get(current_regime)
            if regime_wr is not None and overall_wr and overall_wr > 0:
                # Continuous boost — scaled by relative regime advantage
                ratio_r = regime_wr / overall_wr
                boost = max(-0.05, min(0.20, (ratio_r - 1.0) * 0.20))
            elif current_regime in suitable:
                # Cold-start fallback (no per-regime data yet)
                boost = boost_pp

        adj_score = c.score + boost

        if overall_wr is not None:
            ratio_w = max(wr_floor, min(1.0, overall_wr / wr_target))
            adj_score *= ratio_w

        # M10 P2 Q3 (revised) — gentle nudge based on combined_score_60d.
        #
        # Empirical finding: aggressive demote (factor 0.30 at combined=-0.5)
        # backfires in regime-shift conditions because combined_60d is a
        # LAGGED signal — by the time a setup's combined drops, the regime
        # may have already shifted and demoting locks in stale data.
        #
        # Revised mapping (much weaker):
        #   ≤ -1.0 → 0.80  (mild discount even for terrible setups)
        #   =  0.0 → 1.00  (neutral)
        #   ≥ +1.0 → 1.20  (mild boost for strong-edge setups)
        # Linear; clipped to [0.80, 1.20]. Maximum effect ≤ 20%.
        combined = m.get("combined_score_60d")
        if combined is not None:
            c_factor = max(0.80, min(1.20, 1.0 + 0.20 * float(combined)))
            adj_score *= c_factor

        enriched.append((adj_score, c, status, overall_wr))

    # M9.7 — sector_flow Layer 2 multiplier params
    from ifa.families.ta.params import load_params
    sf_params = load_params().get("sector_flow", {})
    quality_min = sf_params.get("quality_weight_min", 0.6)
    quality_max = sf_params.get("quality_weight_max", 1.0)
    quality_span = quality_max - quality_min

    # M9.7 — track sector_role/phase/quality per stock from candidate.evidence
    # (scanner injects these from SetupContext)
    sector_role_by_stock: dict[str, str | None] = {}
    sector_phase_by_stock: dict[str, str | None] = {}
    sector_quality_by_stock: dict[str, float | None] = {}
    for adj_score, c, status, _wr in enriched:
        if c.ts_code in sector_role_by_stock:
            continue
        ev = c.evidence if isinstance(c.evidence, dict) else {}
        sector_role_by_stock[c.ts_code] = ev.get("sector_role")
        sector_phase_by_stock[c.ts_code] = ev.get("sector_cycle_phase")
        sector_quality_by_stock[c.ts_code] = ev.get("sector_quality")

    # Pass 2: per-stock aggregation with **Bayesian resonance** (M9.6):
    # Each family's confirming signal is weighted by:
    #   (a) its own adj_score (continuous strength)
    #   (b) its setup's historical winrate (60d) — high-edge setups carry more weight
    # Combined: extra_bonus_i = score_i × base_weight_i × (winrate_i / WR_TARGET)
    # → strong family with high historical winrate ⟹ much bigger bonus contribution.
    by_stock: dict[str, dict] = {}
    for adj_score, c, status, overall_wr in enriched:
        rec = by_stock.setdefault(c.ts_code, {
            "rows": [],
            "family_best": {},     # fam → (best_adj_score, winrate_of_that_setup)
            "any_active": False,
            "sector_quality": sector_quality_by_stock.get(c.ts_code),
        })
        rec["rows"].append((adj_score, c, status))
        fam = _family_of(c.setup_name)
        prev = rec["family_best"].get(fam)
        if prev is None or adj_score > prev[0]:
            rec["family_best"][fam] = (adj_score, overall_wr)
        if status == "active":
            rec["any_active"] = True

    EXTRA_FAMILY_WEIGHTS = [0.08, 0.05, 0.03]    # base weights for 2nd/3rd/4th
    for ts_code, rec in by_stock.items():
        family_records = sorted(rec["family_best"].values(),
                                key=lambda t: -t[0])
        if not family_records:
            rec["stock_score"] = 0.0
            rec["resonance_count"] = 0
            rec["resonance_families"] = ()
            rec["max_score"] = 0.0
            rec["families"] = set()
            continue
        primary_score, _ = family_records[0]
        bonus = 0.0
        for (score_i, wr_i), base_w in zip(family_records[1:], EXTRA_FAMILY_WEIGHTS):
            # Bayesian weighting: scale base weight by winrate evidence
            # wr=30% (target) → factor 1.0; wr=45% → 1.5; wr=15% → 0.5; floor at 0.4
            if wr_i is not None and wr_i > 0:
                wr_factor = max(0.4, min(1.5, wr_i / wr_target))
            else:
                wr_factor = 1.0
            bonus += score_i * base_w * wr_factor
        raw_stock_score = primary_score + bonus
        # M9.7 sector_quality multiplier — institutional discipline:
        # TA signal in poor-flow sector is fundamentally weaker
        sq = rec.get("sector_quality")
        if sq is not None:
            sector_factor = quality_min + quality_span * max(0.0, min(1.0, sq))
        else:
            # Cold-start (SmartMoney data missing): neutral midpoint
            sector_factor = (quality_min + quality_max) / 2.0
        rec["raw_stock_score"] = raw_stock_score
        rec["stock_score"] = raw_stock_score * sector_factor
        rec["sector_factor"] = sector_factor
        rec["resonance_count"] = len(rec["family_best"])
        rec["resonance_families"] = tuple(sorted(rec["family_best"].keys()))
        rec["max_score"] = primary_score
        rec["families"] = set(rec["family_best"].keys())

    # Pass 3: rank stocks by (stock_score, resonance_count) — multi-family wins ties
    sorted_stocks = sorted(
        by_stock.items(),
        key=lambda kv: (-kv[1]["stock_score"], -kv[1]["resonance_count"], kv[0]),
    )

    # Tier assignment: A top 10, B next 20, C next 100, drop rest
    # M10 P1.5 — Concentration cap (per L2 sector): keep portfolio diversified.
    tiers = rp.get("tiers", {})
    a_size = tiers.get("a_size", 10)
    b_size = tiers.get("b_size", 20)
    c_size = tiers.get("c_size", 100)
    conc = (rp_root := load_params()).get("concentration", {}) or {}
    cap_a_per_l2 = conc.get("tier_a_per_l2_max", 99) if conc.get("enabled", False) else 99
    cap_b_per_l2 = conc.get("tier_b_per_l2_max", 99) if conc.get("enabled", False) else 99
    cap_ab_per_l2 = conc.get("tier_ab_per_l2_max", 99) if conc.get("enabled", False) else 99

    # Build per-stock SW L2 lookup from candidate evidence (most reliable in-flight).
    l2_of_stock: dict[str, str | None] = {}
    for ts_code, rec in by_stock.items():
        for adj_score, c, status in rec["rows"]:
            ev = c.evidence if isinstance(c.evidence, dict) else {}
            l2 = ev.get("sw_l2_code") or ev.get("l2_code")
            if l2:
                l2_of_stock[ts_code] = l2
                break
        else:
            l2_of_stock[ts_code] = None

    tier_of: dict[str, str] = {}
    a_count_l2: dict[str, int] = {}
    b_count_l2: dict[str, int] = {}
    a_filled = b_filled = c_filled = 0
    for ts_code, rec in sorted_stocks:
        if not rec["any_active"]:
            continue
        l2 = l2_of_stock.get(ts_code) or "_unknown"
        a_n = a_count_l2.get(l2, 0)
        b_n = b_count_l2.get(l2, 0)
        ab_n = a_n + b_n
        # Try Tier A first, with caps
        if a_filled < a_size and a_n < cap_a_per_l2 and ab_n < cap_ab_per_l2:
            tier_of[ts_code] = "A"
            a_count_l2[l2] = a_n + 1
            a_filled += 1
        elif b_filled < b_size and b_n < cap_b_per_l2 and ab_n < cap_ab_per_l2:
            tier_of[ts_code] = "B"
            b_count_l2[l2] = b_n + 1
            b_filled += 1
        elif c_filled < c_size:
            tier_of[ts_code] = "C"
            c_filled += 1
        else:
            tier_of[ts_code] = ""    # dropped — won't be persisted
    top_stocks = {ts for ts, t in tier_of.items() if t == "A"}

    # Pass 4: emit RankedCandidate per row, inheriting stock-level fields
    stock_rank = {ts: i + 1 for i, (ts, _) in enumerate(sorted_stocks)}

    # Diversity cap on rows in top_watchlist (so audit table still gets variety)
    diversity_cap = rp["diversity"]["top_cap_per_setup"]
    per_setup_top_count: dict[str, int] = {}

    out: list[RankedCandidate] = []
    # Process rows ordered by stock rank, then by row score within stock
    rows_with_stock = []
    for ts_code, rec in by_stock.items():
        for adj_score, c, status in rec["rows"]:
            rows_with_stock.append((stock_rank[ts_code], -adj_score, c.setup_name,
                                    adj_score, c, status, rec))
    rows_with_stock.sort(key=lambda t: (t[0], t[1], t[2]))

    for rank_pos, _negs, _setup, adj_score, c, status, rec in rows_with_stock:
        stock_score = rec["stock_score"]
        tier = tier_of.get(c.ts_code, "")
        if not tier:
            continue   # drop — only persist Tier A/B/C
        in_top = (
            tier == "A"
            and status == "active"
            and per_setup_top_count.get(c.setup_name, 0) < diversity_cap
        )
        if in_top:
            per_setup_top_count[c.setup_name] = per_setup_top_count.get(c.setup_name, 0) + 1
        out.append(RankedCandidate(
            candidate=c,
            rank=rank_pos,
            star_rating=_stars(stock_score),
            in_top_watchlist=in_top,
            governance_status=status,
            stock_score=stock_score,
            raw_stock_score=rec.get("raw_stock_score", stock_score),
            sector_factor=rec.get("sector_factor", 1.0),
            sector_role=sector_role_by_stock.get(c.ts_code),
            sector_cycle_phase=sector_phase_by_stock.get(c.ts_code),
            resonance_count=rec["resonance_count"],
            resonance_families=rec["resonance_families"],
            tier=tier,
        ))
    return out
