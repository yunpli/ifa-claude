#!/usr/bin/env python3
"""Gap-fill script: detects and re-fetches missing raw_* data after backfill.

Usage:
    python scripts/gap_fill.py [--start 2023-01-01] [--end 2026-04-30] [--dry-run] [--tables raw_top_list,raw_limit_list_d]

Logic:
  1. Collect all trade dates in window that have raw_daily rows (the anchor).
  2. For each raw table, find dates where anchor has data but the table has none.
  3. Re-fetch only those (date, table) pairs, with retry.
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

# Tables that always have data on every trade date (anchor check tables)
ANCHOR_TABLE = "raw_daily"

# Tables we can gap-fill with a simple trade_date call
SIMPLE_TABLES = [t for t, _ in rf.TRADE_DATE_FETCHERS]

# Per-code tables — need universe resolution
PER_CODE_TABLES = ["raw_sw_daily", "raw_index_daily"]

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
    """Dates in anchor_dates that already have ≥1 row in `table`."""
    if not anchor_dates:
        return set()
    sql = text(f"""
        SELECT DISTINCT trade_date FROM {SCHEMA}.{table}
        WHERE trade_date = ANY(:dates)
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"dates": anchor_dates}).fetchall()
    return {r[0] for r in rows}


def fetch_with_retry(fn, client, engine, *, trade_date: dt.date,
                     table: str, extra_kwargs: dict | None = None) -> int:
    """Call fetcher fn up to MAX_RETRIES times; return rows loaded or -1 on permanent fail."""
    kwargs = {"trade_date": trade_date}
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
                        help="Comma-separated list of tables to check (default: all simple tables)")
    parser.add_argument("--chips", action="store_true",
                        help="Also check/fill raw_cyq_chips (slow)")
    args = parser.parse_args()

    start = dt.date.fromisoformat(args.start)
    end   = dt.date.fromisoformat(args.end)

    engine   = get_engine()
    settings = get_settings()
    client   = TuShareClient(settings)

    tables_to_check = (
        [t.strip() for t in args.tables.split(",") if t.strip()]
        if args.tables
        else SIMPLE_TABLES + PER_CODE_TABLES
    )
    if args.chips:
        tables_to_check.append("raw_cyq_chips")

    log.info("Gap-fill scan: %s → %s  tables=%s  dry_run=%s",
             start, end, len(tables_to_check), args.dry_run)

    anchor_dates = get_anchor_dates(engine, start, end)
    log.info("Anchor dates (raw_daily): %d trading days", len(anchor_dates))
    if not anchor_dates:
        log.error("No anchor dates found — is the backfill complete?")
        return

    # Build universe once for per-code tables
    universes = None

    summary: dict[str, dict] = {}  # table → {gap_dates, filled, failed}

    fetcher_map = dict(rf.TRADE_DATE_FETCHERS)

    for table in tables_to_check:
        covered = get_covered_dates(engine, table, anchor_dates)
        gap_dates = sorted(d for d in anchor_dates if d not in covered)

        summary[table] = {"gaps": len(gap_dates), "filled": 0, "failed": 0}

        if not gap_dates:
            log.info("[%s] ✓ no gaps", table)
            continue

        log.info("[%s] %d gap dates", table, len(gap_dates))

        if args.dry_run:
            for d in gap_dates[:5]:
                log.info("  gap: %s", d)
            if len(gap_dates) > 5:
                log.info("  ... and %d more", len(gap_dates) - 5)
            continue

        # Determine how to fetch
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
            fn = rf.fetch_raw_cyq_chips
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
            time.sleep(0.5)  # be gentle with TuShare rate limits

    # ── Final report ─────────────────────────────────────────────────────────
    log.info("")
    log.info("═" * 60)
    log.info("GAP-FILL SUMMARY  (%s → %s)", start, end)
    log.info("═" * 60)
    any_gaps = False
    for table, s in summary.items():
        if s["gaps"] == 0:
            log.info("  %-30s  ✓ clean", table)
        else:
            any_gaps = True
            if args.dry_run:
                log.info("  %-30s  GAP %d dates (dry-run, not filled)", table, s["gaps"])
            else:
                status = f"filled={s['filled']}  failed={s['failed']}"
                log.info("  %-30s  gaps=%d  %s", table, s["gaps"], status)
    if not any_gaps:
        log.info("All tables clean — no gaps found.")
    log.info("═" * 60)


def _resolve_universes(client: TuShareClient) -> dict:
    """Resolve active stock/index/SW universe codes."""
    import pandas as pd

    ts_codes: list[str] = []
    sw_codes: list[str] = []
    index_codes: list[str] = []

    stock_basic = client.call("stock_basic", list_status="L", fields="ts_code")
    if stock_basic is not None:
        ts_codes = stock_basic["ts_code"].tolist()

    sw_basic = client.call("index_classify", level="L1", src="SW2021")
    if sw_basic is not None:
        sw_codes = sw_basic["index_code"].tolist()

    # Standard indices used in the pipeline
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
