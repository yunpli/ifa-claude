"""Data loaders for standalone Stock Edge tuning jobs."""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.stock.data.tushare_backfill import backfill_core_stock_window


def load_daily_bars_for_tuning(
    engine: Engine,
    *,
    ts_code: str,
    as_of_date: dt.date,
    lookback_rows: int = 900,
) -> pd.DataFrame:
    """Load PIT daily bars from local PostgreSQL for one stock."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ts_code, trade_date, open, high, low, close, amount
                FROM smartmoney.raw_daily
                WHERE ts_code = :ts_code AND trade_date <= :as_of
                ORDER BY trade_date DESC
                LIMIT :limit
            """),
            {"ts_code": ts_code, "as_of": as_of_date, "limit": lookback_rows},
        ).mappings().all()
    return pd.DataFrame([dict(row) for row in reversed(rows)])


def load_top_liquidity_universe(
    engine: Engine,
    *,
    as_of_date: dt.date,
    lookback_days: int = 20,
    limit: int = 500,
) -> list[str]:
    """Select a high-liquidity universe from local daily bars."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                WITH recent AS (
                    SELECT ts_code, AVG(amount) AS avg_amount
                    FROM smartmoney.raw_daily
                    WHERE trade_date <= :as_of
                      AND trade_date >= :as_of - CAST(:lookback AS INTEGER)
                    GROUP BY ts_code
                )
                SELECT ts_code
                FROM recent
                WHERE avg_amount IS NOT NULL
                ORDER BY avg_amount DESC
                LIMIT :limit
            """),
            {"as_of": as_of_date, "lookback": lookback_days, "limit": limit},
        ).scalars().all()
    return [str(row) for row in rows]


def load_universe_daily_bars(
    engine: Engine,
    *,
    ts_codes: list[str],
    as_of_date: dt.date,
    lookback_rows: int = 900,
) -> dict[str, pd.DataFrame]:
    """Load daily bars for a list of stocks."""
    return {
        ts_code: load_daily_bars_for_tuning(
            engine,
            ts_code=ts_code,
            as_of_date=as_of_date,
            lookback_rows=lookback_rows,
        )
        for ts_code in ts_codes
    }


def load_universe_daily_bars_with_backfill(
    engine: Engine,
    *,
    ts_codes: list[str],
    as_of_date: dt.date,
    lookback_rows: int = 900,
    min_history_rows: int = 360,
    backfill_short_history: bool = True,
    max_backfill_stocks: int = 50,
    on_log: Callable[[str], None] | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Load universe bars and optionally backfill selected short-history gaps.

    This keeps weekend/global preset jobs local-first while still honoring the
    Stock Edge rule that missing local data should be pulled from TuShare when
    it is available. The backfill is capped so an overnight preset cannot turn
    into an unbounded full-market crawl by accident.
    """
    bars = load_universe_daily_bars(engine, ts_codes=ts_codes, as_of_date=as_of_date, lookback_rows=lookback_rows)
    short_codes = [code for code, frame in bars.items() if len(frame) < min_history_rows]
    meta: dict[str, Any] = {
        "short_history_count": len(short_codes),
        "backfill_attempted": 0,
        "backfill_errors": 0,
        "backfill_capped": max(0, len(short_codes) - max_backfill_stocks),
    }
    if not backfill_short_history or not short_codes:
        return bars, meta

    for ts_code in short_codes[: max(0, max_backfill_stocks)]:
        if on_log:
            on_log(f"  [backfill] {ts_code} short history {len(bars[ts_code])}/{min_history_rows}; trying TuShare")
        result = backfill_core_stock_window(
            engine,
            ts_code,
            as_of_date,
            daily_rows=lookback_rows,
            basic_rows=20,
            moneyflow_rows=20,
        )
        meta["backfill_attempted"] += 1
        meta["backfill_errors"] += len(result.errors)
        bars[ts_code] = load_daily_bars_for_tuning(
            engine,
            ts_code=ts_code,
            as_of_date=as_of_date,
            lookback_rows=lookback_rows,
        )
    meta["short_history_after_backfill"] = sum(1 for frame in bars.values() if len(frame) < min_history_rows)
    return bars, meta
