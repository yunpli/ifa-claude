"""TA ETL: pull quarterly financial indicators → ta.fina_indicator_quarterly.

Periodic ETL (quarterly cadence). Tushare `fina_indicator` returns all
companies for a given period (end_date in YYYYMMDD format like '20260331').

Used by context_loader fundamental_filter to enforce
"近 4 季 ROE 不全部为负" when populated.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.tushare.client import TuShareClient

log = logging.getLogger(__name__)

_UPSERT = text("""
    INSERT INTO ta.fina_indicator_quarterly
        (ts_code, end_date, ann_date,
         roe, roe_dt, eps, netprofit_margin, grossprofit_margin, ar_turn)
    VALUES
        (:ts_code, :end_date, :ann_date,
         :roe, :roe_dt, :eps, :netprofit_margin, :grossprofit_margin, :ar_turn)
    ON CONFLICT (ts_code, end_date) DO UPDATE SET
        ann_date = EXCLUDED.ann_date,
        roe = EXCLUDED.roe,
        roe_dt = EXCLUDED.roe_dt,
        eps = EXCLUDED.eps,
        netprofit_margin = EXCLUDED.netprofit_margin,
        grossprofit_margin = EXCLUDED.grossprofit_margin,
        ar_turn = EXCLUDED.ar_turn
""")


def _safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _parse_yyyymmdd(s):
    if not s:
        return None
    try:
        return date.fromisoformat(f"{str(s)[:4]}-{str(s)[4:6]}-{str(s)[6:8]}")
    except Exception:
        return None


def fetch_fina_indicator_for_period(
    client: TuShareClient,
    engine: Engine,
    *,
    end_date: date,
) -> int:
    """Pull all listed companies' fina_indicator for a single period (e.g. 20260331)."""
    yyyymmdd = end_date.strftime("%Y%m%d")
    try:
        df = client.call(
            "fina_indicator",
            period=yyyymmdd,
            fields="ts_code,end_date,ann_date,roe,roe_dt,eps,"
                   "netprofit_margin,grossprofit_margin,ar_turn",
        )
    except Exception as e:
        log.warning("fina_indicator fetch failed for period %s: %s", end_date, e)
        return 0

    if df is None or len(df) == 0:
        return 0
    n = 0
    with engine.begin() as conn:
        for _, r in df.iterrows():
            ts = r.get("ts_code")
            if not ts:
                continue
            conn.execute(_UPSERT, {
                "ts_code": ts,
                "end_date": _parse_yyyymmdd(r.get("end_date")) or end_date,
                "ann_date": _parse_yyyymmdd(r.get("ann_date")),
                "roe": _safe_float(r.get("roe")),
                "roe_dt": _safe_float(r.get("roe_dt")),
                "eps": _safe_float(r.get("eps")),
                "netprofit_margin": _safe_float(r.get("netprofit_margin")),
                "grossprofit_margin": _safe_float(r.get("grossprofit_margin")),
                "ar_turn": _safe_float(r.get("ar_turn")),
            })
            n += 1
    log.info("fina_indicator: %d rows for period %s", n, end_date)
    return n
