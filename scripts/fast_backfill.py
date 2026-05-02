#!/usr/bin/env python3
"""Optimised SmartMoney raw backfill.

Why this exists
---------------
The production runner (run_backfill) is designed for correct daily ETL:
  · fetches all 20 tables per trading day including raw_dc_member (~12s/day)
    and raw_sw_daily (31 per-code calls, ~13s/day)
  · adds 0.5 s sleep between days
Total: ~40 s/day → ~10 h for 916 days

This script applies three targeted optimisations:

  1. **Skip raw_dc_member** — replaced permanently by SW (申万); not needed
     for B-phase compute.  Saves ~12 s/day.

  2. **Bulk-fetch raw_sw_daily & raw_index_daily** — instead of 31+8 TuShare
     calls per trading day, each code is fetched once with a start/end date
     range covering the full backfill window.  Reduces from
     (31+8) × N_days = ~36 000 calls to just 39 calls total.  Saves ~13 s/day
     pulled out of the per-day loop entirely.

  3. **Smart-skip complete days** — a day is deemed complete when both
     raw_daily AND raw_moneyflow have ≥ 4 000 rows for that trade_date.
     Skip the 14 remaining table fetches for that day.

Result: ~13 s/day vs ~40 s/day → ~2.5× speed-up.
For 916 backfill days: ~3.3 h total (vs ~10 h).

TuShare note
------------
TuShare is single-token serial; do NOT run two concurrent instances with the
same token — you risk rate-limit bans.  This script deliberately avoids any
artificial sleep; TuShare's own API latency naturally throttles us to ~1–2
calls/second which is within safe limits.

Usage
-----
    cd /Users/neoclaw/claude/ifa-claude
    uv run python scripts/fast_backfill.py --start 2021-01-01 --end 2022-12-31
    uv run python scripts/fast_backfill.py --start 2024-01-17 --end 2025-10-31
    uv run python scripts/fast_backfill.py --start 2021-01-01 --end 2025-10-31  # full range
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

# ── path bootstrap ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force unbuffered output so progress is visible when piped / backgrounded
sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]

from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.core.tushare import TuShareClient
from ifa.families.smartmoney.etl import raw_fetchers as rf
from ifa.families.smartmoney.universe import MAIN_INDEXES, SW_L1_SEED
from sqlalchemy import text


SCHEMA = "smartmoney"
# Minimum rows to consider a day "complete" (raw_daily has ~5000 A-share stocks)
COMPLETE_THRESHOLD = 4_000


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_trade_dates(client: TuShareClient, start: dt.date, end: dt.date) -> list[dt.date]:
    """Pull trade calendar from TuShare; return sorted list of open days."""
    df = client.call(
        "trade_cal",
        exchange="SSE",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if df is None or df.empty:
        return []
    return sorted(
        dt.datetime.strptime(str(r.cal_date), "%Y%m%d").date()
        for r in df.itertuples()
        if int(r.is_open) == 1
    )


def _already_complete(engine, dates: list[dt.date]) -> set[dt.date]:
    """Return the subset of dates that have both raw_daily and raw_moneyflow
    fully loaded (≥ COMPLETE_THRESHOLD rows each)."""
    if not dates:
        return set()
    with engine.connect() as c:
        # Batch check — single query per table
        result = c.execute(text(f"""
            SELECT d.trade_date
            FROM (
                SELECT trade_date, COUNT(*) AS n
                FROM {SCHEMA}.raw_daily
                WHERE trade_date = ANY(:dates)
                GROUP BY trade_date
            ) d
            JOIN (
                SELECT trade_date, COUNT(*) AS n
                FROM {SCHEMA}.raw_moneyflow
                WHERE trade_date = ANY(:dates)
                GROUP BY trade_date
            ) m USING (trade_date)
            WHERE d.n >= :thr AND m.n >= :thr
        """), {"dates": dates, "thr": COMPLETE_THRESHOLD}).fetchall()
    return {row[0] for row in result}


def _bulk_fetch_sw_daily(
    client: TuShareClient, engine, start: dt.date, end: dt.date
) -> int:
    """Fetch raw_sw_daily for ALL SW L1 codes across the full date range.

    One TuShare call per SW L1 code (31 calls total) instead of one call per
    (code × day) pair (~28 000 calls).  Massive reduction in API usage.
    """
    sw_codes = [code for code, _ in SW_L1_SEED]
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    total = 0
    print(f"\n[bulk] raw_sw_daily — {len(sw_codes)} SW L1 codes, {s}→{e}")
    for i, code in enumerate(sw_codes, 1):
        try:
            df = client.call("sw_daily", ts_code=code, start_date=s, end_date=e)
        except Exception as exc:
            print(f"  [{code}] FAIL {exc}")
            continue
        if df is None or df.empty:
            continue
        rows = []
        for r in df.itertuples():
            td_raw = str(r.trade_date)
            td = dt.date(int(td_raw[:4]), int(td_raw[4:6]), int(td_raw[6:8])) \
                 if len(td_raw) == 8 and td_raw.isdigit() \
                 else None
            if td is None:
                continue
            rows.append({
                "ts_code": str(r.ts_code),
                "trade_date": td,
                "name": str(r.name) if hasattr(r, "name") and r.name else None,
                "open": float(r.open) if r.open and str(r.open) != "nan" else None,
                "low": float(r.low) if r.low and str(r.low) != "nan" else None,
                "high": float(r.high) if r.high and str(r.high) != "nan" else None,
                "close": float(r.close) if r.close and str(r.close) != "nan" else None,
                "change_": float(r.change) if hasattr(r, "change") and r.change and str(r.change) != "nan" else None,
                "pct_change": float(r.pct_change) if hasattr(r, "pct_change") and r.pct_change and str(r.pct_change) != "nan" else None,
                "vol": float(r.vol) if r.vol and str(r.vol) != "nan" else None,
                "amount": float(r.amount) if r.amount and str(r.amount) != "nan" else None,
                "pe": float(r.pe) if hasattr(r, "pe") and r.pe and str(r.pe) != "nan" else None,
                "pb": float(r.pb) if hasattr(r, "pb") and r.pb and str(r.pb) != "nan" else None,
                "float_mv": float(r.float_mv) if hasattr(r, "float_mv") and r.float_mv and str(r.float_mv) != "nan" else None,
                "total_mv": float(r.total_mv) if hasattr(r, "total_mv") and r.total_mv and str(r.total_mv) != "nan" else None,
            })
        n = rf._bulk_upsert(engine, "raw_sw_daily", rows, pk_cols=["trade_date", "ts_code"])
        total += n
        print(f"  [{i:02d}/{len(sw_codes)}] {code}: {n} rows")
    print(f"[bulk] raw_sw_daily done — {total} rows total\n")
    return total


def _bulk_fetch_index_daily(
    client: TuShareClient, engine, start: dt.date, end: dt.date
) -> int:
    """Fetch raw_index_daily for all main index codes across the full range."""
    index_codes = [code for code, _ in MAIN_INDEXES]
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    total = 0
    print(f"[bulk] raw_index_daily — {len(index_codes)} index codes, {s}→{e}")
    for i, code in enumerate(index_codes, 1):
        try:
            df = client.call("index_daily", ts_code=code, start_date=s, end_date=e)
        except Exception as exc:
            print(f"  [{code}] FAIL {exc}")
            continue
        if df is None or df.empty:
            continue
        rows = []
        for r in df.itertuples():
            td_raw = str(r.trade_date)
            td = dt.date(int(td_raw[:4]), int(td_raw[4:6]), int(td_raw[6:8])) \
                 if len(td_raw) == 8 and td_raw.isdigit() \
                 else None
            if td is None:
                continue
            rows.append({
                "ts_code": str(r.ts_code), "trade_date": td,
                "close": float(r.close) if r.close and str(r.close) != "nan" else None,
                "open": float(r.open) if r.open and str(r.open) != "nan" else None,
                "high": float(r.high) if r.high and str(r.high) != "nan" else None,
                "low": float(r.low) if r.low and str(r.low) != "nan" else None,
                "pre_close": float(r.pre_close) if r.pre_close and str(r.pre_close) != "nan" else None,
                "change_": float(r.change) if hasattr(r, "change") and r.change and str(r.change) != "nan" else None,
                "pct_chg": float(r.pct_chg) if r.pct_chg and str(r.pct_chg) != "nan" else None,
                "vol": float(r.vol) if r.vol and str(r.vol) != "nan" else None,
                "amount": float(r.amount) if r.amount and str(r.amount) != "nan" else None,
            })
        n = rf._bulk_upsert(engine, "raw_index_daily", rows, pk_cols=["trade_date", "ts_code"])
        total += n
        print(f"  [{i:02d}/{len(index_codes)}] {code}: {n} rows")
    print(f"[bulk] raw_index_daily done — {total} rows total\n")
    return total


# ── per-day loop (skips dc_member + uses pre-fetched sw/index daily) ──────────

# Tables to fetch per day — dc_member removed (replaced by SW; not needed for B-phase)
# sw_daily and index_daily handled in bulk phase above
_PER_DAY_FETCHERS = [
    ("raw_daily",             rf.fetch_raw_daily),
    ("raw_daily_basic",       rf.fetch_raw_daily_basic),
    ("raw_moneyflow",         rf.fetch_raw_moneyflow),
    ("raw_moneyflow_ind_dc",  rf.fetch_raw_moneyflow_ind_dc),
    ("raw_moneyflow_ind_ths", rf.fetch_raw_moneyflow_ind_ths),
    ("raw_moneyflow_hsgt",    rf.fetch_raw_moneyflow_hsgt),
    ("raw_margin",            rf.fetch_raw_margin),
    ("raw_limit_list_d",      rf.fetch_raw_limit_list_d),
    ("raw_kpl_concept",       rf.fetch_raw_kpl_concept),
    ("raw_kpl_concept_cons",  rf.fetch_raw_kpl_concept_cons),
    ("raw_kpl_list",          rf.fetch_raw_kpl_list),
    ("raw_top_list",          rf.fetch_raw_top_list),
    ("raw_top_inst",          rf.fetch_raw_top_inst),
    ("raw_ths_hot",           rf.fetch_raw_ths_hot),
    ("raw_dc_hot",            rf.fetch_raw_dc_hot),
    ("raw_dc_index",          rf.fetch_raw_dc_index),
    # raw_dc_member: SKIPPED — replaced by SW (申万)
    ("raw_block_trade",       rf.fetch_raw_block_trade),
]


def _fetch_one_day(client, engine, trade_date: dt.date) -> dict[str, int]:
    """Run all per-day fetchers for one trading date. Returns {table: rows}."""
    result = {}
    for table, fn in _PER_DAY_FETCHERS:
        try:
            n = fn(client, engine, trade_date=trade_date)
            result[table] = n
        except Exception as exc:
            print(f"    [{table}] FAIL {exc}")
            result[table] = -1
    return result


# ── main ─────────────────────────────────────────────────────────────────────

def run_fast_backfill(
    start: dt.date,
    end: dt.date,
    *,
    skip_bulk: bool = False,
    dry_run: bool = False,
) -> None:
    """Execute the optimised backfill.

    Args:
        start, end: inclusive date range (any calendar dates; only trading
                    days will actually be processed).
        skip_bulk:  set True to skip the sw_daily/index_daily bulk phase
                    (e.g. if already done in a previous run).
        dry_run:    print what would happen without touching the DB.
    """
    t0_total = time.monotonic()
    settings = get_settings()
    engine = get_engine(settings)
    client = TuShareClient(settings)

    print(f"\n{'='*60}")
    print(f"  fast_backfill  {start} → {end}")
    print(f"  Skipping: raw_dc_member")
    print(f"  Bulk-fetching: raw_sw_daily, raw_index_daily")
    print(f"{'='*60}\n")

    # ── Phase 0: trade calendar ───────────────────────────────────────────────
    print(f"[0/3] Loading trade calendar {start} → {end}…")
    trade_dates = _get_trade_dates(client, start, end)
    print(f"      {len(trade_dates)} trading days in window\n")

    if dry_run:
        print("[DRY RUN] Would process:", len(trade_dates), "days — exiting")
        return

    # ── Phase 1: bulk sw_daily + index_daily ─────────────────────────────────
    if not skip_bulk:
        print("[1/3] Bulk-fetching per-code tables (sw_daily, index_daily)…")
        _bulk_fetch_sw_daily(client, engine, start, end)
        _bulk_fetch_index_daily(client, engine, start, end)
    else:
        print("[1/3] Bulk phase skipped (--skip-bulk)\n")

    # ── Phase 2: check already-complete days ──────────────────────────────────
    print("[2/3] Checking which days are already complete in DB…")
    done_set = _already_complete(engine, trade_dates)
    todo = [d for d in trade_dates if d not in done_set]
    print(f"      Already complete: {len(done_set)} days → skipping")
    print(f"      Need backfill:    {len(todo)} days\n")

    if not todo:
        print("All days complete — nothing to do.")
        return

    # ── Phase 3: per-day loop ─────────────────────────────────────────────────
    print(f"[3/3] Per-day fetch loop ({len(todo)} days, dc_member skipped)…\n")
    total_rows = 0
    for i, td in enumerate(todo, 1):
        t_day = time.monotonic()
        row_counts = _fetch_one_day(client, engine, td)
        day_rows = sum(v for v in row_counts.values() if v > 0)
        total_rows += day_rows
        elapsed = time.monotonic() - t_day
        pct = 100 * i / len(todo)
        eta_s = int((time.monotonic() - t0_total) / i * (len(todo) - i))
        eta = f"{eta_s // 3600}h{(eta_s % 3600) // 60:02d}m"
        print(f"  [{i:4d}/{len(todo)}] {td}  {day_rows:6,} rows  {elapsed:.1f}s  "
              f"({pct:.0f}% done, ETA {eta})")

    total_elapsed = time.monotonic() - t0_total
    print(f"\n{'='*60}")
    print(f"  fast_backfill DONE")
    print(f"  {len(todo)} days processed, {total_rows:,} rows, "
          f"{total_elapsed/60:.1f} min total")
    print(f"{'='*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartMoney fast raw backfill")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--skip-bulk", action="store_true",
                        help="Skip the sw_daily/index_daily bulk phase")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan without touching DB")
    args = parser.parse_args()

    start = dt.date.fromisoformat(args.start)
    end   = dt.date.fromisoformat(args.end)
    run_fast_backfill(start, end, skip_bulk=args.skip_bulk, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
