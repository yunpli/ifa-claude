"""Tier A / Tier B portfolio performance — the post-aggregation truth.

Per-setup edges (in metrics_v2 / runner.py) measure what each setup contributes
in isolation. But the user-visible product is Tier A (top 10) and Tier B
(next 20) — the result of Bayesian resonance + regime_winrates + sector_factor
+ concentration cap. Tier-level performance IS the system's deliverable alpha.

For each generation_date:
  · For each candidate marked Tier A or B (one row per ts_code, dedup'd from
    multiple-setup hits via DISTINCT ON), join its position event row.
  · Compute equal-weight portfolio mean of T+5/T+10/T+15 returns.
  · Compute combined per ta_v2.3.yaml.backtest_objective.weights.

Aggregate over a window:
  · daily portfolio returns time series (mean + std)
  · win/loss/expectancy ratios at each horizon
  · combined score weighted across days
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.ta.params import load_params


@dataclass
class TierPerf:
    tier: str                    # 'A' or 'B'
    n_days: int
    n_positions: int             # total picks across the window
    n_filled: int                # filled (entry挂单成交)
    fill_rate: float             # filled / total picks
    # Position-state-machine outcomes (out of filled):
    n_target_hit: int            # 达到目标价 — user's "成功" definition
    n_stop_hit: int              # 止损
    n_time_exit: int             # T+15 收盘平仓
    n_still_holding: int         # 数据未足 (T+15 forward not yet realized)
    success_rate: float          # n_target_hit / n_filled  (user-defined "成功率")
    # Aggregate close-based returns:
    avg_t5: float
    avg_t10: float
    avg_t15: float
    wr_t5: float                 # % positions with T+5 return >= 3.0
    wr_t10: float                # % >= 4.0
    wr_t15: float                # % >= 5.0
    # Realized (state-machine) return — uses fill_price → exit_price:
    avg_realized_return: float   # mean of realized_return_pct (only filled+exited)
    avg_max_dd: float
    combined: float              # weighted-objective score
    daily_mean_t15: list[tuple]  # (date, mean_t15) — for plotting later


def analyze_tier_perf(
    engine: Engine,
    *,
    start: date,
    end: date,
    tier: str = "A",
) -> TierPerf:
    """Compute portfolio-level T+5/T+10/T+15 performance for Tier A or B."""
    p = load_params().get("backtest_objective", {}).get("weights", {})
    w_t15 = float(p.get("t15", 0.7))
    w_t5 = float(p.get("t5", 0.2))
    w_t10 = float(p.get("t10", 0.1))

    # One row per (trade_date, ts_code) — dedup multi-setup hits.
    sql = text("""
        WITH picks AS (
            SELECT DISTINCT ON (c.trade_date, c.ts_code)
                   c.trade_date, c.ts_code, c.candidate_id
            FROM ta.candidates_daily c
            WHERE c.trade_date >= :s AND c.trade_date <= :e
              AND c.evidence_json->>'tier' = :tier
            ORDER BY c.trade_date, c.ts_code, c.final_score DESC
        )
        SELECT p.trade_date, p.ts_code,
               pe.fill_status, pe.exit_status,
               pe.return_t5_pct, pe.return_t10_pct, pe.return_t15_pct,
               pe.realized_return_pct, pe.max_drawdown_pct
        FROM picks p
        JOIN ta.position_events_daily pe ON pe.candidate_id = p.candidate_id
    """)
    daily_t15: dict[date, list[float]] = {}
    n_total = 0
    n_filled = 0
    n_target = n_stop = n_time = n_holding = 0
    rt5: list[float] = []
    rt10: list[float] = []
    rt15: list[float] = []
    realized: list[float] = []
    dds: list[float] = []
    with engine.connect() as conn:
        for r in conn.execute(sql, {"s": start, "e": end, "tier": tier}):
            n_total += 1
            if r[2] != "filled":
                continue
            n_filled += 1
            exit_status = r[3]
            if exit_status == "target_hit":
                n_target += 1
            elif exit_status == "stop_hit":
                n_stop += 1
            elif exit_status == "time_exit":
                n_time += 1
            elif exit_status == "still_holding":
                n_holding += 1
            if r[4] is not None:
                rt5.append(float(r[4]))
            if r[5] is not None:
                rt10.append(float(r[5]))
            if r[6] is not None:
                rt15.append(float(r[6]))
                daily_t15.setdefault(r[0], []).append(float(r[6]))
            if r[7] is not None:
                realized.append(float(r[7]))
            if r[8] is not None:
                dds.append(float(r[8]))

    n_days = len(daily_t15)
    avg_t5 = sum(rt5) / len(rt5) if rt5 else 0.0
    avg_t10 = sum(rt10) / len(rt10) if rt10 else 0.0
    avg_t15 = sum(rt15) / len(rt15) if rt15 else 0.0
    wr_t5 = sum(1 for r in rt5 if r >= 3.0) / len(rt5) * 100 if rt5 else 0.0
    wr_t10 = sum(1 for r in rt10 if r >= 4.0) / len(rt10) * 100 if rt10 else 0.0
    wr_t15 = sum(1 for r in rt15 if r >= 5.0) / len(rt15) * 100 if rt15 else 0.0
    avg_dd = sum(dds) / len(dds) if dds else 0.0

    combined = (
        w_t15 * (wr_t15 / 100.0) * avg_t15
        + w_t5 * (wr_t5 / 100.0) * avg_t5
        + w_t10 * (wr_t10 / 100.0) * avg_t10
    )

    daily_means = sorted(
        ((d, sum(vs) / len(vs)) for d, vs in daily_t15.items()),
        key=lambda kv: kv[0],
    )

    avg_realized = sum(realized) / len(realized) if realized else 0.0

    return TierPerf(
        tier=tier,
        n_days=n_days,
        n_positions=n_total,
        n_filled=n_filled,
        fill_rate=n_filled / n_total if n_total else 0.0,
        n_target_hit=n_target,
        n_stop_hit=n_stop,
        n_time_exit=n_time,
        n_still_holding=n_holding,
        success_rate=n_target / max(n_filled, 1),
        avg_t5=avg_t5,
        avg_t10=avg_t10,
        avg_t15=avg_t15,
        wr_t5=wr_t5,
        wr_t10=wr_t10,
        wr_t15=wr_t15,
        avg_realized_return=avg_realized,
        avg_max_dd=avg_dd,
        combined=combined,
        daily_mean_t15=daily_means,
    )
