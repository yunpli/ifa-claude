"""Load RegimeContext from smartmoney source tables.

Single SQL per source, no caching. Missing fields stay None — classifier
falls back to high_difficulty when too little data is present.

Sources:
  · smartmoney.raw_index_daily (ts_code='000001.SH') — SSE close + 20d MA + vol
  · smartmoney.market_state_daily — breadth, limit-up/down, amount, consecutive lb
  · smartmoney.raw_moneyflow_hsgt — north-flow + 60d percentile
  · smartmoney.raw_sw_daily — sector pct_change std (L1 only)
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.ta.regime.classifier import RegimeContext

log = logging.getLogger(__name__)



def load_regime_context(engine: Engine, on_date: date) -> RegimeContext:
    """Build a RegimeContext from DB rows for `on_date`."""
    ctx = RegimeContext(trade_date=on_date)

    _fill_index(engine, on_date, ctx)
    _fill_market_state(engine, on_date, ctx)
    _fill_hsgt(engine, on_date, ctx)
    _fill_sector_dispersion(engine, on_date, ctx)

    return ctx


def _fill_index(engine: Engine, on_date: date, ctx: RegimeContext) -> None:
    """SSE composite (000001.SH): close, MA5, MA20, MA20_prev, 20d vol."""
    sql = text("""
        WITH idx AS (
            SELECT trade_date, close, pct_chg
            FROM smartmoney.raw_index_daily
            WHERE ts_code = '000001.SH'
              AND trade_date <= :on_date
            ORDER BY trade_date DESC
            LIMIT 25
        ),
        ordered AS (
            SELECT trade_date, close, pct_chg
            FROM idx ORDER BY trade_date ASC
        )
        SELECT
            close,
            AVG(close)  OVER (ORDER BY trade_date ROWS BETWEEN  4 PRECEDING AND CURRENT ROW) AS ma5,
            AVG(close)  OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
            AVG(close)  OVER (ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS ma20_prev,
            STDDEV(pct_chg) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                AS vol_pct,
            trade_date
        FROM ordered
        ORDER BY trade_date DESC
        LIMIT 1
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"on_date": on_date}).fetchone()
    if not row:
        return
    ctx.sse_close = float(row[0]) if row[0] is not None else None
    ctx.sse_ma5 = float(row[1]) if row[1] is not None else None
    ctx.sse_ma20 = float(row[2]) if row[2] is not None else None
    ctx.sse_ma20_prev = float(row[3]) if row[3] is not None else None
    ctx.sse_volatility_20d_pct = float(row[4]) if row[4] is not None else None


def _fill_market_state(engine: Engine, on_date: date, ctx: RegimeContext) -> None:
    """Breadth + limit-up/down + amount + consecutive_lb_high. Requires exact-date row."""
    sql_today = text("""
        SELECT up_count, down_count, limit_up_count, limit_down_count,
               max_consecutive_limit_up, total_amount, amount_10d_avg
        FROM smartmoney.market_state_daily
        WHERE trade_date = :on_date
    """)
    sql_prev = text("""
        SELECT limit_up_count
        FROM smartmoney.market_state_daily
        WHERE trade_date < :on_date
        ORDER BY trade_date DESC LIMIT 1
    """)
    with engine.connect() as conn:
        today = conn.execute(sql_today, {"on_date": on_date}).fetchone()
        if not today:
            return
        prev = conn.execute(sql_prev, {"on_date": on_date}).fetchone()
    ctx.n_up = int(today[0]) if today[0] is not None else None
    ctx.n_down = int(today[1]) if today[1] is not None else None
    ctx.n_limit_up = int(today[2]) if today[2] is not None else None
    ctx.n_limit_down = int(today[3]) if today[3] is not None else None
    ctx.consecutive_lb_high = int(today[4]) if today[4] is not None else None
    ctx.market_amount_yuan = float(today[5]) if today[5] is not None else None
    ctx.market_amount_yuan_ma20 = float(today[6]) if today[6] is not None else None
    if prev and prev[0] is not None:
        ctx.n_limit_up_prev = int(prev[0])


def _fill_hsgt(engine: Engine, on_date: date, ctx: RegimeContext) -> None:
    """Northbound flow today + 60d percentile."""
    sql = text("""
        WITH window60 AS (
            SELECT trade_date, north_money
            FROM smartmoney.raw_moneyflow_hsgt
            WHERE trade_date <= :on_date
            ORDER BY trade_date DESC
            LIMIT 60
        )
        SELECT
            (SELECT north_money FROM window60 ORDER BY trade_date DESC LIMIT 1) AS today_net,
            (SELECT
                100.0 * COUNT(*) FILTER (WHERE w.north_money <= t.nm) / NULLIF(COUNT(*), 0)
             FROM window60 w, (SELECT north_money AS nm FROM window60 ORDER BY trade_date DESC LIMIT 1) t
            ) AS pct_60d
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"on_date": on_date}).fetchone()
    if not row:
        return
    if row[0] is not None:
        ctx.hsgt_net_amount_yuan = float(row[0])
    if row[1] is not None:
        ctx.hsgt_net_pct_60d = float(row[1])


def _fill_sector_dispersion(engine: Engine, on_date: date, ctx: RegimeContext) -> None:
    """Std dev of SW L1 sector pct_change for the day (31 L1 industries)."""
    sql = text("""
        SELECT STDDEV(pct_change)
        FROM smartmoney.raw_sw_daily
        WHERE trade_date = :on_date
          AND ts_code IN (SELECT DISTINCT l1_code FROM smartmoney.sw_member_monthly)
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"on_date": on_date}).fetchone()
    if row and row[0] is not None:
        ctx.sector_pct_change_std = float(row[0])
