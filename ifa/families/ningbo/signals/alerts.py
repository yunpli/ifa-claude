"""Position alerts: stop-loss (跌破生命线) and take-profit (≥+20%).

Reads from `ningbo.recommendation_outcomes` after the daily tracking batch
has updated terminal states. Returns alerts that fired *today specifically*
(outcome_date = on_date), so subscribers see "actionable today" signals
rather than historical events.

Two alert types:
    - stop_loss:    today's close < MA24, within 15-day tracking window
    - take_profit:  cumulative return reached +20% within tracking window

Both alerts apply per (rec_date, ts_code, strategy, scoring_mode) tuple.
A stock recommended on D-3 by sniper that triggered stop_loss today
appears in the alerts; the same stock recommended on D-7 by basin (still
in progress) does NOT appear in stop_loss alerts (its tracking didn't
trigger today).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import Engine, text


def detect_today_alerts(engine: Engine, on_date: dt.date) -> dict[str, pd.DataFrame]:
    """Find recommendations whose terminal state was set TO today.

    Returns dict with two DataFrames:
        'stop_loss':   recs that crossed below MA24 today
        'take_profit': recs that hit ≥+20% today

    Each DataFrame has columns:
        rec_date, ts_code, strategy, scoring_mode,
        rec_price, outcome_track_day, final_cum_return,
        peak_cum_return, trough_cum_return
    """
    sql = text("""
        SELECT
            r.rec_date, r.ts_code, r.strategy, r.scoring_mode,
            r.rec_price, r.confidence_score,
            o.outcome_status, o.outcome_track_day,
            o.final_cum_return, o.peak_cum_return, o.trough_cum_return
        FROM ningbo.recommendation_outcomes o
        JOIN ningbo.recommendations_daily r
          ON r.rec_date = o.rec_date
         AND r.ts_code = o.ts_code
         AND r.strategy = o.strategy
         AND r.scoring_mode = o.scoring_mode
        WHERE o.outcome_date = :on_date
          AND o.outcome_status IN ('stop_loss', 'take_profit')
        ORDER BY o.outcome_status, o.final_cum_return DESC
    """)
    df = pd.read_sql(sql, engine, params={"on_date": on_date})

    result = {
        "stop_loss": df[df["outcome_status"] == "stop_loss"].drop(columns=["outcome_status"]).reset_index(drop=True),
        "take_profit": df[df["outcome_status"] == "take_profit"].drop(columns=["outcome_status"]).reset_index(drop=True),
    }
    return result


def fetch_in_progress_summary(engine: Engine, on_date: dt.date) -> pd.DataFrame:
    """Return all recs still in progress (not yet terminal) as of on_date.

    Used by the daily report's recap section to render the active tracking
    table (≤75 rows: 5 picks/day × 15 day window, per scoring_mode).
    """
    sql = text("""
        SELECT
            r.rec_date, r.ts_code, r.strategy, r.scoring_mode,
            r.rec_price, r.confidence_score,
            o.outcome_status,
            o.peak_cum_return, o.trough_cum_return,
            o.outcome_track_day
        FROM ningbo.recommendations_daily r
        JOIN ningbo.recommendation_outcomes o
          ON r.rec_date = o.rec_date
         AND r.ts_code = o.ts_code
         AND r.strategy = o.strategy
         AND r.scoring_mode = o.scoring_mode
        WHERE r.rec_date >= :earliest_rec_date
          AND r.rec_date <= :on_date
        ORDER BY r.rec_date DESC, r.scoring_mode, r.confidence_score DESC
    """)
    earliest = on_date - dt.timedelta(days=30)  # ~15 trading days ≈ 21 calendar days; buffer
    return pd.read_sql(sql, engine, params={
        "on_date": on_date,
        "earliest_rec_date": earliest,
    })
