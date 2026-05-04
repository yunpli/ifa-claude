"""TA family daily ETL runner.

Run after the SmartMoney daily ETL completes (so raw_daily / raw_top_inst /
raw_kpl_list / raw_moneyflow are fresh). Calls in dependency order:

  1. fetch_factor_pro       — 80 derived factors (MACD/RSI/MA/ATR proxy)
  2. fetch_cyq_perf         — chip distribution
  3. fetch_suspend_limit    — suspension + price limits
  4. fetch_event_signals    — earnings forecast/express/disclosure
  5. fetch_blacklist        — anns_d adverse-event scan + forecast losses

Then optional weekend tasks:

  · fetch_fina_indicator (quarterly cadence) — ROE / EPS / margins
  · coverage_check         — alert if today's candidate count < threshold
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.tushare.client import TuShareClient

log = logging.getLogger(__name__)


def run_ta_daily_etls(
    client: TuShareClient,
    engine: Engine,
    *,
    trade_date: date,
    skip_factor_pro: bool = False,
    skip_cyq: bool = False,
    skip_suspend: bool = False,
    skip_events: bool = False,
    skip_blacklist: bool = False,
) -> dict[str, int]:
    """Run all TA daily ETLs for `trade_date`. Returns per-source row counts."""
    out: dict[str, int] = {}

    if not skip_factor_pro:
        try:
            from ifa.families.ta.etl.factor_pro import fetch_and_store_factor_pro
            out["factor_pro"] = fetch_and_store_factor_pro(engine, trade_date)
        except Exception as e:
            log.warning("factor_pro skipped: %s", e)
            out["factor_pro"] = -1
    if not skip_cyq:
        try:
            from ifa.families.ta.etl.cyq import fetch_cyq_perf_full_market
            out["cyq_perf"] = fetch_cyq_perf_full_market(engine, trade_date)
        except Exception as e:
            log.warning("cyq skipped: %s", e)
            out["cyq_perf"] = -1
    if not skip_suspend:
        try:
            from ifa.families.ta.etl.suspend_limit import fetch_and_store_all
            sl = fetch_and_store_all(engine, trade_date)
            out["suspend_limit"] = sum(sl.values()) if isinstance(sl, dict) else int(sl or 0)
        except Exception as e:
            log.warning("suspend skipped: %s", e)
            out["suspend_limit"] = -1
    if not skip_events:
        from ifa.families.ta.etl.event_etl import fetch_event_signals
        out["event_signals"] = fetch_event_signals(
            client, engine, trade_date=trade_date,
        )
    if not skip_blacklist:
        from ifa.families.ta.etl.blacklist_etl import fetch_blacklist
        out["blacklist"] = fetch_blacklist(
            client, engine, trade_date=trade_date,
        )

    log.info("TA daily ETL %s: %s", trade_date, out)
    return out


def coverage_check(
    engine: Engine,
    *,
    on_date: date,
    lookback_days: int = 30,
    min_monthly_coverage: int = 30,
) -> dict[str, dict]:
    """For each setup, count distinct trade dates with ≥1 candidate in the
    lookback window. Setups below threshold (parameter too tight) get flagged.

    Returns {setup_name: {trade_dates_with_hits, total_candidates, status}}.
    """
    sql = text("""
        SELECT setup_name,
               COUNT(DISTINCT trade_date) AS n_dates,
               COUNT(*) AS n_total
        FROM ta.candidates_daily
        WHERE trade_date > :start AND trade_date <= :on_date
        GROUP BY setup_name
        ORDER BY n_total DESC
    """)
    out: dict[str, dict] = {}
    with engine.connect() as conn:
        for r in conn.execute(sql, {
            "start": on_date - timedelta(days=lookback_days),
            "on_date": on_date,
        }):
            setup, n_dates, n_total = r[0], int(r[1]), int(r[2])
            status = "ok"
            if n_total < min_monthly_coverage:
                status = "low_coverage"
            elif n_total < 5:
                status = "starved"
            out[setup] = {
                "trade_dates_with_hits": n_dates,
                "total_candidates": n_total,
                "status": status,
            }
    return out
