"""Minimal Tushare backfill helpers for Stock Edge.

This module intentionally reuses SmartMoney's raw fetchers. They already know
the canonical local table schemas and upsert rules. Stock Edge only decides
which small date window needs to be filled.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.calendar import trading_days_between
from ifa.core.tushare import TuShareClient
from ifa.families.smartmoney.etl.raw_fetchers import (
    fetch_raw_daily,
    fetch_raw_daily_basic,
    fetch_raw_moneyflow,
)


@dataclass(frozen=True)
class BackfillResult:
    requested_dates: list[dt.date]
    fetched_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def attempted(self) -> bool:
        return bool(self.requested_dates)


def recent_trading_dates(engine: Engine, as_of: dt.date, *, n: int) -> list[dt.date]:
    """Return the latest *n* local trading days up to and including *as_of*."""
    start = as_of - dt.timedelta(days=max(30, n * 3))
    days = trading_days_between(engine, start, as_of)
    return days[-n:]


def backfill_core_stock_window(
    engine: Engine,
    ts_code: str,
    as_of: dt.date,
    *,
    daily_rows: int = 60,
    basic_rows: int = 7,
    moneyflow_rows: int = 7,
    client: TuShareClient | None = None,
) -> BackfillResult:
    """Backfill missing core dates for one target stock.

    The underlying Tushare endpoints are date-based and return full-market rows.
    That is acceptable for the first functional version because the window is
    small and the data becomes reusable by every family.
    """
    needed = sorted(
        set(_missing_dates(engine, "raw_daily", ts_code, as_of, daily_rows))
        | set(_missing_dates(engine, "raw_daily_basic", ts_code, as_of, basic_rows))
        | set(_missing_dates(engine, "raw_moneyflow", ts_code, as_of, moneyflow_rows))
    )
    counts = {"raw_daily": 0, "raw_daily_basic": 0, "raw_moneyflow": 0}
    errors: list[str] = []
    if not needed:
        return BackfillResult(requested_dates=[], fetched_counts=counts, errors=errors)
    client = client or TuShareClient()
    for day in needed:
        try:
            counts["raw_daily"] += fetch_raw_daily(client, engine, trade_date=day)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"raw_daily {day}: {type(exc).__name__}: {exc}")
        try:
            counts["raw_daily_basic"] += fetch_raw_daily_basic(client, engine, trade_date=day)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"raw_daily_basic {day}: {type(exc).__name__}: {exc}")
        try:
            counts["raw_moneyflow"] += fetch_raw_moneyflow(client, engine, trade_date=day)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"raw_moneyflow {day}: {type(exc).__name__}: {exc}")
    return BackfillResult(requested_dates=needed, fetched_counts=counts, errors=errors)


def _missing_dates(engine: Engine, table: str, ts_code: str, as_of: dt.date, rows: int) -> list[dt.date]:
    dates = recent_trading_dates(engine, as_of, n=rows)
    if not dates:
        return []
    with engine.connect() as conn:
        existing = conn.execute(
            text(f"""
                SELECT trade_date
                FROM smartmoney.{table}
                WHERE ts_code = :ts_code
                  AND trade_date = ANY(:dates)
            """),
            {"ts_code": ts_code, "dates": dates},
        ).scalars().all()
    return [day for day in dates if day not in set(existing)]
