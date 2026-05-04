"""Build SetupContext for every stock on a given trade_date — batch loader.

Sources (current):
  · smartmoney.raw_daily        — 60-day OHLCV per stock; we compute MA5/10/20/60 inline
  · smartmoney.sw_member_monthly — stock → SW L1/L2 mapping
  · smartmoney.raw_sw_daily      — L1/L2 sector pct_change for the day

Sources (deferred — return None until ETL populates):
  · ta.factor_pro_daily          — MACD/RSI/turnover (T3 etc remain inactive)
  · ta.cyq_perf_daily            — chip distribution (C1/C2 inactive)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.ta.regime.classifier import Regime
from ifa.families.ta.setups.base import SetupContext

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 95    # need 60 trade days; 95 calendar accommodates weekends + CN holidays


def build_contexts(
    engine: Engine,
    on_date: date,
    *,
    regime: Regime | None = None,
) -> dict[str, SetupContext]:
    """Returns {ts_code: SetupContext} for every stock with OHLCV on `on_date`.

    Stocks lacking on_date row are excluded. SetupContext closes/highs/lows/volumes
    are tuples ascending by date; today's row sits at index -1.
    """
    cutoff = on_date - timedelta(days=LOOKBACK_DAYS)

    # OHLCV — one wide query, partition by ts_code in Python.
    sql_ohlcv = text("""
        SELECT ts_code, trade_date, open, high, low, close, vol, amount, pre_close
        FROM smartmoney.raw_daily
        WHERE trade_date >= :cutoff AND trade_date <= :on_date
        ORDER BY ts_code, trade_date
    """)
    sql_sector_pct = text("""
        SELECT ts_code, pct_change
        FROM smartmoney.raw_sw_daily
        WHERE trade_date = :on_date
    """)
    sql_member = text("""
        SELECT ts_code, l1_code, l2_code
        FROM smartmoney.sw_member_monthly
        WHERE snapshot_month = date_trunc('month', CAST(:on_date AS date))
    """)

    by_stock: dict[str, list] = defaultdict(list)
    with engine.connect() as conn:
        for row in conn.execute(sql_ohlcv, {"cutoff": cutoff, "on_date": on_date}):
            by_stock[row[0]].append(row)
        sector_pct = {r[0]: float(r[1]) if r[1] is not None else None
                      for r in conn.execute(sql_sector_pct, {"on_date": on_date})}
        members = {r[0]: (r[1], r[2])
                   for r in conn.execute(sql_member, {"on_date": on_date})}

    # Build per-L2 peer dict for sector_peers_pct_change
    l2_to_members: dict[str, list[str]] = defaultdict(list)
    for ts_code, (l1, l2) in members.items():
        if l2:
            l2_to_members[l2].append(ts_code)

    # Compute today's stock pct_change from raw_daily for peer dicts
    stock_pct_today: dict[str, float] = {}
    for ts_code, rows in by_stock.items():
        if not rows or rows[-1][1] != on_date:
            continue
        row = rows[-1]
        pre_close = float(row[8]) if row[8] else None
        close = float(row[5]) if row[5] else None
        if pre_close and close:
            stock_pct_today[ts_code] = (close / pre_close - 1.0) * 100

    contexts: dict[str, SetupContext] = {}
    for ts_code, rows in by_stock.items():
        if not rows or rows[-1][1] != on_date:
            continue
        # Need at least 21 rows for the cheapest setups (T1, R3 etc); cull below
        if len(rows) < 21:
            continue

        closes = tuple(float(r[5]) for r in rows)
        highs = tuple(float(r[3]) for r in rows)
        lows = tuple(float(r[4]) for r in rows)
        volumes = tuple(float(r[6]) for r in rows)

        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None

        # volume_ratio = today's vol / 20-day avg
        avg_vol_20 = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else None
        vol_ratio = volumes[-1] / avg_vol_20 if avg_vol_20 and avg_vol_20 > 0 else None

        l1_code, l2_code = members.get(ts_code, (None, None))
        l1_pct = sector_pct.get(l1_code) if l1_code else None
        l2_pct = sector_pct.get(l2_code) if l2_code else None

        peers = None
        if l2_code and l2_code in l2_to_members:
            peers = {peer: stock_pct_today[peer]
                     for peer in l2_to_members[l2_code]
                     if peer != ts_code and peer in stock_pct_today}
            if not peers:
                peers = None

        contexts[ts_code] = SetupContext(
            ts_code=ts_code,
            trade_date=on_date,
            closes=closes,
            highs=highs,
            lows=lows,
            volumes=volumes,
            close_today=closes[-1],
            ma_qfq_5=ma5,
            ma_qfq_10=ma10,
            ma_qfq_20=ma20,
            ma_qfq_60=ma60,
            volume_ratio=vol_ratio,
            regime=regime,
            sw_l1_code=l1_code,
            sw_l2_code=l2_code,
            sw_l1_pct_change=l1_pct,
            sw_l2_pct_change=l2_pct,
            sector_peers_pct_change=peers,
        )

    log.info("built %d setup contexts for %s", len(contexts), on_date)
    return contexts
