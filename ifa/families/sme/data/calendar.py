"""Trading date helpers for SME."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text


def parse_date(value: str | None) -> dt.date | None:
    if not value or value == "auto":
        return None
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def latest_trade_date(engine) -> dt.date:
    with engine.connect() as conn:
        d = conn.execute(text("""
            SELECT max(trade_date)
            FROM (
                SELECT trade_date FROM smartmoney.raw_moneyflow
                UNION
                SELECT trade_date FROM smartmoney.raw_daily
                UNION
                SELECT trade_date FROM smartmoney.raw_daily_basic
            ) d
        """)).scalar_one()
    if d is None:
        raise RuntimeError("No smartmoney source trade dates found")
    return d


def trading_dates(engine, start: dt.date, end: dt.date) -> list[dt.date]:
    """Return canonical SME trading dates.

    SME treats "previous day", "next day", and rolling windows as trading-day
    concepts. The calendar is therefore the union of core daily source dates,
    not any single source table. A single-source calendar silently dropped
    2021-05-10 because `raw_daily` was missing while moneyflow/basic existed.
    """
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT trade_date
            FROM (
                SELECT trade_date FROM smartmoney.raw_moneyflow
                UNION
                SELECT trade_date FROM smartmoney.raw_daily
                UNION
                SELECT trade_date FROM smartmoney.raw_daily_basic
            ) d
            WHERE trade_date BETWEEN :start AND :end
            ORDER BY trade_date
        """), {"start": start, "end": end}).fetchall()
    return [r[0] for r in rows]


def previous_trade_date(engine, trade_date: dt.date) -> dt.date | None:
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT max(trade_date)
            FROM (
                SELECT trade_date FROM smartmoney.raw_moneyflow
                UNION
                SELECT trade_date FROM smartmoney.raw_daily
                UNION
                SELECT trade_date FROM smartmoney.raw_daily_basic
            ) d
            WHERE trade_date < :d
        """), {"d": trade_date}).scalar_one()


def next_trade_date(engine, trade_date: dt.date) -> dt.date | None:
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT min(trade_date)
            FROM (
                SELECT trade_date FROM smartmoney.raw_moneyflow
                UNION
                SELECT trade_date FROM smartmoney.raw_daily
                UNION
                SELECT trade_date FROM smartmoney.raw_daily_basic
            ) d
            WHERE trade_date > :d
        """), {"d": trade_date}).scalar_one()


def nth_next_trade_date(engine, trade_date: dt.date, n: int) -> dt.date | None:
    if n <= 0:
        return trade_date
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT trade_date
            FROM (
                SELECT trade_date,
                       ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
                FROM (
                    SELECT trade_date FROM smartmoney.raw_moneyflow
                    UNION
                    SELECT trade_date FROM smartmoney.raw_daily
                    UNION
                    SELECT trade_date FROM smartmoney.raw_daily_basic
                ) d
                WHERE trade_date > :d
            ) ranked
            WHERE rn = :n
        """), {"d": trade_date, "n": n}).scalar_one_or_none()
