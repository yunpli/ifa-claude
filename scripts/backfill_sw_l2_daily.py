#!/usr/bin/env python3
"""SW L2 daily price backfill (V2.1.1).

Why this exists
---------------
`raw_sw_daily` was historically populated only with 31 SW L1 indices. SW L2
(~131 sub-industry indices) carries the granularity needed for:

  · `market.fetch_main_lines` — direct L2 close/pct_change instead of
    aggregating from member stocks
  · Tech V2.1 five-layer cake — direct L2 board prices
  · SmartMoney B-phase factor flow & sector-state computation

This script bulk-fetches `sw_daily(ts_code, start_date, end_date)` for every
distinct L2 code in `raw_sw_member`. One TuShare call per code returning the
whole window — typically 131 calls for the full 2021–today backfill.

TuShare cost
------------
- Endpoint: `sw_daily` — ~1 call/sec sustained, full window per code
- 131 codes × 1 call = ~3 minutes wall time
- Total rows: ~131 × ~1300 trade days ≈ 170 k rows

Idempotent — uses the same `(trade_date, ts_code)` PK as L1 rows; safe to
re-run.

Usage
-----
    cd /Users/neoclaw/claude/ifa-claude

    # Full backfill 2021-01-01 → today
    uv run python scripts/backfill_sw_l2_daily.py

    # Custom range
    uv run python scripts/backfill_sw_l2_daily.py --start 2024-01-01 --end 2026-04-30

    # Only the most recent N days (incremental top-up)
    uv run python scripts/backfill_sw_l2_daily.py --recent-days 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

# ── path bootstrap ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]

from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.core.tushare import TuShareClient
from ifa.families.smartmoney.etl import raw_fetchers as rf

SCHEMA = "smartmoney"


def _list_sw_l2_codes(engine) -> list[tuple[str, str]]:
    """Return [(l2_code, l2_name), ...] for every L2 in raw_sw_member."""
    with engine.connect() as c:
        rows = c.execute(text(f"""
            SELECT DISTINCT l2_code, l2_name
              FROM {SCHEMA}.raw_sw_member
             WHERE l2_code IS NOT NULL
             ORDER BY l2_code
        """)).fetchall()
    return [(r[0], r[1] or r[0]) for r in rows]


def _bulk_fetch_one_code(
    client: TuShareClient,
    engine,
    code: str,
    name: str,
    start: dt.date,
    end: dt.date,
) -> int:
    """Fetch sw_daily for one ts_code over [start, end] and upsert."""
    s = start.strftime("%Y%m%d")
    e = end.strftime("%Y%m%d")
    try:
        df = client.call("sw_daily", ts_code=code, start_date=s, end_date=e)
    except Exception as exc:
        print(f"  FAIL {code} ({name}): {exc}")
        return 0
    if df is None or df.empty:
        return 0

    rows: list[dict] = []
    for r in df.itertuples():
        td_raw = str(r.trade_date)
        if not (len(td_raw) == 8 and td_raw.isdigit()):
            continue
        td = dt.date(int(td_raw[:4]), int(td_raw[4:6]), int(td_raw[6:8]))

        def _f(v):
            if v is None or str(v) == "nan":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        rows.append({
            "ts_code": str(r.ts_code),
            "trade_date": td,
            "name": str(r.name) if hasattr(r, "name") and r.name else name,
            "open":   _f(getattr(r, "open", None)),
            "low":    _f(getattr(r, "low", None)),
            "high":   _f(getattr(r, "high", None)),
            "close":  _f(getattr(r, "close", None)),
            "change_": _f(getattr(r, "change", None)),
            "pct_change": _f(getattr(r, "pct_change", None)),
            "vol":    _f(getattr(r, "vol", None)),
            "amount": _f(getattr(r, "amount", None)),
            "pe":     _f(getattr(r, "pe", None)),
            "pb":     _f(getattr(r, "pb", None)),
            "float_mv": _f(getattr(r, "float_mv", None)),
            "total_mv": _f(getattr(r, "total_mv", None)),
        })
    if not rows:
        return 0
    return rf._bulk_upsert(engine, "raw_sw_daily", rows,
                           pk_cols=["trade_date", "ts_code"])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=str, default="2021-01-01",
                   help="Start date YYYY-MM-DD (default 2021-01-01)")
    p.add_argument("--end", type=str, default=None,
                   help="End date YYYY-MM-DD (default: today)")
    p.add_argument("--recent-days", type=int, default=None,
                   help="Override start: backfill only the last N calendar days "
                        "(useful for daily incremental top-up)")
    args = p.parse_args()

    end = dt.date.today() if args.end is None else dt.date.fromisoformat(args.end)
    if args.recent_days is not None:
        start = end - dt.timedelta(days=args.recent_days)
    else:
        start = dt.date.fromisoformat(args.start)

    engine = get_engine()
    client = TuShareClient()

    codes = _list_sw_l2_codes(engine)
    print(f"SW L2 backfill — {len(codes)} codes, {start} → {end}")
    print(f"DB: {engine.url}")
    print("=" * 72)

    total_rows = 0
    fail_codes: list[tuple[str, str]] = []
    t_start = dt.datetime.now()

    for i, (code, name) in enumerate(codes, 1):
        n = _bulk_fetch_one_code(client, engine, code, name, start, end)
        if n == 0:
            fail_codes.append((code, name))
        total_rows += n
        print(f"  [{i:3d}/{len(codes)}] {code:12s} {name:20s} {n:5d} rows")

    elapsed = (dt.datetime.now() - t_start).total_seconds()
    print("=" * 72)
    print(f"Done in {elapsed:.1f}s — {total_rows:,} rows total ({len(codes)} codes)")
    if fail_codes:
        print(f"\n{len(fail_codes)} codes returned 0 rows:")
        for code, name in fail_codes[:20]:
            print(f"  {code} {name}")
        if len(fail_codes) > 20:
            print(f"  ... and {len(fail_codes) - 20} more")

    # Quick coverage report
    with engine.connect() as c:
        n_l2_in_db = c.execute(text(f"""
            SELECT COUNT(DISTINCT ts_code) FROM {SCHEMA}.raw_sw_daily
            WHERE ts_code LIKE '8011%' OR ts_code LIKE '8012%'
               OR ts_code LIKE '8013%' OR ts_code LIKE '8014%'
               OR ts_code LIKE '8015%' OR ts_code LIKE '8016%'
               OR ts_code LIKE '8017%' OR ts_code LIKE '8018%'
               OR ts_code LIKE '8019%'
        """)).scalar()
    print(f"\nraw_sw_daily distinct L2 codes in DB: {n_l2_in_db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
