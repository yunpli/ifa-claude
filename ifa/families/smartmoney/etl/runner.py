"""ETL orchestrator: backfill (range) + daily_etl (single date).

Drives all 21 raw fetchers per trade date, updates etl_watermarks per table,
handles trade-calendar awareness (skip weekends / holidays), and reports per-
table row counts.

Usage from CLI:
    ifa smartmoney backfill --start 20251101 --end 20260430
    ifa smartmoney etl --report-date 2026-04-30 --mode test
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.config import RunMode, get_settings
from ifa.core.db import get_engine
from ifa.core.tushare import TuShareClient

from ..universe import MAIN_INDEXES, SW_L1_SEED
from . import raw_fetchers as rf

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"


@dataclass
class TableStats:
    table: str
    rows_loaded: int = 0
    seconds: float = 0.0
    error: str | None = None


@dataclass
class DayStats:
    trade_date: dt.date
    tables: list[TableStats] = field(default_factory=list)
    total_rows: int = 0
    total_seconds: float = 0.0


def _is_trade_date(client: TuShareClient, d: dt.date) -> bool:
    """Use TuShare trade_cal to confirm trading day. Cached per call burst."""
    try:
        df = client.call("trade_cal",
                         exchange="SSE",
                         start_date=d.strftime("%Y%m%d"),
                         end_date=d.strftime("%Y%m%d"))
        if df is None or df.empty:
            return False
        return int(df.iloc[0]["is_open"]) == 1
    except Exception:
        return False


def _resolve_active_universes(client: TuShareClient) -> dict[str, list[str]]:
    """SW + index codes pulled from the curated universe in universe.py."""
    return {
        "sw_codes": [code for code, _ in SW_L1_SEED],
        "index_codes": [code for code, _ in MAIN_INDEXES],
    }


def _select_active_stocks_for_chips(engine: Engine, *, trade_date: dt.date,
                                    limit: int = 80) -> list[str]:
    """Pick the day's most active stocks (limit-up / kpl_list members) for
    cyq_chips fetch (per-stock cost; limit volume)."""
    sql = text(f"""
        SELECT DISTINCT ts_code FROM (
            SELECT ts_code FROM {SCHEMA}.raw_kpl_list WHERE trade_date = :d
            UNION
            SELECT ts_code FROM {SCHEMA}.raw_limit_list_d WHERE trade_date = :d
            UNION
            SELECT ts_code FROM {SCHEMA}.raw_top_list WHERE trade_date = :d
        ) sub
        LIMIT :lim
    """)
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(sql, {"d": trade_date, "lim": limit}).all()]


def _update_watermark(engine: Engine, *, table: str, trade_date: dt.date,
                      rows: int, run_mode: RunMode) -> None:
    sql = text(f"""
        INSERT INTO {SCHEMA}.etl_watermarks
            (table_name, last_trade_date_loaded, last_run_at, last_run_mode, rows_loaded_total)
        VALUES (:t, :d, now(), :m, :r)
        ON CONFLICT (table_name) DO UPDATE SET
            last_trade_date_loaded = GREATEST(
                COALESCE({SCHEMA}.etl_watermarks.last_trade_date_loaded, EXCLUDED.last_trade_date_loaded),
                EXCLUDED.last_trade_date_loaded
            ),
            last_run_at = EXCLUDED.last_run_at,
            last_run_mode = EXCLUDED.last_run_mode,
            rows_loaded_total = {SCHEMA}.etl_watermarks.rows_loaded_total + EXCLUDED.rows_loaded_total
    """)
    with engine.begin() as conn:
        conn.execute(sql, {"t": table, "d": trade_date, "m": run_mode.value, "r": rows})


def run_etl_for_date(
    *,
    trade_date: dt.date,
    on_log: Callable[[str], None] = lambda m: None,
    skip_chips: bool = False,
) -> DayStats:
    """Pull all raw_* tables for one trade date."""
    settings = get_settings()
    engine = get_engine(settings)
    client = TuShareClient(settings)

    if not _is_trade_date(client, trade_date):
        on_log(f"[{trade_date}] not a trade date; skipping")
        return DayStats(trade_date=trade_date)

    stats = DayStats(trade_date=trade_date)
    universes = _resolve_active_universes(client)

    # 18 trade-date-only fetchers
    for table, fn in rf.TRADE_DATE_FETCHERS:
        t0 = time.monotonic()
        try:
            n = fn(client, engine, trade_date=trade_date)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - t0
            stats.tables.append(TableStats(table=table, rows_loaded=0,
                                            seconds=elapsed,
                                            error=f"{type(exc).__name__}: {exc}"))
            on_log(f"  [{table}] FAIL {exc}")
            continue
        elapsed = time.monotonic() - t0
        stats.tables.append(TableStats(table=table, rows_loaded=n, seconds=elapsed))
        stats.total_rows += n
        stats.total_seconds += elapsed
        if n:
            _update_watermark(engine, table=table, trade_date=trade_date,
                              rows=n, run_mode=settings.run_mode)
            on_log(f"  [{table}] {n} rows in {elapsed:.1f}s")

    # SW daily (per-code)
    t0 = time.monotonic()
    n = rf.fetch_raw_sw_daily(client, engine, trade_date=trade_date,
                              sw_codes=universes["sw_codes"])
    elapsed = time.monotonic() - t0
    stats.tables.append(TableStats(table="raw_sw_daily", rows_loaded=n, seconds=elapsed))
    stats.total_rows += n
    if n:
        _update_watermark(engine, table="raw_sw_daily", trade_date=trade_date,
                          rows=n, run_mode=settings.run_mode)
        on_log(f"  [raw_sw_daily] {n} rows in {elapsed:.1f}s")

    # Main index daily (per-code)
    t0 = time.monotonic()
    n = rf.fetch_raw_index_daily(client, engine, trade_date=trade_date,
                                 index_codes=universes["index_codes"])
    elapsed = time.monotonic() - t0
    stats.tables.append(TableStats(table="raw_index_daily", rows_loaded=n, seconds=elapsed))
    stats.total_rows += n
    if n:
        _update_watermark(engine, table="raw_index_daily", trade_date=trade_date,
                          rows=n, run_mode=settings.run_mode)
        on_log(f"  [raw_index_daily] {n} rows in {elapsed:.1f}s")

    # cyq_chips (per-stock; gated to active stocks; can be skipped during heavy backfill)
    if not skip_chips:
        active = _select_active_stocks_for_chips(engine, trade_date=trade_date, limit=80)
        if active:
            t0 = time.monotonic()
            n = rf.fetch_raw_cyq_chips(client, engine, trade_date=trade_date, ts_codes=active)
            elapsed = time.monotonic() - t0
            stats.tables.append(TableStats(table="raw_cyq_chips",
                                            rows_loaded=n, seconds=elapsed))
            stats.total_rows += n
            if n:
                _update_watermark(engine, table="raw_cyq_chips", trade_date=trade_date,
                                  rows=n, run_mode=settings.run_mode)
                on_log(f"  [raw_cyq_chips] {n} rows ({len(active)} stocks) in {elapsed:.1f}s")

    return stats


def run_backfill(
    *,
    start: dt.date,
    end: dt.date,
    on_log: Callable[[str], None] = lambda m: None,
    skip_chips: bool = True,  # cyq_chips is expensive during backfill; on by default
    sleep_between_days: float = 0.5,
) -> list[DayStats]:
    """Walk every calendar date between start and end inclusive; ETL each
    actual trading day. Skips weekends/holidays via trade_cal."""
    out: list[DayStats] = []
    cur = start
    settings = get_settings()
    client = TuShareClient(settings)
    # Pull the full calendar slice once for efficient date iteration
    cal = client.call("trade_cal",
                      exchange="SSE",
                      start_date=start.strftime("%Y%m%d"),
                      end_date=end.strftime("%Y%m%d"))
    if cal is not None and not cal.empty:
        trade_dates = sorted({
            dt.datetime.strptime(str(r.cal_date), "%Y%m%d").date()
            for r in cal.itertuples() if int(r.is_open) == 1
        })
    else:
        trade_dates = []

    on_log(f"backfill window {start} → {end}: {len(trade_dates)} trading days")
    for i, td in enumerate(trade_dates, start=1):
        on_log(f"--- day {i}/{len(trade_dates)} · {td} ---")
        s = run_etl_for_date(trade_date=td, on_log=on_log, skip_chips=skip_chips)
        out.append(s)
        if sleep_between_days:
            time.sleep(sleep_between_days)
    on_log(f"backfill complete: {len(out)} days, total rows = "
           f"{sum(d.total_rows for d in out):,}")
    return out
