"""Data loading layer for ningbo.

Reads from smartmoney.raw_* tables via JOIN. Does not own any raw data.

Public API:
    load_universe(engine, on_date, lookback_days) -> pd.DataFrame
        Wide daily OHLCV + basics + moneyflow for all A-shares
        on the trading days in [on_date - lookback_days, on_date].

    load_close_for_tracking(engine, ts_codes, dates) -> pd.DataFrame
        Close prices for tracking_batch — many (ts_code, date) pairs.

    load_index_daily(engine, on_date, lookback_days) -> pd.DataFrame
        Index-level OHLCV for market regime context.

    load_weekly_bars(engine, ts_codes, end_date, lookback_weeks) -> pd.DataFrame
        Weekly bars aggregated from raw_daily (Mon-Fri close), used by
        半年翻倍 strategy (周线共振).
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

import pandas as pd
from sqlalchemy import Engine, text


def load_universe(
    engine: Engine,
    on_date: dt.date,
    lookback_days: int = 60,
) -> pd.DataFrame:
    """Load wide daily data for the full A-share universe.

    Returns DataFrame with columns:
        ts_code, trade_date, open, high, low, close, vol, amount,
        pct_chg, pre_close,
        turnover_rate, total_mv, circ_mv,
        net_mf_amount, buy_elg_amount, sell_elg_amount,
        buy_lg_amount, sell_lg_amount

    `lookback_days` is in CALENDAR days (not trading days).  The caller
    is responsible for slicing by trading-day count after load.
    """
    start_date = on_date - dt.timedelta(days=lookback_days)
    sql = text("""
        SELECT
            d.ts_code, d.trade_date,
            d.open, d.high, d.low, d.close, d.pre_close,
            d.vol, d.amount, d.pct_chg,
            db.turnover_rate, db.total_mv, db.circ_mv,
            mf.net_mf_amount,
            mf.buy_elg_amount, mf.sell_elg_amount,
            mf.buy_lg_amount, mf.sell_lg_amount
        FROM smartmoney.raw_daily d
        LEFT JOIN smartmoney.raw_daily_basic db
               ON d.ts_code = db.ts_code AND d.trade_date = db.trade_date
        LEFT JOIN smartmoney.raw_moneyflow mf
               ON d.ts_code = mf.ts_code AND d.trade_date = mf.trade_date
        WHERE d.trade_date BETWEEN :start AND :end
        ORDER BY d.ts_code, d.trade_date
    """)
    return pd.read_sql(sql, engine, params={"start": start_date, "end": on_date})


def load_close_for_tracking(
    engine: Engine,
    ts_codes: Iterable[str],
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Load close prices for tracking batch."""
    codes = list(ts_codes)
    if not codes:
        return pd.DataFrame(columns=["ts_code", "trade_date", "close"])
    sql = text("""
        SELECT ts_code, trade_date, close
        FROM smartmoney.raw_daily
        WHERE ts_code = ANY(:codes)
          AND trade_date BETWEEN :start AND :end
        ORDER BY ts_code, trade_date
    """)
    return pd.read_sql(sql, engine, params={"codes": codes, "start": start_date, "end": end_date})


def load_index_daily(
    engine: Engine,
    on_date: dt.date,
    lookback_days: int = 60,
    index_codes: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Load index OHLCV for market regime context."""
    start_date = on_date - dt.timedelta(days=lookback_days)
    if index_codes is None:
        index_codes = ["000001.SH", "399001.SZ", "399006.SZ", "000688.SH"]
    sql = text("""
        SELECT ts_code, trade_date, open, high, low, close, vol, amount, pct_chg
        FROM smartmoney.raw_index_daily
        WHERE ts_code = ANY(:codes)
          AND trade_date BETWEEN :start AND :end
        ORDER BY ts_code, trade_date
    """)
    return pd.read_sql(sql, engine, params={"codes": list(index_codes), "start": start_date, "end": on_date})


def load_weekly_bars(
    engine: Engine,
    ts_codes: Iterable[str],
    end_date: dt.date,
    lookback_weeks: int = 60,
) -> pd.DataFrame:
    """Aggregate weekly OHLCV bars from raw_daily (Mon-Fri).

    Used by 半年翻倍 strategy for weekly MA/MACD computation.
    """
    codes = list(ts_codes)
    if not codes:
        return pd.DataFrame()
    start_date = end_date - dt.timedelta(weeks=lookback_weeks + 4)  # buffer
    sql = text("""
        SELECT ts_code, trade_date, open, high, low, close, vol, amount
        FROM smartmoney.raw_daily
        WHERE ts_code = ANY(:codes)
          AND trade_date BETWEEN :start AND :end
        ORDER BY ts_code, trade_date
    """)
    df = pd.read_sql(sql, engine, params={"codes": codes, "start": start_date, "end": end_date})
    if df.empty:
        return df
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
