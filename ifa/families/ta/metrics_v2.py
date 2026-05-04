"""TA setup metrics — v2.3 forward-return ETL.

Sources data from `ta.position_events_daily` (filled-only, real exits) instead
of legacy `ta.candidate_tracking` (close-to-close, no fill check).

Objective (per ta_v2.3.yaml.backtest_objective.weights):
    combined_score = 0.7 × T+15 win-rate × avg-ret
                   + 0.2 × T+5  win-rate × avg-ret
                   + 0.1 × T+10 win-rate × avg-ret

Outputs to `ta.setup_metrics_daily`:
  · winrate_60d (T+15 only — main objective)
  · avg_return_60d (T+15)
  · combined_score_60d (weighted)
  · pl_ratio_60d, decay_score
  · suitable_regimes ARRAY (legacy boolean for ranker M5.3 gating)
  · regime_winrates JSONB (continuous, used by ranker)
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.calendar import trading_days_between
from ifa.families.ta.params import load_params

log = logging.getLogger(__name__)


def compute_setup_metrics_v2(engine: Engine, on_date: date) -> int:
    """Compute v2.3 setup metrics using position_events_daily as ground truth."""
    p = load_params()
    weights = p.get("backtest_objective", {}).get("weights", {})
    w_t5 = float(weights.get("t5", 0.2))
    w_t10 = float(weights.get("t10", 0.1))
    w_t15 = float(weights.get("t15", 0.7))

    cal_start = on_date - timedelta(days=400)
    all_days = trading_days_between(engine, cal_start, on_date)
    if not all_days:
        return 0
    w60_start = all_days[-min(60, len(all_days))]
    w250_start = all_days[-min(250, len(all_days))]

    sql = text("""
        WITH joined_60 AS (
            SELECT c.setup_name, c.regime_at_gen,
                   p.return_t5_pct, p.return_t10_pct, p.return_t15_pct,
                   p.realized_return_pct, p.fill_status
            FROM ta.candidates_daily c
            JOIN ta.position_events_daily p ON p.candidate_id = c.candidate_id
            WHERE c.trade_date >= :w60 AND c.trade_date <= :on_date
              AND p.fill_status = 'filled'
        ),
        joined_250 AS (
            SELECT c.setup_name, p.return_t15_pct
            FROM ta.candidates_daily c
            JOIN ta.position_events_daily p ON p.candidate_id = c.candidate_id
            WHERE c.trade_date >= :w250 AND c.trade_date <= :on_date
              AND p.fill_status = 'filled'
        ),
        agg_60 AS (
            SELECT setup_name,
                   COUNT(*) AS n,
                   AVG(return_t15_pct) AS avg_ret_t15,
                   AVG(return_t5_pct)  AS avg_ret_t5,
                   AVG(return_t10_pct) AS avg_ret_t10,
                   100.0 * COUNT(*) FILTER (WHERE return_t15_pct >= 5.0) / NULLIF(COUNT(*), 0) AS wr_t15,
                   100.0 * COUNT(*) FILTER (WHERE return_t5_pct  >= 3.0) / NULLIF(COUNT(*), 0) AS wr_t5,
                   100.0 * COUNT(*) FILTER (WHERE return_t10_pct >= 4.0) / NULLIF(COUNT(*), 0) AS wr_t10,
                   AVG(return_t15_pct) FILTER (WHERE return_t15_pct >= 5.0) AS avg_gain,
                   AVG(return_t15_pct) FILTER (WHERE return_t15_pct <= -3.0) AS avg_loss
            FROM joined_60 GROUP BY setup_name
        ),
        agg_250 AS (
            SELECT setup_name,
                   100.0 * COUNT(*) FILTER (WHERE return_t15_pct >= 5.0) / NULLIF(COUNT(*), 0) AS wr_t15
            FROM joined_250 GROUP BY setup_name
        )
        SELECT a.setup_name, a.n,
               a.avg_ret_t15, a.wr_t15,
               a.avg_ret_t5,  a.wr_t5,
               a.avg_ret_t10, a.wr_t10,
               CASE WHEN a.avg_loss IS NULL OR a.avg_loss = 0 THEN NULL
                    ELSE a.avg_gain / ABS(a.avg_loss) END AS pl_ratio,
               b.wr_t15 AS wr_250
        FROM agg_60 a LEFT JOIN agg_250 b USING (setup_name)
    """)

    sql_regime_winrates = text("""
        SELECT c.setup_name, c.regime_at_gen,
               100.0 * COUNT(*) FILTER (WHERE p.return_t15_pct >= 5.0) / NULLIF(COUNT(*), 0) AS wr,
               COUNT(*) AS n
        FROM ta.candidates_daily c
        JOIN ta.position_events_daily p ON p.candidate_id = c.candidate_id
        WHERE c.trade_date >= :w250 AND c.trade_date <= :on_date
          AND c.regime_at_gen IS NOT NULL AND p.fill_status = 'filled'
        GROUP BY c.setup_name, c.regime_at_gen
    """)

    sql_upsert = text("""
        INSERT INTO ta.setup_metrics_daily
            (trade_date, setup_name, triggers_count,
             winrate_60d, avg_return_60d, pl_ratio_60d,
             winrate_250d, decay_score, suitable_regimes,
             regime_winrates, combined_score_60d)
        VALUES
            (:trade_date, :setup_name, :n,
             :wr60, :avg_ret, :pl,
             :wr250, :decay, :regimes,
             CAST(:regime_winrates AS jsonb), :combined)
        ON CONFLICT (trade_date, setup_name) DO UPDATE SET
            triggers_count = EXCLUDED.triggers_count,
            winrate_60d = EXCLUDED.winrate_60d,
            avg_return_60d = EXCLUDED.avg_return_60d,
            pl_ratio_60d = EXCLUDED.pl_ratio_60d,
            winrate_250d = EXCLUDED.winrate_250d,
            decay_score = EXCLUDED.decay_score,
            suitable_regimes = EXCLUDED.suitable_regimes,
            regime_winrates = EXCLUDED.regime_winrates,
            combined_score_60d = EXCLUDED.combined_score_60d
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "w60": w60_start, "w250": w250_start, "on_date": on_date,
        }).fetchall()
        regime_rows = conn.execute(sql_regime_winrates, {
            "w250": w250_start, "on_date": on_date,
        }).fetchall()

    rw_by_setup: dict[str, dict] = {}
    for r in regime_rows:
        rw_by_setup.setdefault(r[0], {})[r[1]] = {
            "winrate": float(r[2]) if r[2] is not None else None,
            "n": int(r[3]),
        }

    n_written = 0
    with engine.begin() as conn:
        for r in rows:
            (setup_name, n, avg_ret_t15, wr_t15, avg_ret_t5, wr_t5,
             avg_ret_t10, wr_t10, pl, wr_250) = r
            wr_t15_f = float(wr_t15) if wr_t15 is not None else 0.0
            wr_t5_f = float(wr_t5) if wr_t5 is not None else 0.0
            wr_t10_f = float(wr_t10) if wr_t10 is not None else 0.0
            avg_t15 = float(avg_ret_t15) if avg_ret_t15 is not None else 0.0
            avg_t5 = float(avg_ret_t5) if avg_ret_t5 is not None else 0.0
            avg_t10 = float(avg_ret_t10) if avg_ret_t10 is not None else 0.0
            combined = (
                w_t15 * (wr_t15_f / 100.0) * avg_t15
                + w_t5 * (wr_t5_f / 100.0) * avg_t5
                + w_t10 * (wr_t10_f / 100.0) * avg_t10
            )
            decay = (wr_t15_f - float(wr_250)) if wr_250 is not None else 0.0
            # suitable_regimes: regimes whose winrate exceeds 25%
            suitable = [
                regime for regime, info in rw_by_setup.get(setup_name, {}).items()
                if info["n"] >= 5 and (info["winrate"] or 0) >= 25.0
            ]
            rw_map = rw_by_setup.get(setup_name, {})
            rw_payload = {k: v["winrate"] for k, v in rw_map.items() if v["winrate"] is not None}

            conn.execute(sql_upsert, {
                "trade_date": on_date,
                "setup_name": setup_name,
                "n": int(n),
                "wr60": wr_t15_f,
                "avg_ret": avg_t15,
                "pl": float(pl) if pl is not None else None,
                "wr250": float(wr_250) if wr_250 is not None else None,
                "decay": decay,
                "regimes": suitable,
                "regime_winrates": json.dumps(rw_payload) if rw_payload else None,
                "combined": round(combined, 4),
            })
            n_written += 1
    log.info("compute_setup_metrics_v2(%s): wrote %d rows", on_date, n_written)
    return n_written
