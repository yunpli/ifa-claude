"""PIT SW membership materialization."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text

from ifa.families.sme.data.calendar import trading_dates


def compute_membership(engine, *, start: dt.date, end: dt.date, source_mode: str = "prefer_smartmoney", run_id: str | None = None) -> int:
    dates = trading_dates(engine, start, end)
    if not dates:
        return 0

    sql = text("""
        INSERT INTO sme.sme_sw_member_daily (
            trade_date, ts_code, name, l1_code, l1_name, l2_code, l2_name,
            l3_code, l3_name, in_date, out_date, source_mode, source_snapshot_id,
            quality_flag, computed_at
        )
        SELECT
            d.trade_date,
            m.ts_code,
            m.name,
            m.l1_code,
            m.l1_name,
            m.l2_code,
            m.l2_name,
            m.l3_code,
            m.l3_name,
            m.in_date,
            m.out_date,
            :source_mode,
            :run_id,
            'ok',
            now()
        FROM (SELECT unnest(CAST(:dates AS date[])) AS trade_date) d
        JOIN smartmoney.raw_sw_member m
          ON m.in_date <= d.trade_date
         AND (m.out_date IS NULL OR m.out_date > d.trade_date)
         AND m.l2_code IS NOT NULL
        ON CONFLICT (trade_date, l2_code, ts_code) DO UPDATE SET
            name = EXCLUDED.name,
            l1_code = EXCLUDED.l1_code,
            l1_name = EXCLUDED.l1_name,
            l2_name = EXCLUDED.l2_name,
            l3_code = EXCLUDED.l3_code,
            l3_name = EXCLUDED.l3_name,
            in_date = EXCLUDED.in_date,
            out_date = EXCLUDED.out_date,
            source_mode = EXCLUDED.source_mode,
            source_snapshot_id = EXCLUDED.source_snapshot_id,
            quality_flag = EXCLUDED.quality_flag,
            computed_at = now()
    """)
    with engine.begin() as conn:
        result = conn.execute(sql, {"dates": dates, "source_mode": source_mode, "run_id": run_id})
    return int(result.rowcount or 0)
