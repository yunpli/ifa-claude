"""Full candidate pool backfill — Phase 3.B.1.

Mirrors Phase 2's `run_ningbo_backfill` but writes the FULL output of each
strategy (every hit, no top-5 selection) to ningbo.candidates_daily.

This eliminates the sample selection bias that capped ML performance:
when training on top-5 picks only, the model couldn't learn what
distinguishes a good pick from the other 100+ candidates of that day.

Then `compute_candidate_outcomes` computes 15-day forward labels in one
bulk SQL — same approach as Phase 2's `run_bulk_tracking_sql`.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from itertools import groupby
from typing import Callable

import pandas as pd
from sqlalchemy import Engine, text

from ifa.families.ningbo.backfill import (
    LOOKBACK_BUFFER_DAYS, LOOKBACK_CALENDAR_DAYS,
    _compute_weekly_from_daily, _get_trading_days, _load_bulk_daily,
)
from ifa.families.ningbo.strategies import (
    half_year_double, sniper, treasure_basin,
)
from ifa.families.ningbo.strategies._indicators import compute_all_indicators_bulk


@dataclass
class CandidateBackfillSummary:
    start: dt.date
    end: dt.date
    trading_days_processed: int = 0
    trading_days_skipped: int = 0
    candidates_inserted: int = 0
    by_strategy: dict[str, int] = field(default_factory=dict)
    errors: list[tuple[dt.date, str]] = field(default_factory=list)
    elapsed_seconds: float = 0.0


# ── Per-day candidate insertion ───────────────────────────────────────────────

_INSERT_CANDIDATES_SQL = text("""
    INSERT INTO ningbo.candidates_daily
        (rec_date, ts_code, strategy, confidence_score, rec_price, signal_meta)
    VALUES
        (:rec_date, :ts_code, :strategy, :confidence_score, :rec_price, :signal_meta)
    ON CONFLICT (rec_date, ts_code, strategy)
    DO UPDATE SET
        confidence_score = EXCLUDED.confidence_score,
        rec_price        = EXCLUDED.rec_price,
        signal_meta      = EXCLUDED.signal_meta
""")


def _insert_strategy_candidates(
    engine: Engine,
    df: pd.DataFrame,
    rec_date: dt.date,
    strategy: str,
    price_lookup: dict[str, float],
) -> int:
    """Persist all hits from one strategy on one day."""
    if df.empty:
        return 0
    rows = []
    for r in df.itertuples(index=False):
        ts = r.ts_code
        price = price_lookup.get(ts)
        if price is None:
            continue
        meta = getattr(r, "signal_meta", None) or getattr(r, "rec_signal_meta", None)
        if meta is None:
            meta = {}
        rows.append({
            "rec_date":         rec_date,
            "ts_code":          ts,
            "strategy":         strategy,
            "confidence_score": float(r.confidence_score),
            "rec_price":        float(price),
            "signal_meta":      json.dumps(meta, default=str),
        })
    if not rows:
        return 0
    with engine.begin() as conn:
        conn.execute(_INSERT_CANDIDATES_SQL, rows)
    return len(rows)


# ── Main backfill driver ──────────────────────────────────────────────────────

def backfill_candidates(
    engine: Engine,
    start: dt.date,
    end: dt.date,
    *,
    on_log: Callable[[str], None] = lambda m: None,
) -> CandidateBackfillSummary:
    """Backfill the full candidate pool for [start, end].

    Same chunking + vectorized indicator strategy as Phase 2 but writes
    every strategy hit, not just top-5.
    """
    t_global = time.time()
    summary = CandidateBackfillSummary(start=start, end=end)

    trading_days = _get_trading_days(engine, start, end)
    if not trading_days:
        on_log("⚠️  No trading days found.")
        return summary

    on_log(f"Backfilling candidate pool: {len(trading_days)} trading days  "
           f"{trading_days[0]} → {trading_days[-1]}")

    monthly_chunks: list[list[dt.date]] = []
    for _, days_iter in groupby(trading_days, key=lambda d: (d.year, d.month)):
        monthly_chunks.append(list(days_iter))

    processed = 0
    total = len(trading_days)

    for chunk_idx, chunk_days in enumerate(monthly_chunks):
        chunk_start_day = chunk_days[0]
        chunk_end_day   = chunk_days[-1]
        on_log(f"\n── Chunk {chunk_idx+1}/{len(monthly_chunks)}: "
               f"{chunk_start_day.year}-{chunk_start_day.month:02d}  "
               f"({len(chunk_days)} days) ──")

        bulk_start = chunk_start_day - dt.timedelta(
            days=LOOKBACK_CALENDAR_DAYS + LOOKBACK_BUFFER_DAYS
        )
        try:
            t_load = time.time()
            bulk_daily = _load_bulk_daily(engine, bulk_start, chunk_end_day)
            if bulk_daily.empty:
                on_log(f"  ⚠️  empty bulk load — skipping chunk")
                summary.trading_days_skipped += len(chunk_days)
                continue

            bulk_daily["trade_date"] = pd.to_datetime(bulk_daily["trade_date"])
            bulk_daily = compute_all_indicators_bulk(bulk_daily)
            weekly_full = _compute_weekly_from_daily(bulk_daily)
            if not weekly_full.empty:
                weekly_full["week_end"] = pd.to_datetime(weekly_full["week_end"])

            on_log(
                f"  bulk loaded + enriched: {len(bulk_daily):,} rows  "
                f"[{time.time()-t_load:.1f}s]"
            )
        except Exception as exc:
            on_log(f"  ❌ bulk load failed: {exc}")
            for d in chunk_days:
                summary.errors.append((d, f"bulk load: {exc}"))
                summary.trading_days_skipped += 1
            continue

        # Per-day candidate generation
        for day in chunk_days:
            processed += 1
            t_day = time.time()
            day_ts = pd.Timestamp(day)
            lookback_ts = pd.Timestamp(day - dt.timedelta(days=LOOKBACK_CALENDAR_DAYS))

            slice_df = bulk_daily[
                (bulk_daily["trade_date"] >= lookback_ts) &
                (bulk_daily["trade_date"] <= day_ts)
            ].copy()
            slice_df["trade_date"] = slice_df["trade_date"].dt.date

            if not (slice_df["trade_date"] == day).any():
                summary.trading_days_skipped += 1
                continue

            # Build price lookup for the day's close
            day_data = slice_df[slice_df["trade_date"] == day]
            price_lookup = dict(zip(day_data["ts_code"], day_data["close"].astype(float)))

            # Weekly slice
            if not weekly_full.empty:
                weekly_lb = pd.Timestamp(day - dt.timedelta(weeks=44))
                weekly_slice = weekly_full[
                    (weekly_full["week_end"] <= day_ts) &
                    (weekly_full["week_end"] >= weekly_lb)
                ].copy()
                weekly_slice["week_end"] = weekly_slice["week_end"].dt.date
                weekly_slice = weekly_slice[weekly_slice["ts_code"].isin(set(day_data["ts_code"]))]
            else:
                weekly_slice = pd.DataFrame()

            # Run strategies — keep ALL hits
            try:
                sniper_df = sniper.detect_signals(slice_df, day)
                basin_df  = treasure_basin.detect_signals(slice_df, day)
                hyd_df    = half_year_double.detect_signals(slice_df, weekly_slice, day)
            except Exception as exc:
                on_log(f"  ❌ {day}: strategy error: {exc}")
                summary.errors.append((day, f"strategy: {exc}"))
                summary.trading_days_skipped += 1
                continue

            n_s = _insert_strategy_candidates(engine, sniper_df, day, "sniper", price_lookup)
            n_b = _insert_strategy_candidates(engine, basin_df,  day, "treasure_basin", price_lookup)
            n_h = _insert_strategy_candidates(engine, hyd_df,    day, "half_year_double", price_lookup)

            summary.candidates_inserted += (n_s + n_b + n_h)
            summary.by_strategy["sniper"]           = summary.by_strategy.get("sniper", 0) + n_s
            summary.by_strategy["treasure_basin"]   = summary.by_strategy.get("treasure_basin", 0) + n_b
            summary.by_strategy["half_year_double"] = summary.by_strategy.get("half_year_double", 0) + n_h
            summary.trading_days_processed += 1

            if processed % 20 == 0 or day == trading_days[-1]:
                on_log(
                    f"  [{processed}/{total}] {day}  "
                    f"sniper={n_s} basin={n_b} hyd={n_h}  "
                    f"[{time.time()-t_day:.1f}s/day]"
                )

    summary.elapsed_seconds = time.time() - t_global
    return summary


# ── Bulk SQL outcomes ─────────────────────────────────────────────────────────

def compute_candidate_outcomes(
    engine: Engine,
    start: dt.date,
    end: dt.date,
    *,
    on_log: Callable[[str], None] = lambda m: None,
) -> dict:
    """Compute 15-day forward labels for all candidates in [start, end].

    Uses the same bulk SQL approach as Phase 2's run_bulk_tracking_sql but
    sources from candidates_daily (the full pool, not top-5).
    Stop-loss rule: any close < MA24 within 15 days.
    Take-profit rule: any cum_return >= 0.20 within 15 days.
    Expired: neither triggered after 15 days.
    """
    ma24_start    = start - dt.timedelta(days=60)
    tracking_end  = end + dt.timedelta(days=30)
    on_log(f"Bulk candidate outcomes: rec_date {start} → {end}, "
           f"ma24 warmup from {ma24_start}")

    t0 = time.time()

    # ── Step 1: Build a temporary tracking table for all candidates ──────────
    # We compute peak/trough/final return + outcome status directly without
    # persisting per-day tracking rows (we don't need them for ML labels).
    sql = text("""
        WITH
        ma24_all AS (
            SELECT
                ts_code, trade_date, close,
                AVG(close)  OVER (PARTITION BY ts_code ORDER BY trade_date
                                  ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS ma24,
                COUNT(*)    OVER (PARTITION BY ts_code ORDER BY trade_date
                                  ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS bars_count
            FROM smartmoney.raw_daily
            WHERE trade_date BETWEEN :ma24_start AND :tracking_end
        ),
        tracking_pairs AS (
            SELECT
                c.rec_date, c.ts_code, c.strategy, c.rec_price,
                tc.cal_date AS track_date, tc.track_day
            FROM ningbo.candidates_daily c
            CROSS JOIN LATERAL (
                SELECT cal_date, ROW_NUMBER() OVER (ORDER BY cal_date) AS track_day
                FROM smartmoney.trade_cal
                WHERE exchange = 'SSE' AND is_open
                  AND cal_date >  c.rec_date
                  AND cal_date <= :tracking_end
                ORDER BY cal_date
                LIMIT 15
            ) tc
            WHERE c.rec_date BETWEEN :start_date AND :end_date
        ),
        with_close AS (
            SELECT
                tp.rec_date, tp.ts_code, tp.strategy, tp.rec_price,
                tp.track_day, tp.track_date,
                m.close,
                m.ma24,
                m.bars_count,
                (m.close - tp.rec_price) / tp.rec_price AS cum_return,
                CASE WHEN m.bars_count >= 24 THEN (m.close < m.ma24)
                     ELSE NULL END AS below_ma24
            FROM tracking_pairs tp
            JOIN ma24_all m
              ON m.ts_code    = tp.ts_code
             AND m.trade_date = tp.track_date
            WHERE m.close IS NOT NULL
        ),
        first_sl AS (
            SELECT rec_date, ts_code, strategy, MIN(track_day) AS sl_day
            FROM with_close WHERE below_ma24 = TRUE
            GROUP BY rec_date, ts_code, strategy
        ),
        first_tp AS (
            SELECT rec_date, ts_code, strategy, MIN(track_day) AS tp_day
            FROM with_close WHERE cum_return >= 0.20
            GROUP BY rec_date, ts_code, strategy
        ),
        agg AS (
            SELECT
                rec_date, ts_code, strategy,
                MAX(track_day)  AS max_track_day,
                MAX(cum_return) AS peak_cum,
                MIN(cum_return) AS trough_cum,
                COUNT(*)        AS n_days
            FROM with_close
            GROUP BY rec_date, ts_code, strategy
        ),
        last_row AS (
            SELECT DISTINCT ON (rec_date, ts_code, strategy)
                   rec_date, ts_code, strategy,
                   cum_return AS final_cum,
                   track_date AS last_date
            FROM with_close
            ORDER BY rec_date, ts_code, strategy, track_day DESC
        ),
        outcome AS (
            SELECT
                a.rec_date, a.ts_code, a.strategy,
                a.max_track_day,  a.peak_cum,  a.trough_cum,  a.n_days,
                lr.final_cum,
                CASE
                    WHEN sl.sl_day IS NOT NULL
                     AND (tp.tp_day IS NULL OR sl.sl_day <= tp.tp_day)  THEN 'stop_loss'
                    WHEN tp.tp_day IS NOT NULL                          THEN 'take_profit'
                    WHEN a.max_track_day >= 15                          THEN 'expired'
                    ELSE 'in_progress'
                END AS outcome_status,
                CASE
                    WHEN sl.sl_day IS NOT NULL
                     AND (tp.tp_day IS NULL OR sl.sl_day <= tp.tp_day) THEN sl.sl_day
                    WHEN tp.tp_day IS NOT NULL                          THEN tp.tp_day
                    WHEN a.max_track_day >= 15                          THEN a.max_track_day
                    ELSE NULL
                END AS outcome_track_day
            FROM agg a
            LEFT JOIN first_sl sl USING (rec_date, ts_code, strategy)
            LEFT JOIN first_tp tp USING (rec_date, ts_code, strategy)
            LEFT JOIN last_row lr USING (rec_date, ts_code, strategy)
        ),
        outcome_with_date AS (
            SELECT o.*, t.track_date AS outcome_date
            FROM outcome o
            LEFT JOIN with_close t
              ON  t.rec_date  = o.rec_date
              AND t.ts_code   = o.ts_code
              AND t.strategy  = o.strategy
              AND t.track_day = o.outcome_track_day
        )
        INSERT INTO ningbo.candidate_outcomes
            (rec_date, ts_code, strategy,
             outcome_status, outcome_track_day, outcome_date,
             final_cum_return, peak_cum_return, trough_cum_return,
             n_tracking_days, updated_at)
        SELECT
            rec_date, ts_code, strategy,
            outcome_status, outcome_track_day, outcome_date,
            final_cum, peak_cum, trough_cum, n_days, NOW()
        FROM outcome_with_date
        ON CONFLICT (rec_date, ts_code, strategy)
        DO UPDATE SET
            outcome_status    = EXCLUDED.outcome_status,
            outcome_track_day = EXCLUDED.outcome_track_day,
            outcome_date      = EXCLUDED.outcome_date,
            final_cum_return  = EXCLUDED.final_cum_return,
            peak_cum_return   = EXCLUDED.peak_cum_return,
            trough_cum_return = EXCLUDED.trough_cum_return,
            n_tracking_days   = EXCLUDED.n_tracking_days,
            updated_at        = NOW()
    """)

    with engine.begin() as conn:
        result = conn.execute(sql, {
            "ma24_start":   ma24_start,
            "tracking_end": tracking_end,
            "start_date":   start,
            "end_date":     end,
        })
        n = result.rowcount

    on_log(f"  → {n:,} candidate outcomes upserted  [{time.time()-t0:.1f}s]")
    return {"outcomes_upserted": n, "elapsed_seconds": time.time() - t0}
