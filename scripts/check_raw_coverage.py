#!/usr/bin/env python3
"""Raw backfill progress checker.

Usage:
    uv run python scripts/check_raw_coverage.py
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ifa.core.db import get_engine
from sqlalchemy import text

TABLES = [
    ("raw_moneyflow",       "smartmoney"),
    ("raw_daily",           "smartmoney"),
    ("factor_daily",        "smartmoney"),
    ("sector_state_daily",  "smartmoney"),
    ("stock_signals_daily", "smartmoney"),
]

# Expected trading days per full year (approximate)
EXPECTED = {2021: 243, 2022: 242, 2023: 242, 2024: 244, 2025: 242}

BAR_WIDTH = 20


def bar(done: int, total: int) -> str:
    if total == 0:
        return "[" + "·" * BAR_WIDTH + "]"
    filled = int(BAR_WIDTH * done / total)
    return "[" + "█" * filled + "·" * (BAR_WIDTH - filled) + f"] {done}/{total}"


def main() -> None:
    eng = get_engine()
    with eng.connect() as c:
        for tbl, schema in TABLES:
            try:
                rows = c.execute(text(f"""
                    SELECT EXTRACT(YEAR FROM trade_date)::int AS yr,
                           MIN(trade_date), MAX(trade_date),
                           COUNT(DISTINCT trade_date) AS n
                    FROM {schema}.{tbl}
                    GROUP BY yr ORDER BY yr
                """)).fetchall()
            except Exception as e:
                print(f"\n{tbl}: ERROR — {e}")
                continue

            total_days = sum(r[3] for r in rows)
            print(f"\n{'─'*60}")
            print(f"  {schema}.{tbl}  ({total_days} total days in DB)")
            print(f"{'─'*60}")

            for yr, mn, mx, n in rows:
                expected = EXPECTED.get(int(yr), 242)
                pct = 100 * n / expected
                status = "✓" if pct >= 98 else ("▶" if pct >= 10 else "✗")
                b = bar(n, expected)
                print(f"  {status} {yr}: {mn} → {mx}  {b}  ({pct:.0f}%)")

            # Missing years
            all_yrs = {int(r[0]) for r in rows}
            missing = [y for y in EXPECTED if y not in all_yrs]
            for y in sorted(missing):
                print(f"  ✗ {y}: NO DATA")

    print(f"\n{'─'*60}")
    print("  Backfill targets:")
    print("  Script-A: 2021-01-01 → 2022-12-31  (~484 days, completely missing)")
    print("  Script-B: 2024-01-17 → 2025-10-31  (~432 days, 2023 done, picking up from Jan-16)")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    main()
