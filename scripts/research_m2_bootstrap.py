"""Bootstrap company_identity + api_cache for 3 test stocks.

Run this ONCE before research_m2_smoketest.py — it:
  1. Calls Tushare stock_basic to learn name/exchange for each ts_code
  2. Upserts research.company_identity
  3. Runs fetch_all (23 APIs) to populate research.api_cache

Idempotent: safe to re-run; cache hits skip the network.
"""
from __future__ import annotations

import logging
from datetime import date

from ifa.core.db import get_engine
from ifa.families.research.fetcher.client import fetch_all, fetch_stock_basic
from ifa.families.research.resolver import upsert_company_identity

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bootstrap")

TEST_TS_CODES = [
    "001339.SZ",   # 智微智能
    "301486.SZ",   # 致尚科技
    "002938.SZ",   # 鹏鼎控股
]


def _parse_yyyymmdd(s: str | None) -> date | None:
    if not s or len(s) < 8:
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def bootstrap() -> None:
    engine = get_engine()
    for ts_code in TEST_TS_CODES:
        log.info("─── %s ─────────────────────────────────", ts_code)

        # 1. stock_basic for identity
        try:
            sb = fetch_stock_basic(engine, ts_code)
        except Exception as e:
            log.error("stock_basic failed for %s: %s", ts_code, e)
            continue
        if not sb:
            log.warning("stock_basic returned empty for %s — skipping", ts_code)
            continue

        first = sb[0]
        name = str(first.get("name") or "")
        exchange = str(first.get("exchange") or "")
        market = str(first.get("market") or "") or None
        list_status = str(first.get("list_status") or "") or None
        list_date = _parse_yyyymmdd(str(first.get("list_date") or ""))

        log.info("identity: %s / %s / exchange=%s", ts_code, name, exchange)
        upsert_company_identity(
            engine, ts_code=ts_code, name=name, exchange=exchange,
            market=market, list_date=list_date, list_status=list_status,
        )

        # 2. fetch_all (cached after first run)
        log.info("running fetch_all (23 APIs)…")
        results = fetch_all(engine, ts_code, exchange, verbose=True)
        ok = sum(1 for v in results.values() if v)
        log.info("done: %d/%d APIs returned data", ok, len(results))


if __name__ == "__main__":
    bootstrap()
