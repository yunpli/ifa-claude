"""M2.3b · Peer percentile rank within SW L2.

Given a factor (computed for the target stock) and the target's SW L2 code,
this module computes a percentile rank against the same-L2 universe.

Design choices:
  · **Universe = SW L2 PIT membership** at `data_cutoff_date` (read from
    `smartmoney.sw_member_monthly`). L1 fallback if L2 sample < `min_peer_count`.
  · **Storage**: peer factor values live in `research.factor_value` (one row
    per (ts_code, factor_name, period)). The peer scan does NOT recompute —
    it reads what was already persisted by upstream loaders.
  · **Direction-aware percentile**: for `higher_better` factors, percentile=100
    means "best". For `lower_better`, percentile=100 still means best (we flip
    internally so the score is always "higher = better").
  · **None propagation**: missing target value → peer_percentile=None.
  · **Sample size**: if the L2 has fewer than `min_peer_count` observations,
    return None and add a `notes` entry instead of producing a noisy rank.

Public API:
  · `compute_peer_rank(engine, ts_code, factor_name, value, sw_l2_code,
                       direction, period, *, min_peer_count=8)`
      → PeerRankResult(rank, total, percentile_0_100)
  · `attach_peer_ranks(engine, results, snapshot, *, min_peer_count=8)`
      → mutates each FactorResult to fill `peer_rank` + `peer_percentile`
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.research.analyzer.data import CompanyFinancialSnapshot
from ifa.families.research.analyzer.factors import FactorResult

log = logging.getLogger(__name__)


@dataclass
class PeerRankResult:
    rank: int                  # 1 = best, total = worst (already direction-flipped)
    total: int                 # number of peers including self
    percentile_0_100: float    # 100 = best, 0 = worst
    universe: str              # 'sw_l2' | 'sw_l1' (which scope was used)


def compute_peer_rank(
    engine: Engine,
    *,
    ts_code: str,
    factor_name: str,
    value: float | None,
    sw_l2_code: str | None,
    sw_l1_code: str | None,
    direction: str,
    period: str | None,
    on_date: date,
    min_peer_count: int = 8,
) -> PeerRankResult | None:
    """Return PeerRankResult or None if data insufficient."""
    if value is None:
        return None

    # Try L2 first, fall back to L1 if too sparse
    for scope, code, col in (
        ("sw_l2", sw_l2_code, "l2_code"),
        ("sw_l1", sw_l1_code, "l1_code"),
    ):
        if code is None:
            continue
        peers = _fetch_peer_values(engine, factor_name, code, col, period, on_date)
        if len(peers) >= min_peer_count:
            return _rank_against_peers(
                ts_code=ts_code, value=float(value), peers=peers,
                direction=direction, universe=scope,
            )

    return None


def attach_peer_ranks(
    engine: Engine,
    results: list[FactorResult],
    snapshot: CompanyFinancialSnapshot,
    *,
    min_peer_count: int = 8,
) -> None:
    """Mutate each FactorResult in-place: fill peer_rank + peer_percentile.

    Skips factors where:
      · spec.industry_sensitive is False (peer rank not meaningful)
      · value is None
      · neither L2 nor L1 has enough peers
    """
    ts_code = snapshot.company.ts_code
    sw_l2 = snapshot.sw_l2_code
    sw_l1 = snapshot.sw_l1_code
    on_date = snapshot.data_cutoff_date

    for r in results:
        if not r.spec.industry_sensitive or r.value is None:
            continue
        try:
            v = float(r.value)
        except (TypeError, ValueError):
            continue

        rank = compute_peer_rank(
            engine,
            ts_code=ts_code,
            factor_name=r.spec.name,
            value=v,
            sw_l2_code=sw_l2,
            sw_l1_code=sw_l1,
            direction=r.spec.direction,
            period=r.period,
            on_date=on_date,
            min_peer_count=min_peer_count,
        )
        if rank is None:
            continue

        r.peer_rank = (rank.rank, rank.total)
        r.peer_percentile = rank.percentile_0_100
        if rank.universe == "sw_l1":
            r.notes.append(f"peer scope: SW L1 ({rank.total} 同业，L2 样本不足)")


# ─── Internals ────────────────────────────────────────────────────────────────

def _fetch_peer_values(
    engine: Engine,
    factor_name: str,
    sw_code: str,
    sw_col: str,
    period: str | None,
    on_date: date,
) -> list[tuple[str, float]]:
    """Read (ts_code, value) pairs for all peers in the given SW scope.

    Joins research.factor_value × smartmoney.sw_member_monthly on the snapshot
    month corresponding to `on_date`.
    """
    snapshot_month = on_date.replace(day=1)
    # Build SQL conditionally to avoid `:period IS NULL` (Postgres can't infer
    # the parameter type without a CAST under prepared statements).
    period_clause = "AND fv.period = :period" if period is not None else ""
    sql = text(f"""
        SELECT fv.ts_code, fv.value
        FROM research.factor_value fv
        JOIN smartmoney.sw_member_monthly sm
          ON fv.ts_code = sm.ts_code
         AND sm.snapshot_month = :sm
         AND sm.{sw_col} = :code
        WHERE fv.factor_name = :fname
          {period_clause}
          AND fv.value IS NOT NULL
    """)
    params: dict = {"sm": snapshot_month, "code": sw_code, "fname": factor_name}
    if period is not None:
        params["period"] = period
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        # Don't swallow silently — log at WARNING so bugs surface in dev/prod.
        log.warning("peer scan SQL failed for %s/%s: %s", factor_name, sw_code, e)
        return []
    return [(str(tc), float(v)) for tc, v in rows]


def _rank_against_peers(
    *,
    ts_code: str,
    value: float,
    peers: list[tuple[str, float]],
    direction: str,
    universe: str,
) -> PeerRankResult:
    """Compute rank+percentile. Higher percentile always means 'better' for the factor."""
    # Make sure the target is included exactly once (dedupe by ts_code).
    pool: dict[str, float] = {tc: v for tc, v in peers}
    pool[ts_code] = value

    # Sort so that index 0 is the *best* given the direction.
    if direction == "higher_better":
        sorted_codes = sorted(pool, key=lambda k: pool[k], reverse=True)
    elif direction == "lower_better":
        sorted_codes = sorted(pool, key=lambda k: pool[k])
    elif direction == "in_band":
        # For in-band, "better" = closer to the band center. We approximate by
        # ranking by absolute deviation from the *median* — a robust proxy for
        # the healthy band when explicit band edges aren't passed in.
        median = sorted(pool.values())[len(pool) // 2]
        sorted_codes = sorted(pool, key=lambda k: abs(pool[k] - median))
    else:
        sorted_codes = sorted(pool, key=lambda k: pool[k], reverse=True)

    rank = sorted_codes.index(ts_code) + 1   # 1-based
    total = len(sorted_codes)
    # Percentile: 100 = best, 0 = worst. Use (total - rank) / (total - 1) when total > 1.
    if total <= 1:
        percentile = 50.0
    else:
        percentile = (total - rank) / (total - 1) * 100

    return PeerRankResult(
        rank=rank, total=total,
        percentile_0_100=percentile,
        universe=universe,
    )
