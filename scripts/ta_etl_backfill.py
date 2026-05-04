"""TA ETL backfill — populate ta.factor_pro_daily + ta.cyq_perf_daily for [start, end].

Single Tushare call per trade_date for each table (full-market batch).

Run:
    uv run python scripts/ta_etl_backfill.py --start 2026-01-02 --end 2026-04-30
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date

from ifa.core.calendar import trading_days_between
from ifa.core.db import get_engine
from ifa.families.ta.etl.cyq import fetch_cyq_perf_full_market
from ifa.families.ta.etl.factor_pro import fetch_and_store_factor_pro

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    args = p.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    engine = get_engine()
    days = trading_days_between(engine, start, end)
    log.info("ETL backfill %s → %s · %d trade days", start, end, len(days))

    t0 = time.time()
    n_factor = 0
    n_cyq = 0
    for i, d in enumerate(days, 1):
        try:
            n = fetch_and_store_factor_pro(engine, d)
            n_factor += n
        except Exception as e:
            log.warning("factor_pro %s failed: %s", d, e)
        try:
            n = fetch_cyq_perf_full_market(engine, d)
            n_cyq += n
        except Exception as e:
            log.warning("cyq_perf %s failed: %s", d, e)
        if i % 5 == 0 or i == len(days):
            log.info("  %d/%d %s · factor=%d cyq=%d · %.1fs",
                     i, len(days), d, n_factor, n_cyq, time.time() - t0)

    log.info("DONE.  factor_pro %d rows · cyq_perf %d rows · %.1f min",
             n_factor, n_cyq, (time.time() - t0) / 60)


if __name__ == "__main__":
    main()
