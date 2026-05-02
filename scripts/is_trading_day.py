#!/usr/bin/env python3
"""Check whether a given date is an A-share trading day (SSE).

Usage
-----
    # Today (Beijing time)
    uv run python scripts/is_trading_day.py

    # Specific date
    uv run python scripts/is_trading_day.py 2026-04-30
    uv run python scripts/is_trading_day.py 2026-05-01

    # Refresh local trade_cal table from TuShare (run monthly/quarterly)
    uv run python scripts/is_trading_day.py --refresh
    uv run python scripts/is_trading_day.py --refresh --start-year 2015

Exit codes
----------
    0  = trading day
    1  = not a trading day
    2  = error (table empty, DB connection failed, etc.)
"""
from __future__ import annotations

import sys
import datetime as dt

# ── imports after path setup ──────────────────────────────────────────────────
try:
    from ifa.core.db import get_engine
    from ifa.core.calendar import (
        is_trading_day,
        refresh_trade_cal,
        today_bjt,
    )
except ImportError as e:
    print(f"ERROR: {e}", file=sys.stderr)
    print("Run from the project root with: uv run python scripts/is_trading_day.py", file=sys.stderr)
    sys.exit(2)


def _parse_date(s: str) -> dt.date:
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}  (expected YYYY-MM-DD or YYYYMMDD)")


def _do_refresh(engine, start_year: int) -> None:
    try:
        from ifa.config import get_settings
        from ifa.core.tushare import TuShareClient
        settings = get_settings()
        client = TuShareClient(settings)
    except Exception as e:
        print(f"ERROR initialising TuShare client: {e}", file=sys.stderr)
        sys.exit(2)

    end_year = today_bjt().year + 1
    print(f"Fetching SSE trading calendar {start_year}–{end_year} from TuShare…")
    try:
        n = refresh_trade_cal(engine, client, start_year=start_year, end_year=end_year)
        print(f"✓ Upserted {n} rows into smartmoney.trade_cal")
    except Exception as e:
        print(f"ERROR during refresh: {e}", file=sys.stderr)
        sys.exit(2)


def main() -> None:
    args = sys.argv[1:]

    # ── --refresh mode ────────────────────────────────────────────────────────
    if "--refresh" in args:
        args = [a for a in args if a != "--refresh"]
        start_year = 2015
        for a in args:
            if a.startswith("--start-year="):
                start_year = int(a.split("=", 1)[1])
            elif a.startswith("--start-year"):
                idx = args.index(a)
                if idx + 1 < len(args):
                    start_year = int(args[idx + 1])
        engine = get_engine()
        _do_refresh(engine, start_year)
        return

    # ── check mode ────────────────────────────────────────────────────────────
    if args:
        try:
            check_date = _parse_date(args[0])
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)
    else:
        check_date = today_bjt()

    engine = get_engine()
    try:
        result = is_trading_day(engine, check_date)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    day_str = check_date.strftime("%Y-%m-%d (%A)")
    if result:
        print(f"true   {day_str} is a trading day")
        sys.exit(0)
    else:
        print(f"false  {day_str} is NOT a trading day")
        sys.exit(1)


if __name__ == "__main__":
    main()
