"""Walk-forward backtest runner — independent of report generation.

Produces per-setup objective scores from real position outcomes:
  combined = w_t15 × wr_t15 × avg_ret_t15
           + w_t5  × wr_t5  × avg_ret_t5
           + w_t10 × wr_t10 × avg_ret_t10

Two entry points:
  · backtest_window(start, end)        — single window: scan + track + agg
  · walk_forward(end_date, n_rolls)    — IS=90/OOS=30 rolling × n windows

Usage (CLI later wires this):
  result = backtest_window(eng, date(2025,12,1), date(2026,2,28))
  for setup, m in result.metrics.items():
      print(setup, m['combined'], m['n'])

Idempotent — uses upsert_candidates / upsert_position_events pattern.
Re-running on the same window overwrites prior rows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.calendar import trading_days_between
from ifa.families.ta.params import load_params
from ifa.families.ta.setups.context_loader import build_contexts
from ifa.families.ta.setups.position_tracker import (
    DEFAULT_HORIZON_TRADE_DAYS,
    evaluate_for_date,
)
from ifa.families.ta.setups.ranker import rank
from ifa.families.ta.setups.repo import upsert_candidates, upsert_warnings
from ifa.families.ta.setups.scanner import scan

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    start: date
    end: date
    n_days_processed: int
    n_candidates_total: int
    n_positions_total: int
    metrics: dict[str, dict]      # setup_name → {n, wr_t15, avg_t15, ..., combined}
    by_window: list[dict] = field(default_factory=list)  # walk-forward per-roll detail


def _aggregate_window_metrics(engine: Engine, start: date, end: date) -> dict[str, dict]:
    """Aggregate position_events over a window into per-setup metrics."""
    p = load_params()
    w = p.get("backtest_objective", {}).get("weights", {})
    w_t15 = float(w.get("t15", 0.7))
    w_t5 = float(w.get("t5", 0.2))
    w_t10 = float(w.get("t10", 0.1))

    sql = text("""
        SELECT c.setup_name,
               COUNT(*) AS n,
               AVG(p.return_t15_pct) AS avg_t15,
               AVG(p.return_t5_pct)  AS avg_t5,
               AVG(p.return_t10_pct) AS avg_t10,
               100.0 * COUNT(*) FILTER (WHERE p.return_t15_pct >= 5.0) / NULLIF(COUNT(*),0) AS wr_t15,
               100.0 * COUNT(*) FILTER (WHERE p.return_t5_pct  >= 3.0) / NULLIF(COUNT(*),0) AS wr_t5,
               100.0 * COUNT(*) FILTER (WHERE p.return_t10_pct >= 4.0) / NULLIF(COUNT(*),0) AS wr_t10,
               AVG(p.max_drawdown_pct) AS avg_dd
        FROM ta.candidates_daily c
        JOIN ta.position_events_daily p ON p.candidate_id = c.candidate_id
        WHERE c.trade_date >= :s AND c.trade_date <= :e
          AND p.fill_status = 'filled'
        GROUP BY c.setup_name
    """)
    out: dict[str, dict] = {}
    with engine.connect() as conn:
        for r in conn.execute(sql, {"s": start, "e": end}):
            (setup_name, n, avg_t15, avg_t5, avg_t10,
             wr_t15, wr_t5, wr_t10, avg_dd) = r
            wr_t15_f = float(wr_t15) if wr_t15 is not None else 0.0
            wr_t5_f = float(wr_t5) if wr_t5 is not None else 0.0
            wr_t10_f = float(wr_t10) if wr_t10 is not None else 0.0
            avg_t15_f = float(avg_t15) if avg_t15 is not None else 0.0
            avg_t5_f = float(avg_t5) if avg_t5 is not None else 0.0
            avg_t10_f = float(avg_t10) if avg_t10 is not None else 0.0
            combined = (
                w_t15 * (wr_t15_f / 100.0) * avg_t15_f
                + w_t5 * (wr_t5_f / 100.0) * avg_t5_f
                + w_t10 * (wr_t10_f / 100.0) * avg_t10_f
            )
            out[setup_name] = {
                "n": int(n),
                "wr_t15": round(wr_t15_f, 2),
                "wr_t5": round(wr_t5_f, 2),
                "wr_t10": round(wr_t10_f, 2),
                "avg_ret_t15": round(avg_t15_f, 4),
                "avg_ret_t5": round(avg_t5_f, 4),
                "avg_ret_t10": round(avg_t10_f, 4),
                "avg_max_dd": float(avg_dd) if avg_dd is not None else None,
                "combined": round(combined, 4),
            }
    return out


def _scan_and_persist_one_day(engine: Engine, on_date: date) -> int:
    """Build contexts → scan → rank → upsert candidates+warnings for one day.
    Returns rows of long candidates persisted. Idempotent.
    """
    with engine.connect() as conn:
        regime = conn.execute(
            text("SELECT regime FROM ta.regime_daily WHERE trade_date = :d"),
            {"d": on_date},
        ).scalar()
        latest = conn.execute(
            text("SELECT MAX(trade_date) FROM ta.setup_metrics_daily WHERE trade_date < :d"),
            {"d": on_date},
        ).scalar()
        setup_metrics: dict = {}
        if latest:
            for r in conn.execute(text("""
                SELECT setup_name, decay_score, suitable_regimes,
                       winrate_60d, regime_winrates
                FROM ta.setup_metrics_daily WHERE trade_date = :d
            """), {"d": latest}):
                setup_metrics[r[0]] = {
                    "decay_score": float(r[1]) if r[1] else None,
                    "suitable_regimes": list(r[2]) if r[2] else [],
                    "winrate_60d": float(r[3]) if r[3] else None,
                    "regime_winrates": r[4] if isinstance(r[4], dict) else {},
                }
    ctxs = build_contexts(engine, on_date, regime=regime)
    if not ctxs:
        return 0
    long_c, warn_c = scan(ctxs.values())
    ranked = rank(long_c, top_n=200,
                  current_regime=regime, setup_metrics=setup_metrics)
    n = upsert_candidates(engine, on_date, ranked, regime_at_gen=regime)
    if warn_c:
        upsert_warnings(engine, on_date, warn_c, regime_at_gen=regime)
    return n


def backtest_window(
    engine: Engine, start: date, end: date,
    *, horizon: int = DEFAULT_HORIZON_TRADE_DAYS,
    skip_scan: bool = False,
) -> BacktestResult:
    """Run scan + position tracking for every trade day in [start, end];
    return per-setup metrics over the window.

    skip_scan=True: assume candidates_daily + position_events already populated
    for the window; just aggregate. Useful for re-running aggregation with
    different objective weights without re-scanning.
    """
    days = trading_days_between(engine, start, end)
    n_cands = 0
    n_pos = 0
    if not skip_scan:
        for d in days:
            n_cands += _scan_and_persist_one_day(engine, d)
            n_pos += evaluate_for_date(engine, d, horizon=horizon,
                                        top_watchlist_only=False)
            log.info("backtest day %s: cands=%d pos=%d cumulative", d, n_cands, n_pos)
    metrics = _aggregate_window_metrics(engine, start, end)
    return BacktestResult(
        start=start, end=end,
        n_days_processed=len(days),
        n_candidates_total=n_cands,
        n_positions_total=n_pos,
        metrics=metrics,
    )


def walk_forward(
    engine: Engine, end_date: date,
    *, is_days: int | None = None,
    oos_days: int | None = None,
    n_rolls: int | None = None,
) -> BacktestResult:
    """Run IS / OOS walk-forward analysis ending at end_date.

    Defaults from ta_v2.3.yaml.backtest_objective.walk_forward:
      is_days  = 90 (in-sample window)
      oos_days = 30 (out-of-sample validation)
      n_rolls  = 12 (≈ 1 year)

    Returns aggregate over ALL OOS windows (the honest performance number).
    `by_window` list contains per-roll metrics for OOS-stability inspection.
    """
    p = load_params().get("backtest_objective", {}).get("walk_forward", {})
    is_days = is_days or p.get("is_trade_days", 90)
    oos_days = oos_days or p.get("oos_trade_days", 30)
    n_rolls = n_rolls or p.get("n_rolls", 12)

    # Build one big trade-day list ending at end_date and walk back.
    big_window = trading_days_between(
        engine,
        end_date.replace(day=1).replace(year=end_date.year - 2),  # rough lower bound
        end_date,
    )
    if len(big_window) < (is_days + oos_days * n_rolls):
        log.warning("walk_forward needs %d trade days; only %d available",
                    is_days + oos_days * n_rolls, len(big_window))
        n_rolls = max(1, (len(big_window) - is_days) // oos_days)

    by_window: list[dict] = []
    for i in range(n_rolls):
        oos_end_idx = len(big_window) - i * oos_days
        oos_start_idx = max(0, oos_end_idx - oos_days)
        if oos_start_idx == 0:
            break
        oos_start = big_window[oos_start_idx]
        oos_end = big_window[oos_end_idx - 1]
        m = _aggregate_window_metrics(engine, oos_start, oos_end)
        by_window.append({
            "roll": i,
            "oos_start": oos_start.isoformat(),
            "oos_end": oos_end.isoformat(),
            "n_setups": len(m),
            "metrics": m,
        })
        log.info("walk-fwd roll %d: OOS [%s..%s] %d setups", i, oos_start, oos_end, len(m))

    # Aggregate across all OOS windows.
    setup_acc: dict[str, dict] = {}
    for w in by_window:
        for setup, m in w["metrics"].items():
            acc = setup_acc.setdefault(setup, {"n": 0, "sum_combined": 0.0,
                                                "sum_t15_n": 0, "sum_t15_wins": 0})
            acc["n"] += m["n"]
            acc["sum_combined"] += m["combined"] * m["n"]
            acc["sum_t15_wins"] += m["wr_t15"] * m["n"] / 100.0
    final_metrics: dict[str, dict] = {}
    for setup, acc in setup_acc.items():
        if acc["n"] == 0:
            continue
        final_metrics[setup] = {
            "n": acc["n"],
            "combined": round(acc["sum_combined"] / acc["n"], 4),
            "wr_t15": round(acc["sum_t15_wins"] / acc["n"] * 100.0, 2),
            "n_rolls_seen": sum(1 for w in by_window if setup in w["metrics"]),
        }

    return BacktestResult(
        start=by_window[-1]["oos_start"] if by_window else end_date,
        end=by_window[0]["oos_end"] if by_window else end_date,
        n_days_processed=sum(oos_days for _ in by_window),
        n_candidates_total=0,
        n_positions_total=0,
        metrics=final_metrics,
        by_window=by_window,
    )
