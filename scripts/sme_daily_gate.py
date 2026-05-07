#!/usr/bin/env python3
"""SME daily production gate.

Third-party schedulers may call SME jobs every calendar day. This helper is the
first step in those jobs: it checks the current Beijing date against
``smartmoney.trade_cal`` and returns a structured run/skip decision. This keeps
non-trading days out of ETL/report generation while still giving delivery
agents a machine-readable message to send.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from ifa.core.calendar import is_trading_day, prev_trading_day, today_bjt
from ifa.core.db import get_engine


def _parse_date(value: str | None) -> dt.date:
    if not value or value == "auto":
        return today_bjt()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError("date must be YYYY-MM-DD, YYYYMMDD, or auto")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="auto", help="Beijing date to check; default today BJT")
    parser.add_argument("--kind", choices=("incremental", "brief"), required=True)
    parser.add_argument(
        "--brief-target",
        choices=("same-day", "previous-trading-day"),
        default="same-day",
        help=(
            "For kind=brief: same-day is for evening post-ETL reports; "
            "previous-trading-day is for legacy early-morning reports."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()

    check_date = _parse_date(args.date)
    engine = get_engine()

    try:
        open_day = is_trading_day(engine, check_date)
        target_trade_date = None
        if open_day:
            if args.kind == "incremental" or args.brief_target == "same-day":
                target_trade_date = check_date
            else:
                target_trade_date = prev_trading_day(engine, check_date)
        payload = {
            "status": "trade_day" if open_day else "non_trade_day",
            "action": "run" if open_day else "skip",
            "job_kind": args.kind,
            "timezone": "Asia/Shanghai",
            "check_date": check_date.isoformat(),
            "is_trading_day": open_day,
            "brief_target": args.brief_target if args.kind == "brief" else None,
            "target_trade_date": target_trade_date.isoformat() if target_trade_date else None,
            "message": (
                f"{check_date.isoformat()} is a trading day; run SME {args.kind}."
                if open_day
                else f"{check_date.isoformat()} is not an A-share trading day; skip SME {args.kind}."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "error",
            "action": "fail",
            "job_kind": args.kind,
            "timezone": "Asia/Shanghai",
            "check_date": check_date.isoformat(),
            "is_trading_day": None,
            "target_trade_date": None,
            "message": f"{type(exc).__name__}: {exc}",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.json else None), file=sys.stderr)
        return 2

    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.json else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
