"""Pre-market data warmer — run once around 09:25 BJT on a trading day.

Pulls today's `stk_limit` (whole-A up/down limit prices) into a CSV cache file
so noon/evening report-time aggregation can join `rt_k` snapshots with limit
prices without re-hitting the API. Also useful as a connectivity smoke test.

Schedule (integrator's responsibility — examples only):
  cron:   25 9 * * 1-5 cd /path/to/ifa-claude && uv run python scripts/premarket_warm_cache.py
  launchd: <key>StartCalendarInterval</key> with Hour=9 Minute=25 Weekday=1..5

Flags:
  --date YYYY-MM-DD   default: today (BJT)
  --out PATH          default: var/cache/stk_limit_<date>.csv
  --skip-on-holiday   exit 0 silently if it's a non-trading day per smartmoney.trade_cal

The report code does NOT depend on this cache file being present — it falls
back to a live `stk_limit` API call. The cache only saves ~1.5s on hot path.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from ifa.core.report.timezones import BJT
from ifa.core.tushare import TuShareClient


def _is_trading_day(on_date: dt.date) -> bool:
    try:
        from ifa.core.calendar import is_trade_day  # type: ignore
        return is_trade_day(on_date)
    except Exception:
        # Conservative: assume Mon-Fri is a trading day if calendar helper missing
        return on_date.weekday() < 5


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today BJT)")
    ap.add_argument("--out", default=None, help="output CSV path")
    ap.add_argument("--skip-on-holiday", action="store_true")
    args = ap.parse_args()

    on_date = (
        dt.datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date else dt.datetime.now(BJT).date()
    )

    if args.skip_on_holiday and not _is_trading_day(on_date):
        print(f"[premarket] {on_date} is not a trading day — skipping")
        return 0

    out_path = Path(args.out) if args.out else (
        Path(__file__).parent.parent / "var" / "cache" / f"stk_limit_{on_date.isoformat()}.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    client = TuShareClient()
    df = client.call("stk_limit", trade_date=on_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        print(f"[premarket] stk_limit empty for {on_date} — TuShare may not have published yet")
        return 1
    df.to_csv(out_path, index=False)
    print(f"[premarket] wrote {len(df)} rows → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
