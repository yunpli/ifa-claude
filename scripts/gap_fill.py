#!/usr/bin/env python3
"""Gap-fill script: detects and re-fetches missing raw_* data after backfill.

Usage:
    python scripts/gap_fill.py [--start 2023-01-01] [--end 2026-04-30] [--dry-run] [--tables raw_top_list,raw_limit_list_d]

Logic:
  1. Collect all trade dates in window that have raw_daily rows (the anchor).
  2. For each raw table, find dates where anchor has data but the table has none.
     SPARSE tables (龙虎榜, 涨跌停, 大宗交易 etc.) are skipped in gap detection
     because they legitimately have 0 rows on most days — they are fetched
     unconditionally instead (cheaper than false-positive re-fetches).
  3. Re-fetch only the gap (date, table) pairs, with retry.
  4. Report a summary at the end.

Per-code tables (raw_sw_daily, raw_index_daily) are handled separately
because they require a universe list rather than a single trade_date arg.
raw_cyq_chips is skipped by default (too expensive; use --chips to include).
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
import sys
import os

# ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from ifa.core.db.engine import get_engine
from ifa.core.settings import get_settings
from ifa.families.smartmoney.etl.tushare_client import TuShareClient
from ifa.families.smartmoney.etl import raw_fetchers as rf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gap_fill")

SCHEMA = "smartmoney"
ANCHOR_TABLE = "raw_daily"

# Tables we can gap-fill with a simple trade_date call (from TRADE_DATE_FETCHERS)
SIMPLE_TABLES = [t for t, _ in rf.TRADE_DATE_FETCHERS]

# Per-code tables — need universe resolution
PER_CODE_TABLES = ["raw_sw_daily", "raw_index_daily"]

# Sparse tables: legitimately have 0 rows on most trading days.
# Gap detection (anchor-based) would produce massive false positives for these.
# Instead we just re-fetch them unconditionally for any date that appears in the
# user's requested table list (or always, if --sparse-too is set).
SPARSE_TABLES: set[str] = {
    "raw_top_list",       # 龙虎榜 — only on unusual-volume days
    "raw_top_inst",       # 龙虎榜机构席位 — same as above
    "raw_limit_list_d",   # 涨跌停 — empty if no limit moves
    "raw_kpl_list",       # 开盘啦连板/首板榜 — only during active markets
    "raw_block_trade",    # 大宗交易 — not every day
}

MAX_RETRIES = 3
RETRY_SLEEP = 10  # seconds between retries


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_anchor_dates(engine, start: dt.date, end: dt.date) -> list[dt.date]:
    """Trade dates that have at least one row in raw_daily."""
    sql = text(f"""
        SELECT DISTINCT trade_date FROM {SCHEMA}.{ANCHOR_TABLE}
        WHERE trade_date >= :s AND trade_date <= :e
        ORDER BY trade_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"s": start, "e": end}).fetchall()
    return [r[0] for r in rows]


def get_covered_dates(engine, table: str, anchor_dates: list[dt.date]) -> set[dt.date]:
    """Dates in anchor_dates that already have ≥1 row in `table`.

    Uses explicit IN (...) with date literals — avoids SQLAlchemy text() list-
    binding ambiguity with psycopg3's ANY(:param) syntax.
    """
    if not anchor_dates:
        return set()
    # Safe: dates are dt.date objects, not user input
    date_literals = ", ".join(f"'{d}'" for d in anchor_dates)
    sql = text(f"""
        SELECT DISTINCT trade_date FROM {SCHEMA}.{table}
        WHERE trade_date IN ({date_literals})
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    return {r[0] for r in rows}


def fetch_with_retry(fn, client, engine, *, trade_date: dt.date,
                     table: str, extra_kwargs: dict | None = None) -> int:
    """Call fetcher fn up to MAX_RETRIES times; return rows loaded or -1 on permanent fail."""
    kwargs: dict = {"trade_date": trade_date}
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            n = fn(client, engine, **kwargs)
            return n
        except Exception as exc:
            if attempt < MAX_RETRIES:
                log.warning("[%s] %s attempt %d/%d failed: %s — retrying in %ds",
                            table, trade_date, attempt, MAX_RETRIES, exc, RETRY_SLEEP)
                time.sleep(RETRY_SLEEP)
            else:
                log.error("[%s] %s permanently failed after %d attempts: %s",
                          table, trade_date, MAX_RETRIES, exc)
    return -1


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Gap-fill missing raw_* rows after backfill")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end",   default="2026-04-30")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only report gaps, don't fetch")
    parser.add_argument("--tables", default="",
                        help="Comma-separated list of tables to check (default: all tables)")
    parser.add_argument("--chips", action="store_true",
                        help="Also check/fill raw_cyq_chips (slow)")
    parser.add_argument("--sparse-too", action="store_true",
                        help="Re-fetch sparse tables (top_list, limit_list_d etc.) unconditionally")
    args = parser.parse_args()

    start = dt.date.fromisoformat(args.start)
    end   = dt.date.fromisoformat(args.end)

    engine   = get_engine()
    settings = get_settings()
    client   = TuShareClient(settings)

    if args.tables:
        tables_to_check = [t.strip() for t in args.tables.split(",") if t.strip()]
    else:
        tables_to_check = SIMPLE_TABLES + PER_CODE_TABLES
        if args.chips:
            tables_to_check.append("raw_cyq_chips")

    log.info("Gap-fill scan: %s → %s  tables=%d  dry_run=%s  sparse_too=%s",
             start, end, len(tables_to_check), args.dry_run, args.sparse_too)

    anchor_dates = get_anchor_dates(engine, start, end)
    log.info("Anchor dates (raw_daily): %d trading days", len(anchor_dates))
    if not anchor_dates:
        log.error("No anchor dates found — is the backfill complete?")
        return

    universes = None
    fetcher_map = dict(rf.TRADE_DATE_FETCHERS)
    summary: dict[str, dict] = {}

    for table in tables_to_check:

        # ── Sparse tables: re-fetch unconditionally (only if --sparse-too) ────
        if table in SPARSE_TABLES and not args.sparse_too:
            log.info("[%s] sparse table — skipped (use --sparse-too to force)", table)
            summary[table] = {"gaps": "N/A (sparse)", "filled": 0, "failed": 0}
            continue

        # ── Dense tables: anchor-based gap detection ──────────────────────────
        covered = get_covered_dates(engine, table, anchor_dates)
        gap_dates = sorted(d for d in anchor_dates if d not in covered)

        summary[table] = {"gaps": len(gap_dates), "filled": 0, "failed": 0}

        if not gap_dates and table not in SPARSE_TABLES:
            log.info("[%s] ✓ no gaps", table)
            continue

        # For sparse + --sparse-too: re-fetch every anchor date
        if table in SPARSE_TABLES and args.sparse_too:
            gap_dates = list(anchor_dates)
            log.info("[%s] sparse — will re-fetch all %d dates", table, len(gap_dates))
        else:
            log.info("[%s] %d gap dates", table, len(gap_dates))

        if args.dry_run:
            for d in gap_dates[:5]:
                log.info("  gap: %s", d)
            if len(gap_dates) > 5:
                log.info("  ... and %d more", len(gap_dates) - 5)
            continue

        # Determine fetch function + extra kwargs
        if table in fetcher_map:
            fn = fetcher_map[table]
            extra = None
        elif table in ("raw_sw_daily", "raw_index_daily"):
            fn = rf.PER_CODE_FETCHERS[table]
            if universes is None:
                log.info("Resolving universe lists...")
                universes = _resolve_universes(client)
            code_key = "sw_codes" if table == "raw_sw_daily" else "index_codes"
            extra = {code_key: universes[code_key]}
        elif table == "raw_cyq_chips":
            fn = rf.PER_CODE_FETCHERS["raw_cyq_chips"]
            if universes is None:
                universes = _resolve_universes(client)
            extra = {"ts_codes": universes["ts_codes"]}
        else:
            log.warning("[%s] unknown table — skipping", table)
            continue

        for i, d in enumerate(gap_dates, 1):
            log.info("[%s] filling %s (%d/%d)...", table, d, i, len(gap_dates))
            n = fetch_with_retry(fn, client, engine,
                                 trade_date=d, table=table,
                                 extra_kwargs=extra)
            if n >= 0:
                summary[table]["filled"] += 1
                log.info("[%s] %s → %d rows", table, d, n)
            else:
                summary[table]["failed"] += 1
            time.sleep(0.5)

    # ── Final report ─────────────────────────────────────────────────────────
    log.info("")
    log.info("═" * 62)
    log.info("GAP-FILL SUMMARY  (%s → %s)", start, end)
    log.info("═" * 62)
    any_gaps = False
    for table, s in summary.items():
        gaps = s["gaps"]
        if gaps == 0:
            log.info("  %-32s  ✓ clean", table)
        elif isinstance(gaps, str):
            log.info("  %-32s  %s", table, gaps)
        else:
            any_gaps = True
            if args.dry_run:
                log.info("  %-32s  GAP %d dates (dry-run)", table, gaps)
            else:
                log.info("  %-32s  gaps=%d  filled=%d  failed=%d",
                         table, gaps, s["filled"], s["failed"])
    if not any_gaps:
        log.info("All dense tables clean — no gaps found.")
    log.info("═" * 62)


def _resolve_universes(client: TuShareClient) -> dict:
    """Resolve active stock/index/SW universe codes."""
    ts_codes: list[str] = []
    sw_codes: list[str] = []

    stock_basic = client.call("stock_basic", list_status="L", fields="ts_code")
    if stock_basic is not None:
        ts_codes = stock_basic["ts_code"].tolist()

    sw_basic = client.call("index_classify", level="L1", src="SW2021")
    if sw_basic is not None:
        sw_codes = sw_basic["index_code"].tolist()

    index_codes = [
        "000001.SH", "000300.SH", "000905.SH", "000852.SH",
        "399001.SZ", "399006.SZ", "399303.SZ", "688000.SH",
    ]

    return {
        "ts_codes": ts_codes,
        "sw_codes": sw_codes,
        "index_codes": index_codes,
    }


if __name__ == "__main__":
    main()
