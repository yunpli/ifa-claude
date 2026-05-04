"""Compute ta.sector_phase_metrics_daily — data-derived phase scores.

For each cycle_phase observed in `smartmoney.sector_state_daily` over the
60-day window before `on_date`, compute the realized 15-trade-day forward
return of stocks whose sector was in that phase. Average → derived_score.

This replaces hardcoded phase→score maps. Scores adapt as market behaves.

API:
    compute_sector_phase_metrics(engine, on_date) → number of rows written
    load_phase_scores(engine, on_date) → dict[phase, derived_score]
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.calendar import trading_days_between

log = logging.getLogger(__name__)

# Phases per migration 289066f22cc2 + role aggregation
CYCLE_PHASES = ("冷", "点火", "确认", "扩散", "高潮", "分歧", "退潮")


def compute_sector_phase_metrics(engine: Engine, on_date: date) -> int:
    """Compute and upsert per-phase rolling 60d T+15 metrics for `on_date`."""
    cal_lookback = on_date - timedelta(days=120)   # ~80 trade days, more than 60 + 15 lead
    days = trading_days_between(engine, cal_lookback, on_date)
    if len(days) < 30:
        log.warning("not enough trade days (%d) for sector_phase_metrics %s",
                    len(days), on_date)
        return 0

    # Window: phases observed in the last 60 trade days, where T+15 has settled
    # i.e., observation_date <= on_date - 15 trade days
    if len(days) < 76:
        cutoff_idx = max(0, len(days) - 60)
        window_start = days[cutoff_idx]
        # T+15 must settle by on_date — observation must be ≤ days[-15]
        latest_observable = days[-16] if len(days) >= 16 else days[0]
    else:
        window_start = days[-75]
        latest_observable = days[-16]

    sql = text("""
        WITH phase_obs AS (
            SELECT
                s.cycle_phase,
                s.trade_date AS pick_date,
                m.ts_code
            FROM smartmoney.sector_state_daily s
            JOIN smartmoney.sw_member_monthly m
              ON m.l2_code = s.sector_code
             AND m.snapshot_month = date_trunc('month', s.trade_date)::date
            WHERE s.sector_source = 'sw_l2'
              AND s.trade_date BETWEEN :w_start AND :w_end
              AND s.cycle_phase IS NOT NULL
        ),
        forward_close AS (
            SELECT po.cycle_phase,
                   po.pick_date,
                   po.ts_code,
                   d0.close AS entry_close,
                   future.close AS future_close,
                   future.trade_date AS future_date,
                   ROW_NUMBER() OVER (PARTITION BY po.ts_code, po.pick_date
                                      ORDER BY future.trade_date) AS day_idx
            FROM phase_obs po
            JOIN smartmoney.raw_daily d0
              ON d0.ts_code = po.ts_code AND d0.trade_date = po.pick_date
            JOIN smartmoney.raw_daily future
              ON future.ts_code = po.ts_code
             AND future.trade_date > po.pick_date
        ),
        t15_returns AS (
            SELECT cycle_phase, ts_code, pick_date,
                   (future_close / NULLIF(entry_close, 0) - 1) * 100 AS ret_t15
            FROM forward_close
            WHERE day_idx = 15
              AND entry_close IS NOT NULL AND future_close IS NOT NULL
        )
        SELECT cycle_phase,
               COUNT(*) AS n,
               AVG(ret_t15)::numeric AS avg_ret,
               (100.0 * COUNT(*) FILTER (WHERE ret_t15 > 0) / COUNT(*))::numeric AS win_rate
        FROM t15_returns
        GROUP BY cycle_phase
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "w_start": window_start,
            "w_end": latest_observable,
        }).fetchall()

    if not rows:
        log.info("no phase observations for %s — skipping", on_date)
        return 0

    # SQL columns: phase=r[0], n=r[1] (int), avg_ret=r[2] (numeric), win_rate=r[3] (numeric)
    rec_list: list[tuple[str, int, float, float | None]] = []
    for r in rows:
        if r[2] is None:    # avg_ret missing → skip
            continue
        rec_list.append((r[0], int(r[1]), float(r[2]),
                         float(r[3]) if r[3] is not None else None))
    if not rec_list:
        return 0

    only_rets = [rec[2] for rec in rec_list]
    lo, hi = min(only_rets), max(only_rets)
    span = (hi - lo) or 1.0

    sql_upsert = text("""
        INSERT INTO ta.sector_phase_metrics_daily
            (trade_date, cycle_phase, n_observations, avg_t15_return_pct,
             win_rate_t15_pct, derived_score)
        VALUES
            (:d, :phase, :n, :avg_ret, :wr, :score)
        ON CONFLICT (trade_date, cycle_phase) DO UPDATE SET
            n_observations = EXCLUDED.n_observations,
            avg_t15_return_pct = EXCLUDED.avg_t15_return_pct,
            win_rate_t15_pct = EXCLUDED.win_rate_t15_pct,
            derived_score = EXCLUDED.derived_score
    """)

    n_written = 0
    with engine.begin() as conn:
        for phase, n_obs, avg_ret, wr in rec_list:
            derived = (avg_ret - lo) / span
            conn.execute(sql_upsert, {
                "d": on_date, "phase": phase, "n": n_obs,
                "avg_ret": avg_ret, "wr": wr, "score": round(derived, 4),
            })
            n_written += 1
    log.info("sector_phase_metrics %s: %d phases, score range [%.2f, %.2f]",
             on_date, n_written, lo, hi)
    return n_written


def load_phase_scores(engine: Engine, on_date: date) -> dict[str, float]:
    """Load per-phase derived_score for `on_date` (or most recent ≤ date).

    Returns empty dict on cold-start; ranker falls back to flat 0.5 in that case.
    """
    sql = text("""
        SELECT cycle_phase, derived_score
        FROM ta.sector_phase_metrics_daily
        WHERE trade_date = (
            SELECT MAX(trade_date) FROM ta.sector_phase_metrics_daily
            WHERE trade_date <= :d
        )
    """)
    with engine.connect() as conn:
        return {r[0]: float(r[1]) for r in conn.execute(sql, {"d": on_date})
                if r[1] is not None}
