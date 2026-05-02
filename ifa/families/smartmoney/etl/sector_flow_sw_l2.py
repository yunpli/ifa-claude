"""SW L2 板块资金流汇总 ETL.

Aggregates raw_moneyflow (individual stock) → sector_moneyflow_sw_daily
using sw_member_monthly PIT membership.

PIT-safe: snapshot_month = date_trunc('month', trade_date)::date ensures
correct SW sector membership for each month, no look-ahead bias.

Usage:
    from ifa.families.smartmoney.etl.sector_flow_sw_l2 import (
        aggregate_sector_flow_sw,
        aggregate_sector_flow_sw_for_date,
    )
    n = aggregate_sector_flow_sw_for_date(engine, date(2026, 4, 30))
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"

_UPSERT_SQL = text(f"""
    INSERT INTO {SCHEMA}.sector_moneyflow_sw_daily
        (trade_date, l2_code, l2_name, l1_code, l1_name,
         net_amount, buy_elg_amount, sell_elg_amount,
         buy_lg_amount, sell_lg_amount, stock_count)
    SELECT
        m.trade_date,
        s.l2_code,
        s.l2_name,
        s.l1_code,
        s.l1_name,
        SUM(m.net_mf_amount)      AS net_amount,
        SUM(m.buy_elg_amount)     AS buy_elg_amount,
        SUM(m.sell_elg_amount)    AS sell_elg_amount,
        SUM(m.buy_lg_amount)      AS buy_lg_amount,
        SUM(m.sell_lg_amount)     AS sell_lg_amount,
        COUNT(DISTINCT m.ts_code) AS stock_count
    FROM {SCHEMA}.raw_moneyflow m
    JOIN {SCHEMA}.sw_member_monthly s
      ON m.ts_code = s.ts_code
     AND s.snapshot_month = date_trunc('month', m.trade_date)::date
    WHERE m.trade_date = ANY(:dates)
    GROUP BY m.trade_date, s.l2_code, s.l2_name, s.l1_code, s.l1_name
    ON CONFLICT (trade_date, l2_code) DO UPDATE SET
        net_amount      = EXCLUDED.net_amount,
        buy_elg_amount  = EXCLUDED.buy_elg_amount,
        sell_elg_amount = EXCLUDED.sell_elg_amount,
        buy_lg_amount   = EXCLUDED.buy_lg_amount,
        sell_lg_amount  = EXCLUDED.sell_lg_amount,
        stock_count     = EXCLUDED.stock_count,
        l2_name         = EXCLUDED.l2_name,
        l1_code         = EXCLUDED.l1_code,
        l1_name         = EXCLUDED.l1_name
""")

_BATCH_SIZE = 90  # days per SQL call — keeps array params manageable


def aggregate_sector_flow_sw(engine: Engine, dates: list[dt.date]) -> int:
    """Aggregate raw_moneyflow → sector_moneyflow_sw_daily for a list of dates.

    Idempotent (ON CONFLICT ... DO UPDATE).  Processes in batches of
    _BATCH_SIZE days.  Returns total rows affected.
    """
    if not dates:
        return 0

    total = 0
    n_batches = (len(dates) + _BATCH_SIZE - 1) // _BATCH_SIZE
    for i in range(0, len(dates), _BATCH_SIZE):
        batch = dates[i : i + _BATCH_SIZE]
        with engine.begin() as conn:
            result = conn.execute(_UPSERT_SQL, {"dates": batch})
            total += result.rowcount
        log.info(
            "[sector_flow_sw] batch %d/%d (%s→%s): %d rows",
            i // _BATCH_SIZE + 1,
            n_batches,
            batch[0],
            batch[-1],
            result.rowcount,
        )
    return total


def aggregate_sector_flow_sw_for_date(engine: Engine, trade_date: dt.date) -> int:
    """Aggregate raw_moneyflow → sector_moneyflow_sw_daily for a single date."""
    return aggregate_sector_flow_sw(engine, [trade_date])
