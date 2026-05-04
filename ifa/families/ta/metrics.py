"""Compute ta.setup_metrics_daily — rolling 60d/250d edge metrics per setup.

For each (trade_date=on_date, setup_name), aggregate over candidates whose
trade_date falls in the prior 60 (or 250) trade days AND whose T+10 tracking
row exists:

  · triggers_count   — # of candidates in the 60d window
  · winrate_60d      — % confirmed (return_pct >= 5%) at h=10 in 60d window
  · avg_return_60d   — mean return_pct at h=10 in 60d window
  · pl_ratio_60d     — avg gain (confirmed) / |avg loss (invalidated)| in 60d
  · winrate_250d     — same definition over 250d
  · decay_score      — winrate_60d - winrate_250d  (positive = improving)
  · suitable_regimes — ARRAY of regime names where this setup's confirmed
                       rate exceeds its overall confirmed rate (in 250d)

Window endpoints come from `ifa.core.calendar` (smartmoney.trade_cal).
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.calendar import trading_days_between

log = logging.getLogger(__name__)


def compute_setup_metrics(engine: Engine, on_date: date) -> int:
    """Compute and upsert one row per setup for `on_date`. Returns row count."""
    # Resolve 60d / 250d window starts using the trade calendar.
    from datetime import timedelta
    cal_start = on_date - timedelta(days=400)        # 250 trade ≈ ~360 calendar
    all_days = trading_days_between(engine, cal_start, on_date)
    if len(all_days) < 1:
        log.warning("no trade days in lookback for %s", on_date)
        return 0
    window_60_start = all_days[-min(60, len(all_days))]
    window_250_start = all_days[-min(250, len(all_days))]

    sql = text("""
        WITH cand_60 AS (
            SELECT c.candidate_id, c.setup_name, c.regime_at_gen, t.return_pct
            FROM ta.candidates_daily c
            JOIN ta.candidate_tracking t
              ON t.candidate_id = c.candidate_id AND t.horizon_days = 10
            WHERE c.trade_date >= :w60 AND c.trade_date <= :on_date
        ),
        cand_250 AS (
            SELECT c.candidate_id, c.setup_name, c.regime_at_gen, t.return_pct
            FROM ta.candidates_daily c
            JOIN ta.candidate_tracking t
              ON t.candidate_id = c.candidate_id AND t.horizon_days = 10
            WHERE c.trade_date >= :w250 AND c.trade_date <= :on_date
        ),
        agg_60 AS (
            SELECT setup_name,
                   COUNT(*) AS n,
                   AVG(return_pct) AS avg_ret,
                   100.0 * COUNT(*) FILTER (WHERE return_pct >= 5.0) / NULLIF(COUNT(*), 0) AS winrate,
                   AVG(return_pct) FILTER (WHERE return_pct >= 5.0) AS avg_gain,
                   AVG(return_pct) FILTER (WHERE return_pct <= -3.0) AS avg_loss
            FROM cand_60 GROUP BY setup_name
        ),
        agg_250 AS (
            SELECT setup_name,
                   100.0 * COUNT(*) FILTER (WHERE return_pct >= 5.0) / NULLIF(COUNT(*), 0) AS winrate
            FROM cand_250 GROUP BY setup_name
        )
        SELECT a.setup_name,
               a.n, a.avg_ret, a.winrate,
               CASE WHEN a.avg_loss IS NULL OR a.avg_loss = 0 THEN NULL
                    ELSE a.avg_gain / ABS(a.avg_loss) END AS pl_ratio,
               b.winrate AS winrate_250
        FROM agg_60 a LEFT JOIN agg_250 b USING (setup_name)
    """)

    sql_regime = text("""
        WITH t AS (
            SELECT c.setup_name, c.regime_at_gen,
                   100.0 * COUNT(*) FILTER (WHERE t.return_pct >= 5.0) / NULLIF(COUNT(*), 0) AS wr
            FROM ta.candidates_daily c
            JOIN ta.candidate_tracking t
              ON t.candidate_id = c.candidate_id AND t.horizon_days = 10
            WHERE c.trade_date >= :w250 AND c.trade_date <= :on_date
              AND c.regime_at_gen IS NOT NULL
            GROUP BY c.setup_name, c.regime_at_gen
        ),
        baseline AS (
            SELECT setup_name,
                   100.0 * COUNT(*) FILTER (WHERE return_pct >= 5.0) / NULLIF(COUNT(*), 0) AS wr_all
            FROM ta.candidates_daily c
            JOIN ta.candidate_tracking tk
              ON tk.candidate_id = c.candidate_id AND tk.horizon_days = 10
            WHERE c.trade_date >= :w250 AND c.trade_date <= :on_date
            GROUP BY setup_name
        )
        SELECT t.setup_name, ARRAY_AGG(t.regime_at_gen ORDER BY t.wr DESC) AS regimes
        FROM t JOIN baseline b USING (setup_name)
        WHERE t.wr > b.wr_all
        GROUP BY t.setup_name
    """)

    sql_upsert = text("""
        INSERT INTO ta.setup_metrics_daily
            (trade_date, setup_name, triggers_count, winrate_60d, avg_return_60d,
             pl_ratio_60d, winrate_250d, decay_score, suitable_regimes)
        VALUES
            (:trade_date, :setup_name, :n, :wr60, :avg_ret, :plr,
             :wr250, :decay, :regimes)
        ON CONFLICT (trade_date, setup_name) DO UPDATE SET
            triggers_count = EXCLUDED.triggers_count,
            winrate_60d = EXCLUDED.winrate_60d,
            avg_return_60d = EXCLUDED.avg_return_60d,
            pl_ratio_60d = EXCLUDED.pl_ratio_60d,
            winrate_250d = EXCLUDED.winrate_250d,
            decay_score = EXCLUDED.decay_score,
            suitable_regimes = EXCLUDED.suitable_regimes
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql, {"w60": window_60_start, "w250": window_250_start,
                                   "on_date": on_date}).fetchall()
        regime_rows = {r[0]: list(r[1])
                       for r in conn.execute(sql_regime,
                                             {"w250": window_250_start, "on_date": on_date})}

    n_written = 0
    with engine.begin() as conn:
        for setup, n, avg_ret, wr60, plr, wr250 in rows:
            decay = (float(wr60) - float(wr250)) if (wr60 is not None and wr250 is not None) else None
            conn.execute(sql_upsert, {
                "trade_date": on_date,
                "setup_name": setup,
                "n": int(n) if n is not None else None,
                "wr60": float(wr60) if wr60 is not None else None,
                "avg_ret": float(avg_ret) if avg_ret is not None else None,
                "plr": float(plr) if plr is not None else None,
                "wr250": float(wr250) if wr250 is not None else None,
                "decay": decay,
                "regimes": regime_rows.get(setup, []),
            })
            n_written += 1
    log.info("setup_metrics_daily: %d rows for %s", n_written, on_date)
    return n_written
