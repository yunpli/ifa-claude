"""Historical signal backfill for ningbo strategies — Phase 2.

Processes all trading days in [start, end] in monthly chunks, loading
raw_daily data once per chunk (200-day lookback window) and computing
signals in-memory via pandas slicing.

Key design decisions:
  - Slim DB query: only raw_daily (OHLCV + vol) — strategies need no more.
  - Monthly chunk to cap per-chunk memory at ~80 MB.
  - Weekly bars computed in-memory from the same bulk load.
  - Tracking batch called per-day (idempotent DB UPSERTs).
  - LLM narrative skipped (too slow/expensive for history; llm_narrative=None).
  - Fully idempotent: all inserts use ON CONFLICT DO UPDATE / DO NOTHING.

Usage:
    from ifa.families.ningbo.backfill import run_ningbo_backfill
    summary = run_ningbo_backfill(engine, start, end, on_log=print)
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from itertools import groupby
from typing import Callable

import pandas as pd
from sqlalchemy import Engine, text

from ifa.families.ningbo.signals.confidence import HeuristicScorer
from ifa.families.ningbo.strategies._indicators import compute_all_indicators_bulk
from ifa.families.ningbo.signals.selection import select_top_n
from ifa.families.ningbo.strategies import (
    half_year_double, six_step, sniper, treasure_basin,
)
from ifa.families.ningbo.tracking.batch import (
    insert_recommendations, run_tracking_batch,
)

TOP_N = 10                     # persist top-10 to recommendations_daily (audit + ML rerank)
PER_STRATEGY_CAP = 3
LOOKBACK_CALENDAR_DAYS = 200   # mirror of Phase 1 default
LOOKBACK_BUFFER_DAYS = 10      # extra buffer so the first days of each chunk have full lookback
WEEKLY_LOOKBACK_WEEKS = 44     # 40 + 4-week buffer


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class DayResult:
    trade_date: dt.date
    sniper_n: int = 0
    basin_n: int = 0
    hyd_n: int = 0
    top_n: int = 0
    inserted: int = 0
    tracking_rows: int = 0
    skipped: bool = False
    skip_reason: str = ""
    elapsed: float = 0.0


@dataclass
class BackfillSummary:
    start: dt.date
    end: dt.date
    scoring_mode: str = "heuristic"
    trading_days_total: int = 0
    trading_days_processed: int = 0
    trading_days_skipped: int = 0
    recommendations_inserted: int = 0
    tracking_rows_added: int = 0
    errors: list[tuple[dt.date, str]] = field(default_factory=list)
    day_results: list[DayResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def success_days(self) -> list[DayResult]:
        return [r for r in self.day_results if not r.skipped]

    def win_rate_by_strategy(self) -> dict[str, float]:
        """Placeholder — full stats from DB via print_stats()."""
        return {}


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _get_trading_days(engine: Engine, start: dt.date, end: dt.date) -> list[dt.date]:
    """Return A-share trading days in [start, end] from trade_cal."""
    sql = text("""
        SELECT cal_date FROM smartmoney.trade_cal
        WHERE exchange = 'SSE' AND is_open
          AND cal_date BETWEEN :start AND :end
        ORDER BY cal_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"start": start, "end": end}).fetchall()
    return [r[0] for r in rows]


def _load_bulk_daily(engine: Engine, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Load slim raw_daily for [start, end] — only OHLCV + vol + pre_close.

    Strategies (sniper / basin / hyd / six_step) need only these columns
    for their indicator calculations.  Moneyflow and daily_basic are NOT
    required and are intentionally excluded to reduce memory footprint.
    """
    sql = text("""
        SELECT ts_code, trade_date,
               open, high, low, close, pre_close,
               vol, amount, pct_chg
        FROM smartmoney.raw_daily
        WHERE trade_date BETWEEN :start AND :end
        ORDER BY ts_code, trade_date
    """)
    return pd.read_sql(sql, engine, params={"start": start, "end": end})


def _compute_weekly_from_daily(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate weekly OHLCV bars from daily data in-memory.

    Mirrors `data.load_weekly_bars` but operates on an already-loaded DataFrame.
    """
    if daily_df.empty:
        return pd.DataFrame()

    needed = ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"]
    available = [c for c in needed if c in daily_df.columns]
    df = daily_df[available].copy()
    if not pd.api.types.is_datetime64_any_dtype(df["trade_date"]):
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["week"] = df["trade_date"].dt.to_period("W-FRI")
    weekly = (
        df.groupby(["ts_code", "week"])
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            vol=("vol", "sum"),
            amount=("amount", "sum"),
            week_end=("trade_date", "max"),
        )
        .reset_index()
    )
    return weekly


# ── Backfill core ─────────────────────────────────────────────────────────────

def run_ningbo_backfill(
    engine: Engine,
    start: dt.date,
    end: dt.date,
    *,
    scoring_mode: str = "heuristic",
    skip_tracking: bool = False,
    on_log: Callable[[str], None] = lambda m: None,
    on_progress: Callable[[int, int, dt.date], None] = lambda i, n, d: None,
) -> BackfillSummary:
    """Backfill ningbo heuristic recommendations + tracking for [start, end].

    Processes trading days in chronological order, in monthly chunks.
    Idempotent: re-running is safe (all DB writes use UPSERT).

    Args:
        engine:        SQLAlchemy engine connected to ifavr / ifavr_test.
        start / end:   Inclusive date range to backfill.
        scoring_mode:  'heuristic' (Phase 2) or 'ml' (Phase 3).
        skip_tracking: If True, skip run_tracking_batch per day (faster
                       but leaves recommendation_outcomes unset until
                       a later pass runs tracking for each day).
        on_log:        Log callback (receives formatted strings).
        on_progress:   Progress callback(i, total, current_date).

    Returns:
        BackfillSummary with per-day results and aggregate stats.
    """
    t_global = time.time()
    summary = BackfillSummary(start=start, end=end, scoring_mode=scoring_mode)

    # ── 1. Get trading calendar ───────────────────────────────────────────────
    trading_days = _get_trading_days(engine, start, end)
    if not trading_days:
        on_log("⚠️  No trading days found in range. Check trade_cal.")
        return summary

    summary.trading_days_total = len(trading_days)
    on_log(f"Backfill: {len(trading_days)} trading days  {trading_days[0]} → {trading_days[-1]}")
    on_log(f"Scoring mode: {scoring_mode}  |  skip_tracking={skip_tracking}")

    # ── 2. Build scorer once ──────────────────────────────────────────────────
    if scoring_mode == "heuristic":
        scorer = HeuristicScorer(version="v1.0")
    else:
        on_log(f"⚠️  scoring_mode='{scoring_mode}' — ML not yet implemented (Phase 3). Aborting.")
        return summary

    # ── 3. Group trading days by (year, month) ────────────────────────────────
    def _ym(d: dt.date) -> tuple[int, int]:
        return (d.year, d.month)

    monthly_chunks: list[list[dt.date]] = []
    for _, days_iter in groupby(trading_days, key=_ym):
        monthly_chunks.append(list(days_iter))

    processed = 0
    total = len(trading_days)

    # ── 4. Process each monthly chunk ─────────────────────────────────────────
    for chunk_idx, chunk_days in enumerate(monthly_chunks):
        chunk_start_day = chunk_days[0]
        chunk_end_day = chunk_days[-1]
        y, m = _ym(chunk_start_day)
        on_log(
            f"\n── Chunk {chunk_idx+1}/{len(monthly_chunks)}: "
            f"{y}-{m:02d}  ({len(chunk_days)} trading days) ──"
        )

        # Bulk-load daily data with 200-day lookback buffer
        bulk_start = chunk_start_day - dt.timedelta(days=LOOKBACK_CALENDAR_DAYS + LOOKBACK_BUFFER_DAYS)
        try:
            t_load = time.time()
            bulk_daily = _load_bulk_daily(engine, bulk_start, chunk_end_day)
            if bulk_daily.empty:
                on_log(f"  ⚠️  bulk load returned empty DataFrame — skipping chunk")
                for d in chunk_days:
                    dr = DayResult(d, skipped=True, skip_reason="empty bulk load")
                    summary.day_results.append(dr)
                    summary.trading_days_skipped += 1
                continue

            # Ensure datetime for fast pd filtering
            bulk_daily["trade_date"] = pd.to_datetime(bulk_daily["trade_date"])

            # ── Pre-compute ALL indicators once for the whole chunk ────────
            # enrich_indicators() inside each strategy detects the sentinel
            # columns and becomes a no-op, so per-day signal loops are fast.
            t_ind = time.time()
            bulk_daily = compute_all_indicators_bulk(bulk_daily)
            on_log(f"  indicators pre-computed [{time.time()-t_ind:.1f}s]")

            weekly_full = _compute_weekly_from_daily(bulk_daily)
            if not weekly_full.empty:
                weekly_full["week_end"] = pd.to_datetime(weekly_full["week_end"])

            n_stocks = bulk_daily["ts_code"].nunique()
            on_log(
                f"  bulk loaded + enriched: {len(bulk_daily):,} rows, {n_stocks:,} stocks  "
                f"({bulk_start} → {chunk_end_day})  "
                f"[{time.time()-t_load:.1f}s total]"
            )
        except Exception as exc:
            on_log(f"  ❌ bulk load failed: {exc}")
            for d in chunk_days:
                dr = DayResult(d, skipped=True, skip_reason=f"bulk load error: {exc}")
                summary.day_results.append(dr)
                summary.errors.append((d, f"bulk load error: {exc}"))
                summary.trading_days_skipped += 1
            continue

        # ── 5. Per-day processing ──────────────────────────────────────────
        for day in chunk_days:
            processed += 1
            on_progress(processed, total, day)
            t_day = time.time()
            dr = DayResult(day)

            # Slice universe for this day
            lookback_start_ts = pd.Timestamp(day - dt.timedelta(days=LOOKBACK_CALENDAR_DAYS))
            day_ts = pd.Timestamp(day)

            universe_slice = bulk_daily[
                (bulk_daily["trade_date"] >= lookback_start_ts) &
                (bulk_daily["trade_date"] <= day_ts)
            ].copy()

            # Convert trade_date back to date objects (strategies expect dt.date)
            universe_slice["trade_date"] = universe_slice["trade_date"].dt.date

            # Verify data exists for this specific day
            day_codes_mask = universe_slice["trade_date"] == day
            if not day_codes_mask.any():
                on_log(f"  ⚠️  {day}: no data for this date in raw_daily — skipping")
                dr.skipped = True
                dr.skip_reason = "no data for date"
                summary.day_results.append(dr)
                summary.trading_days_skipped += 1
                continue

            # Weekly slice for this day
            if not weekly_full.empty:
                weekly_lookback_ts = pd.Timestamp(day - dt.timedelta(weeks=WEEKLY_LOOKBACK_WEEKS))
                weekly_slice = weekly_full[
                    (weekly_full["week_end"] <= day_ts) &
                    (weekly_full["week_end"] >= weekly_lookback_ts)
                ].copy()
                weekly_slice["week_end"] = weekly_slice["week_end"].dt.date
                # Restrict to codes present in this day's universe
                day_codes_set = set(universe_slice["ts_code"].unique())
                weekly_slice = weekly_slice[weekly_slice["ts_code"].isin(day_codes_set)]
            else:
                weekly_slice = pd.DataFrame()

            # ── Run strategies ────────────────────────────────────────────
            try:
                sniper_df = sniper.detect_signals(universe_slice, day)
                basin_df = treasure_basin.detect_signals(universe_slice, day)
                hyd_df = half_year_double.detect_signals(universe_slice, weekly_slice, day)

                dr.sniper_n = len(sniper_df)
                dr.basin_n = len(basin_df)
                dr.hyd_n = len(hyd_df)
            except Exception as exc:
                on_log(f"  ❌ {day}: strategy error: {exc}")
                dr.skipped = True
                dr.skip_reason = f"strategy error: {exc}"
                summary.day_results.append(dr)
                summary.errors.append((day, f"strategy error: {exc}"))
                summary.trading_days_skipped += 1
                continue

            # ── Select top-N ──────────────────────────────────────────────
            candidates = {
                "sniper": sniper_df,
                "treasure_basin": basin_df,
                "half_year_double": hyd_df,
            }
            try:
                top = select_top_n(
                    candidates, scorer, top_n=TOP_N, per_strategy_cap=PER_STRATEGY_CAP
                )
                dr.top_n = len(top)
            except Exception as exc:
                on_log(f"  ❌ {day}: selection error: {exc}")
                dr.skipped = True
                dr.skip_reason = f"selection error: {exc}"
                summary.day_results.append(dr)
                summary.errors.append((day, f"selection error: {exc}"))
                summary.trading_days_skipped += 1
                continue

            # ── Insert recommendations (no LLM in backfill) ───────────────
            n_inserted = 0
            if not top.empty:
                # Ensure llm_narrative column is absent / None for backfill
                if "llm_narrative" not in top.columns:
                    top = top.copy()
                    top["llm_narrative"] = None
                try:
                    n_inserted = insert_recommendations(
                        engine, top, day,
                        scoring_mode=scoring_mode,
                        param_version=f"{scoring_mode}_v1.0",
                    )
                    dr.inserted = n_inserted
                    summary.recommendations_inserted += n_inserted
                except Exception as exc:
                    on_log(f"  ❌ {day}: insert error: {exc}")
                    summary.errors.append((day, f"insert error: {exc}"))

            # ── Tracking batch ────────────────────────────────────────────
            if not skip_tracking:
                try:
                    track_result = run_tracking_batch(engine, day)
                    dr.tracking_rows = track_result.n_tracking_rows_inserted
                    summary.tracking_rows_added += track_result.n_tracking_rows_inserted
                except Exception as exc:
                    on_log(f"  ⚠️  {day}: tracking error (non-fatal): {exc}")

            dr.elapsed = time.time() - t_day
            summary.day_results.append(dr)
            summary.trading_days_processed += 1

            # Log every 20 days
            if processed % 20 == 0 or day == trading_days[-1]:
                on_log(
                    f"  [{processed}/{total}] {day}  "
                    f"sniper={dr.sniper_n} basin={dr.basin_n} hyd={dr.hyd_n} "
                    f"→ top={dr.top_n} inserted={dr.inserted}  "
                    f"[{dr.elapsed:.1f}s/day]"
                )

    summary.elapsed_seconds = time.time() - t_global
    return summary


# ── Statistics ─────────────────────────────────────────────────────────────────

def fetch_backfill_stats(engine: Engine, scoring_mode: str = "heuristic") -> pd.DataFrame:
    """Query ningbo DB tables and return a strategy×year summary DataFrame.

    Returns columns:
        scoring_mode, strategy, year, total, take_profit, stop_loss,
        expired, in_progress, win_rate, loss_rate, avg_final_return,
        avg_peak_return, avg_trough_return
    """
    sql = text("""
        SELECT
            o.scoring_mode,
            r.strategy,
            EXTRACT(YEAR FROM r.rec_date)::int                     AS year,
            COUNT(*)                                                AS total,
            COUNT(*) FILTER (WHERE o.outcome_status = 'take_profit') AS take_profit,
            COUNT(*) FILTER (WHERE o.outcome_status = 'stop_loss')   AS stop_loss,
            COUNT(*) FILTER (WHERE o.outcome_status = 'expired')     AS expired,
            COUNT(*) FILTER (WHERE o.outcome_status = 'in_progress') AS in_progress,
            AVG(o.final_cum_return) FILTER (WHERE o.outcome_status != 'in_progress')
                                                                    AS avg_final_return,
            AVG(o.peak_cum_return)  FILTER (WHERE o.outcome_status != 'in_progress')
                                                                    AS avg_peak_return,
            AVG(o.trough_cum_return) FILTER (WHERE o.outcome_status != 'in_progress')
                                                                    AS avg_trough_return,
            AVG(o.outcome_track_day) FILTER (WHERE o.outcome_status != 'in_progress')
                                                                    AS avg_track_day
        FROM ningbo.recommendation_outcomes o
        JOIN ningbo.recommendations_daily r
          ON r.rec_date = o.rec_date
         AND r.ts_code   = o.ts_code
         AND r.strategy  = o.strategy
         AND r.scoring_mode = o.scoring_mode
        WHERE o.scoring_mode = :sm
        GROUP BY o.scoring_mode, r.strategy, year
        ORDER BY r.strategy, year
    """)
    df = pd.read_sql(sql, engine, params={"sm": scoring_mode})
    if df.empty:
        return df

    df["win_rate"]  = df["take_profit"] / df["total"]
    df["loss_rate"] = df["stop_loss"]   / df["total"]
    df["expired_rate"] = df["expired"]  / df["total"]
    return df


def fetch_overall_stats(engine: Engine, scoring_mode: str = "heuristic") -> pd.DataFrame:
    """Aggregate stats across all years per strategy."""
    sql = text("""
        SELECT
            o.scoring_mode,
            r.strategy,
            COUNT(*)                                                AS total,
            COUNT(*) FILTER (WHERE o.outcome_status = 'take_profit') AS take_profit,
            COUNT(*) FILTER (WHERE o.outcome_status = 'stop_loss')   AS stop_loss,
            COUNT(*) FILTER (WHERE o.outcome_status = 'expired')     AS expired,
            COUNT(*) FILTER (WHERE o.outcome_status = 'in_progress') AS in_progress,
            AVG(o.final_cum_return) FILTER (WHERE o.outcome_status != 'in_progress')
                                                                    AS avg_final_return,
            AVG(o.peak_cum_return)  FILTER (WHERE o.outcome_status != 'in_progress')
                                                                    AS avg_peak_return,
            AVG(o.trough_cum_return) FILTER (WHERE o.outcome_status != 'in_progress')
                                                                    AS avg_trough_return,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY o.final_cum_return)
                FILTER (WHERE o.outcome_status != 'in_progress')
                                                                    AS median_final_return,
            AVG(o.outcome_track_day) FILTER (WHERE o.outcome_status != 'in_progress')
                                                                    AS avg_track_day
        FROM ningbo.recommendation_outcomes o
        JOIN ningbo.recommendations_daily r
          ON r.rec_date = o.rec_date
         AND r.ts_code   = o.ts_code
         AND r.strategy  = o.strategy
         AND r.scoring_mode = o.scoring_mode
        WHERE o.scoring_mode = :sm
        GROUP BY o.scoring_mode, r.strategy
        ORDER BY r.strategy
    """)
    df = pd.read_sql(sql, engine, params={"sm": scoring_mode})
    if df.empty:
        return df
    df["win_rate"]  = df["take_profit"] / df["total"]
    df["loss_rate"] = df["stop_loss"]   / df["total"]
    df["expired_rate"] = df["expired"]  / df["total"]
    return df


def run_bulk_tracking_sql(
    engine: Engine,
    start: dt.date,
    end: dt.date,
    *,
    scoring_mode: str = "heuristic",
    on_log: Callable[[str], None] = lambda m: None,
) -> dict:
    """Compute recommendation_tracking + recommendation_outcomes in bulk SQL.

    Much faster than the per-day Python tracking loop used in Phase 1
    (run_tracking_batch).  Designed for post-backfill use:
      1. Run backfill with skip_tracking=True (parallel across years).
      2. Call run_bulk_tracking_sql() once to populate all tracking rows
         and outcomes in a few SQL statements.

    Returns dict with keys: tracking_rows_inserted, outcomes_upserted.
    """
    import time

    # Warmup: need 24 trading days before earliest possible tracking date.
    # The earliest tracking date = start + 1 trading day ≈ start + 1 calendar day.
    # Use 60 calendar days lookback to ensure full MA24 warmup.
    ma24_start = start - dt.timedelta(days=60)
    # Latest tracking date = end + 15 trading days ≈ end + 25 calendar days.
    tracking_end = end + dt.timedelta(days=30)

    on_log(f"Bulk tracking SQL: recs [{start} → {end}], ma24_start={ma24_start}")

    # ── Step 1: Insert tracking rows ──────────────────────────────────────────
    t0 = time.time()
    on_log("  Step 1: Computing tracking rows (MA24 window + close join)…")

    tracking_sql = text("""
        WITH
        -- Pre-compute MA24 for all (ts_code, trade_date) in the needed range.
        -- Uses a window function — efficient set-based operation.
        ma24_all AS (
            SELECT
                ts_code,
                trade_date,
                close,
                AVG(close) OVER (
                    PARTITION BY ts_code
                    ORDER BY trade_date
                    ROWS BETWEEN 23 PRECEDING AND CURRENT ROW
                ) AS ma24,
                COUNT(*) OVER (
                    PARTITION BY ts_code
                    ORDER BY trade_date
                    ROWS BETWEEN 23 PRECEDING AND CURRENT ROW
                ) AS bars_count
            FROM smartmoney.raw_daily
            WHERE trade_date >= :ma24_start
              AND trade_date <= :tracking_end
        ),
        -- For each recommendation, enumerate its next 15 A-share trading days
        -- using a correlated LATERAL query against trade_cal.
        tracking_pairs AS (
            SELECT
                r.rec_date,
                r.ts_code,
                r.strategy,
                r.scoring_mode,
                r.rec_price,
                tc.cal_date   AS track_date,
                tc.track_day
            FROM ningbo.recommendations_daily r
            CROSS JOIN LATERAL (
                SELECT cal_date,
                       ROW_NUMBER() OVER (ORDER BY cal_date) AS track_day
                FROM smartmoney.trade_cal
                WHERE exchange = 'SSE'
                  AND is_open
                  AND cal_date > r.rec_date
                  AND cal_date <= :tracking_end
                ORDER BY cal_date
                LIMIT 15
            ) tc
            WHERE r.scoring_mode = :scoring_mode
              AND r.rec_date BETWEEN :start_date AND :end_date
        ),
        -- Join tracking pairs with pre-computed MA24
        tracking_data AS (
            SELECT
                tp.rec_date,
                tp.ts_code,
                tp.strategy,
                tp.scoring_mode,
                tp.track_day,
                tp.track_date,
                m.close                                              AS close_price,
                (m.close - tp.rec_price) / tp.rec_price             AS cum_return,
                m.ma24,
                CASE WHEN m.bars_count >= 24 THEN (m.close < m.ma24)
                     ELSE NULL END                                   AS below_ma24
            FROM tracking_pairs tp
            JOIN ma24_all m
              ON m.ts_code    = tp.ts_code
             AND m.trade_date = tp.track_date
            WHERE m.close IS NOT NULL
        )
        INSERT INTO ningbo.recommendation_tracking
            (rec_date, ts_code, strategy, scoring_mode,
             track_day, track_date, close_price, cum_return, ma24, below_ma24)
        SELECT
            rec_date, ts_code, strategy, scoring_mode,
            track_day, track_date, close_price, cum_return, ma24, below_ma24
        FROM tracking_data
        ON CONFLICT (rec_date, ts_code, strategy, scoring_mode, track_day)
        DO UPDATE SET
            track_date  = EXCLUDED.track_date,
            close_price = EXCLUDED.close_price,
            cum_return  = EXCLUDED.cum_return,
            ma24        = EXCLUDED.ma24,
            below_ma24  = EXCLUDED.below_ma24
    """)

    with engine.begin() as conn:
        result = conn.execute(tracking_sql, {
            "ma24_start":    ma24_start,
            "tracking_end":  tracking_end,
            "scoring_mode":  scoring_mode,
            "start_date":    start,
            "end_date":      end,
        })
        tracking_rows_inserted = result.rowcount

    on_log(f"  → {tracking_rows_inserted:,} tracking rows upserted  [{time.time()-t0:.1f}s]")

    # ── Step 2: Compute outcomes from accumulated tracking rows ───────────────
    t1 = time.time()
    on_log("  Step 2: Computing outcomes (stop_loss / take_profit / expired)…")

    outcomes_sql = text("""
        WITH
        -- Aggregate tracking rows per recommendation
        first_sl AS (
            SELECT rec_date, ts_code, strategy, scoring_mode,
                   MIN(track_day) AS sl_day
            FROM ningbo.recommendation_tracking
            WHERE scoring_mode = :scoring_mode
              AND rec_date BETWEEN :start_date AND :end_date
              AND below_ma24 = TRUE
            GROUP BY rec_date, ts_code, strategy, scoring_mode
        ),
        first_tp AS (
            SELECT rec_date, ts_code, strategy, scoring_mode,
                   MIN(track_day) AS tp_day
            FROM ningbo.recommendation_tracking
            WHERE scoring_mode = :scoring_mode
              AND rec_date BETWEEN :start_date AND :end_date
              AND cum_return >= 0.20
            GROUP BY rec_date, ts_code, strategy, scoring_mode
        ),
        agg AS (
            SELECT rec_date, ts_code, strategy, scoring_mode,
                   MAX(track_day)   AS max_track_day,
                   MAX(cum_return)  AS peak_cum_return,
                   MIN(cum_return)  AS trough_cum_return
            FROM ningbo.recommendation_tracking
            WHERE scoring_mode = :scoring_mode
              AND rec_date BETWEEN :start_date AND :end_date
            GROUP BY rec_date, ts_code, strategy, scoring_mode
        ),
        -- Final cumulative return = cum_return on the last tracked day
        last_row AS (
            SELECT DISTINCT ON (rec_date, ts_code, strategy, scoring_mode)
                   rec_date, ts_code, strategy, scoring_mode,
                   cum_return AS final_cum_return,
                   track_date AS last_track_date
            FROM ningbo.recommendation_tracking
            WHERE scoring_mode = :scoring_mode
              AND rec_date BETWEEN :start_date AND :end_date
            ORDER BY rec_date, ts_code, strategy, scoring_mode, track_day DESC
        ),
        -- Determine terminal state (priority: stop_loss > take_profit > expired)
        outcome_core AS (
            SELECT
                a.rec_date, a.ts_code, a.strategy, a.scoring_mode,
                a.max_track_day, a.peak_cum_return, a.trough_cum_return,
                lr.final_cum_return,
                sl.sl_day, tp.tp_day,
                CASE
                    WHEN sl.sl_day IS NOT NULL
                     AND (tp.tp_day IS NULL OR sl.sl_day <= tp.tp_day)
                        THEN 'stop_loss'
                    WHEN tp.tp_day IS NOT NULL
                        THEN 'take_profit'
                    WHEN a.max_track_day >= 15
                        THEN 'expired'
                    ELSE 'in_progress'
                END AS outcome_status,
                CASE
                    WHEN sl.sl_day IS NOT NULL
                     AND (tp.tp_day IS NULL OR sl.sl_day <= tp.tp_day)
                        THEN sl.sl_day
                    WHEN tp.tp_day IS NOT NULL THEN tp.tp_day
                    WHEN a.max_track_day >= 15 THEN a.max_track_day
                    ELSE NULL
                END AS outcome_track_day
            FROM agg a
            LEFT JOIN first_sl sl USING (rec_date, ts_code, strategy, scoring_mode)
            LEFT JOIN first_tp tp USING (rec_date, ts_code, strategy, scoring_mode)
            LEFT JOIN last_row lr USING (rec_date, ts_code, strategy, scoring_mode)
        ),
        -- Resolve outcome_track_day → actual outcome_date via tracking table
        outcome_with_date AS (
            SELECT o.*, t.track_date AS outcome_date
            FROM outcome_core o
            LEFT JOIN ningbo.recommendation_tracking t
              ON  t.rec_date    = o.rec_date
              AND t.ts_code     = o.ts_code
              AND t.strategy    = o.strategy
              AND t.scoring_mode= o.scoring_mode
              AND t.track_day   = o.outcome_track_day
        )
        INSERT INTO ningbo.recommendation_outcomes
            (rec_date, ts_code, strategy, scoring_mode,
             outcome_status, outcome_track_day, outcome_date,
             final_cum_return, peak_cum_return, trough_cum_return, updated_at)
        SELECT
            rec_date, ts_code, strategy, scoring_mode,
            outcome_status, outcome_track_day, outcome_date,
            final_cum_return, peak_cum_return, trough_cum_return,
            NOW()
        FROM outcome_with_date
        ON CONFLICT (rec_date, ts_code, strategy, scoring_mode)
        DO UPDATE SET
            outcome_status    = EXCLUDED.outcome_status,
            outcome_track_day = EXCLUDED.outcome_track_day,
            outcome_date      = EXCLUDED.outcome_date,
            final_cum_return  = EXCLUDED.final_cum_return,
            peak_cum_return   = EXCLUDED.peak_cum_return,
            trough_cum_return = EXCLUDED.trough_cum_return,
            updated_at        = NOW()
    """)

    with engine.begin() as conn:
        result = conn.execute(outcomes_sql, {
            "scoring_mode": scoring_mode,
            "start_date":   start,
            "end_date":     end,
        })
        outcomes_upserted = result.rowcount

    on_log(f"  → {outcomes_upserted:,} outcomes upserted  [{time.time()-t1:.1f}s]")
    on_log(f"Bulk tracking complete in {time.time()-t0:.1f}s total")

    return {
        "tracking_rows_inserted": tracking_rows_inserted,
        "outcomes_upserted": outcomes_upserted,
    }


def print_backfill_stats(engine: Engine, scoring_mode: str = "heuristic") -> None:
    """Pretty-print strategy performance stats to console."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    overall = fetch_overall_stats(engine, scoring_mode)
    by_year = fetch_backfill_stats(engine, scoring_mode)

    if overall.empty:
        console.print(f"[yellow]No data for scoring_mode={scoring_mode}.[/yellow]")
        return

    # ── Overall summary table ────────────────────────────────────────────────
    console.print(f"\n[bold cyan]Ningbo Strategy Stats — {scoring_mode} (all years)[/bold cyan]")
    t = Table(show_header=True, header_style="bold")
    t.add_column("Strategy", style="bold")
    t.add_column("Total", justify="right")
    t.add_column("Win%", justify="right", style="green")
    t.add_column("Loss%", justify="right", style="red")
    t.add_column("Exp%", justify="right")
    t.add_column("Avg Ret", justify="right")
    t.add_column("Median Ret", justify="right")
    t.add_column("Avg Peak", justify="right", style="green")
    t.add_column("Avg Trough", justify="right", style="red")
    t.add_column("Avg Days", justify="right")

    for _, r in overall.iterrows():
        def _pct(v): return f"{v*100:.1f}%" if pd.notna(v) else "—"
        def _fp(v):  return f"{v*100:+.1f}%" if pd.notna(v) else "—"
        def _f1(v):  return f"{v:.1f}" if pd.notna(v) else "—"
        t.add_row(
            r["strategy"],
            str(int(r["total"])),
            _pct(r.get("win_rate")),
            _pct(r.get("loss_rate")),
            _pct(r.get("expired_rate")),
            _fp(r.get("avg_final_return")),
            _fp(r.get("median_final_return")),
            _fp(r.get("avg_peak_return")),
            _fp(r.get("avg_trough_return")),
            _f1(r.get("avg_track_day")),
        )
    console.print(t)

    if by_year.empty:
        return

    # ── Per-year breakdown (compact) ─────────────────────────────────────────
    console.print(f"\n[bold cyan]Year × Strategy Breakdown[/bold cyan]")
    t2 = Table(show_header=True, header_style="bold", min_width=90)
    t2.add_column("Year", justify="right")
    t2.add_column("Strategy")
    t2.add_column("N", justify="right")
    t2.add_column("Win%", justify="right", style="green")
    t2.add_column("Loss%", justify="right", style="red")
    t2.add_column("Avg Ret", justify="right")
    t2.add_column("Avg Peak", justify="right")

    for _, r in by_year.iterrows():
        def _pct(v): return f"{v*100:.1f}%" if pd.notna(v) else "—"
        def _fp(v):  return f"{v*100:+.1f}%" if pd.notna(v) else "—"
        t2.add_row(
            str(int(r["year"])),
            r["strategy"],
            str(int(r["total"])),
            _pct(r.get("win_rate")),
            _pct(r.get("loss_rate")),
            _fp(r.get("avg_final_return")),
            _fp(r.get("avg_peak_return")),
        )
    console.print(t2)
