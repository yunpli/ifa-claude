"""Trading-calendar utilities backed by smartmoney.trade_cal.

The table is a local mirror of TuShare's trade_cal API (SSE exchange).
It's a slow-changing reference — refresh monthly or quarterly.

Public API
----------
    is_trading_day(engine, date) -> bool
    prev_trading_day(engine, date) -> datetime.date
    next_trading_day(engine, date) -> datetime.date
    refresh_trade_cal(engine, client, *, start_year, end_year) -> int
    today_bjt() -> datetime.date
"""
from __future__ import annotations

import datetime as dt
import zoneinfo

import pandas as pd
from sqlalchemy import Engine, text

_BJT = zoneinfo.ZoneInfo("Asia/Shanghai")
_SSE = "SSE"
_TABLE = "smartmoney.trade_cal"


# ─── Timezone helper ──────────────────────────────────────────────────────────

def today_bjt() -> dt.date:
    """Current date in Beijing time (UTC+8)."""
    return dt.datetime.now(tz=_BJT).date()


# ─── Query helpers ────────────────────────────────────────────────────────────

def is_trading_day(engine: Engine, date: dt.date, *, exchange: str = _SSE) -> bool:
    """Return True if *date* is a trading day according to the local mirror.

    Raises RuntimeError if the table is empty / unpopulated (run --refresh).
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT is_open FROM {_TABLE} WHERE cal_date = :d AND exchange = :ex"),
            {"d": date, "ex": exchange},
        ).fetchone()
    if row is None:
        raise RuntimeError(
            f"No trade_cal record for {date} (exchange={exchange}). "
            "Run: uv run python scripts/is_trading_day.py --refresh"
        )
    return bool(row[0])


def prev_trading_day(engine: Engine, date: dt.date, *, exchange: str = _SSE) -> dt.date:
    """Return the most recent trading day strictly before *date*."""
    with engine.connect() as conn:
        row = conn.execute(
            text(f"""
                SELECT cal_date FROM {_TABLE}
                WHERE exchange = :ex AND is_open AND cal_date < :d
                ORDER BY cal_date DESC LIMIT 1
            """),
            {"d": date, "ex": exchange},
        ).fetchone()
    if row is None:
        raise RuntimeError(f"No previous trading day found before {date}.")
    return row[0]


def next_trading_day(engine: Engine, date: dt.date, *, exchange: str = _SSE) -> dt.date:
    """Return the next trading day strictly after *date*."""
    with engine.connect() as conn:
        row = conn.execute(
            text(f"""
                SELECT cal_date FROM {_TABLE}
                WHERE exchange = :ex AND is_open AND cal_date > :d
                ORDER BY cal_date LIMIT 1
            """),
            {"d": date, "ex": exchange},
        ).fetchone()
    if row is None:
        raise RuntimeError(f"No next trading day found after {date}.")
    return row[0]


def trading_days_between(
    engine: Engine, start: dt.date, end: dt.date, *, exchange: str = _SSE
) -> list[dt.date]:
    """Return sorted list of trading days in [start, end] inclusive."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT cal_date FROM {_TABLE}
                WHERE exchange = :ex AND is_open
                  AND cal_date BETWEEN :s AND :e
                ORDER BY cal_date
            """),
            {"ex": exchange, "s": start, "e": end},
        ).fetchall()
    return [r[0] for r in rows]


# ─── ETL ──────────────────────────────────────────────────────────────────────

def refresh_trade_cal(
    engine: Engine,
    client,  # TuShareClient
    *,
    start_year: int = 2015,
    end_year: int | None = None,
    exchange: str = _SSE,
) -> int:
    """Fetch trading calendar from TuShare and upsert into local table.

    Returns the number of rows upserted.
    Designed to be idempotent — safe to re-run at any time.
    """
    if end_year is None:
        end_year = today_bjt().year + 1  # include next year (holidays already published)

    start_str = f"{start_year}0101"
    end_str = f"{end_year}1231"

    df: pd.DataFrame = client.call(
        "trade_cal",
        exchange=exchange,
        start_date=start_str,
        end_date=end_str,
    )
    if df is None or df.empty:
        return 0

    # Normalise: cal_date as Python date, is_open as bool
    df = df[["cal_date", "is_open"]].copy()
    df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date
    df["is_open"] = df["is_open"].astype(int).astype(bool)
    df["exchange"] = exchange

    rows = df[["cal_date", "exchange", "is_open"]].to_dict("records")

    upsert_sql = text(f"""
        INSERT INTO {_TABLE} (cal_date, exchange, is_open)
        VALUES (:cal_date, :exchange, :is_open)
        ON CONFLICT (cal_date, exchange) DO UPDATE SET is_open = EXCLUDED.is_open
    """)

    with engine.begin() as conn:
        conn.execute(upsert_sql, rows)

    return len(rows)
