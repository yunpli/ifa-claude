"""Daily tracking batch — update tracking + outcomes for in-progress recs.

Two phases per run:

  Phase A — Append today's tracking row for every in-progress recommendation:
    - Find all recs in `recommendation_outcomes` with status='in_progress'
      whose `rec_date` is within (today - 15 trading days, today]
    - For each, fetch today's close + MA24 from raw_daily
    - Compute cum_return = (close - rec_price) / rec_price
    - Compute below_ma24 flag
    - INSERT into recommendation_tracking (rec_date, ts_code, strategy,
      scoring_mode, track_day=today_distance, track_date, ...)

  Phase B — Update terminal state in `recommendation_outcomes`:
    - For each rec, recompute outcome based on accumulated tracking rows:
        if any below_ma24 in tracking → 'stop_loss', terminal at first such day
        elif any cum_return >= 0.20    → 'take_profit', terminal at first such day
        elif track_day == 15           → 'expired', terminal at day 15
        else                            → 'in_progress'
    - Update peak_cum_return, trough_cum_return, final_cum_return,
      outcome_track_day, outcome_date, outcome_status, updated_at.

Idempotent: re-running for the same on_date is safe (UPSERT on PK).

When called as part of nightly report pipeline, on_date = report_date.
For backfill, on_date iterates over historical trading days.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd
from sqlalchemy import Engine, text

TRACKING_WINDOW_TRADING_DAYS = 15
TAKE_PROFIT_THRESHOLD = 0.20      # +20% cumulative return → 止盈
MA24_WINDOW = 24


@dataclass
class TrackingBatchSummary:
    on_date: dt.date
    n_recs_processed: int = 0
    n_tracking_rows_inserted: int = 0
    newly_stop_loss: int = 0
    newly_take_profit: int = 0
    newly_expired: int = 0
    still_in_progress: int = 0
    skipped_missing_data: int = 0
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def _trading_days_between(engine: Engine, start: dt.date, end: dt.date) -> list[dt.date]:
    """Return trading days in (start, end] from smartmoney.trade_cal."""
    sql = text("""
        SELECT cal_date FROM smartmoney.trade_cal
        WHERE exchange='SSE' AND is_open
          AND cal_date > :start AND cal_date <= :end
        ORDER BY cal_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"start": start, "end": end}).fetchall()
    return [r[0] for r in rows]


def _fetch_recs_in_progress(engine: Engine, on_date: dt.date, lookback_calendar_days: int = 30) -> pd.DataFrame:
    """Fetch in-progress recommendations whose rec_date is within tracking window."""
    earliest_rec_date = on_date - dt.timedelta(days=lookback_calendar_days)
    sql = text("""
        SELECT r.rec_date, r.ts_code, r.strategy, r.scoring_mode,
               r.rec_price,
               COALESCE(o.outcome_status, 'in_progress') AS outcome_status,
               o.peak_cum_return, o.trough_cum_return
        FROM ningbo.recommendations_daily r
        LEFT JOIN ningbo.recommendation_outcomes o
               ON r.rec_date = o.rec_date AND r.ts_code = o.ts_code
              AND r.strategy = o.strategy AND r.scoring_mode = o.scoring_mode
        WHERE r.rec_date >= :earliest
          AND r.rec_date < :on_date
          AND COALESCE(o.outcome_status, 'in_progress') = 'in_progress'
        ORDER BY r.rec_date, r.ts_code, r.strategy, r.scoring_mode
    """)
    return pd.read_sql(
        sql, engine,
        params={"earliest": earliest_rec_date, "on_date": on_date},
    )


def _fetch_today_data(engine: Engine, ts_codes: Iterable[str], on_date: dt.date) -> pd.DataFrame:
    """Fetch close + MA24 for the codes on on_date.

    MA24 is computed inline via window function over last 24 trading days.
    """
    codes = list(set(ts_codes))
    if not codes:
        return pd.DataFrame(columns=["ts_code", "close", "ma24"])

    # Compute MA24 from raw_daily as average of last 24 closes including today
    sql = text("""
        WITH ranked AS (
            SELECT ts_code, trade_date, close,
                   ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
            FROM smartmoney.raw_daily
            WHERE ts_code = ANY(:codes)
              AND trade_date <= :on_date
              AND trade_date > (:on_date - INTERVAL '60 days')
        )
        SELECT ts_code,
               MAX(CASE WHEN rn = 1 AND trade_date = :on_date THEN close END) AS close_today,
               AVG(CASE WHEN rn <= :ma_w THEN close END) AS ma24,
               COUNT(CASE WHEN rn <= :ma_w THEN 1 END) AS bars_used
        FROM ranked
        GROUP BY ts_code
    """)
    df = pd.read_sql(sql, engine, params={
        "codes": codes, "on_date": on_date, "ma_w": MA24_WINDOW
    })
    df = df.rename(columns={"close_today": "close"})
    # Only keep rows where bars_used == 24 (full lookback)
    df = df[df["bars_used"] >= MA24_WINDOW]
    return df[["ts_code", "close", "ma24"]]


def _track_day_for(rec_date: dt.date, on_date: dt.date, tcal: list[dt.date]) -> int:
    """Compute track_day = number of trading days strictly after rec_date up to on_date."""
    return sum(1 for d in tcal if rec_date < d <= on_date)


def run_tracking_batch(engine: Engine, on_date: dt.date) -> TrackingBatchSummary:
    """Run tracking + outcomes update for on_date.

    Idempotent: safe to re-run for the same date.
    """
    import time
    t0 = time.time()
    summary = TrackingBatchSummary(on_date=on_date)

    # Trading calendar — needed to compute track_day correctly
    tcal_back = _trading_days_between(
        engine, on_date - dt.timedelta(days=30), on_date
    )

    recs = _fetch_recs_in_progress(engine, on_date)
    summary.n_recs_processed = len(recs)
    if recs.empty:
        summary.elapsed_seconds = time.time() - t0
        return summary

    # Fetch today's close + MA24 for all unique ts_codes
    today_data = _fetch_today_data(engine, recs["ts_code"].unique(), on_date)
    today_lookup = today_data.set_index("ts_code").to_dict(orient="index")

    # Group new tracking rows + new outcome updates
    tracking_inserts: list[dict] = []
    outcome_updates: list[dict] = []

    for _, rec in recs.iterrows():
        ts_code = rec["ts_code"]
        rec_date = rec["rec_date"]
        rec_price = float(rec["rec_price"])

        td = today_lookup.get(ts_code)
        if td is None or pd.isna(td.get("close")):
            summary.skipped_missing_data += 1
            continue

        close = float(td["close"])
        ma24 = float(td["ma24"]) if td.get("ma24") is not None and not pd.isna(td["ma24"]) else None

        track_day = _track_day_for(rec_date, on_date, tcal_back)
        if track_day < 1 or track_day > TRACKING_WINDOW_TRADING_DAYS:
            continue  # outside window — shouldn't happen given query filter

        cum_return = (close - rec_price) / rec_price
        below_ma24 = (close < ma24) if ma24 is not None else None

        tracking_inserts.append({
            "rec_date": rec_date,
            "ts_code": ts_code,
            "strategy": rec["strategy"],
            "scoring_mode": rec["scoring_mode"],
            "track_day": track_day,
            "track_date": on_date,
            "close_price": close,
            "cum_return": cum_return,
            "ma24": ma24,
            "below_ma24": below_ma24,
        })

    # Phase A: write tracking rows (UPSERT)
    if tracking_inserts:
        upsert_track_sql = text("""
            INSERT INTO ningbo.recommendation_tracking
                (rec_date, ts_code, strategy, scoring_mode, track_day,
                 track_date, close_price, cum_return, ma24, below_ma24)
            VALUES
                (:rec_date, :ts_code, :strategy, :scoring_mode, :track_day,
                 :track_date, :close_price, :cum_return, :ma24, :below_ma24)
            ON CONFLICT (rec_date, ts_code, strategy, scoring_mode, track_day)
            DO UPDATE SET
                track_date = EXCLUDED.track_date,
                close_price = EXCLUDED.close_price,
                cum_return = EXCLUDED.cum_return,
                ma24 = EXCLUDED.ma24,
                below_ma24 = EXCLUDED.below_ma24
        """)
        with engine.begin() as conn:
            conn.execute(upsert_track_sql, tracking_inserts)
        summary.n_tracking_rows_inserted = len(tracking_inserts)

    # Phase B: recompute outcomes for affected recs using accumulated tracking
    affected_keys = list({
        (r["rec_date"], r["ts_code"], r["strategy"], r["scoring_mode"])
        for r in tracking_inserts
    })

    for rec_date, ts_code, strategy, scoring_mode in affected_keys:
        with engine.connect() as conn:
            tracks = conn.execute(text("""
                SELECT track_day, track_date, cum_return, below_ma24
                FROM ningbo.recommendation_tracking
                WHERE rec_date = :rd AND ts_code = :tc
                  AND strategy = :s AND scoring_mode = :sm
                ORDER BY track_day
            """), {"rd": rec_date, "tc": ts_code, "s": strategy, "sm": scoring_mode}).fetchall()

        if not tracks:
            continue

        peak = max(float(t[2]) for t in tracks)
        trough = min(float(t[2]) for t in tracks)
        final = float(tracks[-1][2])
        last_track_day = int(tracks[-1][0])

        # Determine terminal state (priority: stop_loss > take_profit > expired)
        outcome_status = "in_progress"
        outcome_track_day = None
        outcome_date = None

        for td, tdate, cret, bm24 in tracks:
            if bm24:
                outcome_status = "stop_loss"
                outcome_track_day = int(td)
                outcome_date = tdate
                break
        if outcome_status == "in_progress":
            for td, tdate, cret, bm24 in tracks:
                if float(cret) >= TAKE_PROFIT_THRESHOLD:
                    outcome_status = "take_profit"
                    outcome_track_day = int(td)
                    outcome_date = tdate
                    break
        if outcome_status == "in_progress" and last_track_day >= TRACKING_WINDOW_TRADING_DAYS:
            outcome_status = "expired"
            outcome_track_day = last_track_day
            outcome_date = tracks[-1][1]

        outcome_updates.append({
            "rec_date": rec_date,
            "ts_code": ts_code,
            "strategy": strategy,
            "scoring_mode": scoring_mode,
            "outcome_status": outcome_status,
            "outcome_track_day": outcome_track_day,
            "outcome_date": outcome_date,
            "final_cum_return": final,
            "peak_cum_return": peak,
            "trough_cum_return": trough,
        })

        if outcome_status == "stop_loss":
            summary.newly_stop_loss += 1
        elif outcome_status == "take_profit":
            summary.newly_take_profit += 1
        elif outcome_status == "expired":
            summary.newly_expired += 1
        else:
            summary.still_in_progress += 1

    if outcome_updates:
        upsert_outcome_sql = text("""
            INSERT INTO ningbo.recommendation_outcomes
                (rec_date, ts_code, strategy, scoring_mode,
                 outcome_status, outcome_track_day, outcome_date,
                 final_cum_return, peak_cum_return, trough_cum_return,
                 updated_at)
            VALUES
                (:rec_date, :ts_code, :strategy, :scoring_mode,
                 :outcome_status, :outcome_track_day, :outcome_date,
                 :final_cum_return, :peak_cum_return, :trough_cum_return,
                 NOW())
            ON CONFLICT (rec_date, ts_code, strategy, scoring_mode)
            DO UPDATE SET
                outcome_status = EXCLUDED.outcome_status,
                outcome_track_day = EXCLUDED.outcome_track_day,
                outcome_date = EXCLUDED.outcome_date,
                final_cum_return = EXCLUDED.final_cum_return,
                peak_cum_return = EXCLUDED.peak_cum_return,
                trough_cum_return = EXCLUDED.trough_cum_return,
                updated_at = NOW()
        """)
        with engine.begin() as conn:
            conn.execute(upsert_outcome_sql, outcome_updates)

    summary.elapsed_seconds = time.time() - t0
    return summary


def insert_recommendations(
    engine: Engine,
    recs_df: pd.DataFrame,
    rec_date: dt.date,
    *,
    scoring_mode: str,
    param_version: str,
) -> int:
    """Insert today's recommendations + initialize their outcomes as 'in_progress'.

    `recs_df` must have: ts_code, strategy, confidence_score, rec_signal_meta (dict).
    rec_price is fetched from raw_daily for rec_date.
    """
    if recs_df.empty:
        return 0

    # Fetch today's close as rec_price for each ts_code
    sql = text("""
        SELECT ts_code, close
        FROM smartmoney.raw_daily
        WHERE ts_code = ANY(:codes) AND trade_date = :rd
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "codes": recs_df["ts_code"].tolist(), "rd": rec_date
        }).fetchall()
    price_lookup = {r[0]: float(r[1]) for r in rows}

    rec_inserts: list[dict] = []
    outcome_inits: list[dict] = []

    import json
    for _, r in recs_df.iterrows():
        ts_code = r["ts_code"]
        rec_price = price_lookup.get(ts_code)
        if rec_price is None:
            continue
        meta = r.get("rec_signal_meta") or {}
        rec_inserts.append({
            "rec_date": rec_date,
            "ts_code": ts_code,
            "strategy": r["strategy"],
            "scoring_mode": scoring_mode,
            "param_version": param_version,
            "rec_price": rec_price,
            "confidence_score": float(r["confidence_score"]),
            "rec_signal_meta": json.dumps(meta, default=str),
            "llm_narrative": r.get("llm_narrative"),
        })
        outcome_inits.append({
            "rec_date": rec_date,
            "ts_code": ts_code,
            "strategy": r["strategy"],
            "scoring_mode": scoring_mode,
            "outcome_status": "in_progress",
            "outcome_track_day": None,
            "outcome_date": None,
            "final_cum_return": 0.0,
            "peak_cum_return": 0.0,
            "trough_cum_return": 0.0,
        })

    insert_rec_sql = text("""
        INSERT INTO ningbo.recommendations_daily
            (rec_date, ts_code, strategy, scoring_mode, param_version,
             rec_price, confidence_score, rec_signal_meta, llm_narrative)
        VALUES
            (:rec_date, :ts_code, :strategy, :scoring_mode, :param_version,
             :rec_price, :confidence_score, :rec_signal_meta, :llm_narrative)
        ON CONFLICT (rec_date, ts_code, strategy, scoring_mode)
        DO UPDATE SET
            param_version = EXCLUDED.param_version,
            rec_price = EXCLUDED.rec_price,
            confidence_score = EXCLUDED.confidence_score,
            rec_signal_meta = EXCLUDED.rec_signal_meta,
            llm_narrative = EXCLUDED.llm_narrative
    """)
    insert_outcome_sql = text("""
        INSERT INTO ningbo.recommendation_outcomes
            (rec_date, ts_code, strategy, scoring_mode,
             outcome_status, outcome_track_day, outcome_date,
             final_cum_return, peak_cum_return, trough_cum_return, updated_at)
        VALUES
            (:rec_date, :ts_code, :strategy, :scoring_mode,
             :outcome_status, :outcome_track_day, :outcome_date,
             :final_cum_return, :peak_cum_return, :trough_cum_return, NOW())
        ON CONFLICT (rec_date, ts_code, strategy, scoring_mode) DO NOTHING
    """)

    with engine.begin() as conn:
        if rec_inserts:
            conn.execute(insert_rec_sql, rec_inserts)
        if outcome_inits:
            conn.execute(insert_outcome_sql, outcome_inits)

    return len(rec_inserts)
