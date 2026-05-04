"""TA ETL: fetch suspend + stk_limit_d and store into ta.suspend_daily / ta.stk_limit_daily.

Unit notes:
  · suspend: no monetary fields, all text/date
  · stk_limit_d:
    - close: 元/股 (stored as-is)
    - pct_chg: 0-100 pct (stored as pct_chg_pct)
    - fd_amount: 元 (from tushare-units-reference: limit_list_d.amount is 元)
    - amp: 0-100 pct
    - fc_ratio, fl_ratio: 比例 (stored as-is)
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
import tushare as ts
from sqlalchemy import text
from sqlalchemy.engine import Engine
from tenacity import retry, stop_after_attempt, wait_exponential

from ifa.config import get_settings

log = logging.getLogger(__name__)

_pro: Any = None


def _get_pro() -> Any:
    global _pro
    if _pro is None:
        settings = get_settings()
        ts.set_token(settings.tushare_token.get_secret_value())
        _pro = ts.pro_api()
    return _pro


def _safe(val: Any) -> Decimal | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    return Decimal(str(val))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _pull_suspend(trade_date: str) -> pd.DataFrame:
    return _get_pro().suspend_d(trade_date=trade_date)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _pull_limit(trade_date: str) -> pd.DataFrame:
    return _get_pro().limit_list_d(trade_date=trade_date)


def fetch_and_store_suspend(engine: Engine, trade_date: date | str) -> int:
    td = trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")
    try:
        df = _pull_suspend(td)
    except Exception as exc:
        log.warning("suspend_d %s: %s", td, exc)
        return 0
    if df is None or df.empty:
        return 0

    rows = [
        {
            "trade_date": td,
            "ts_code": str(r["ts_code"]),
            "suspend_type": str(r.get("suspend_type", "")) or None,
            "suspend_timing": str(r.get("suspend_timing", "")) or None,
        }
        for _, r in df.iterrows()
        if r.get("ts_code")
    ]
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ta.suspend_daily (trade_date, ts_code, suspend_type, suspend_timing)
                VALUES (:trade_date, :ts_code, :suspend_type, :suspend_timing)
                ON CONFLICT (trade_date, ts_code) DO UPDATE SET
                    suspend_type = EXCLUDED.suspend_type,
                    suspend_timing = EXCLUDED.suspend_timing
            """),
            rows,
        )
    log.info("suspend: %d rows for %s", len(rows), td)
    return len(rows)


def fetch_and_store_limit(engine: Engine, trade_date: date | str) -> int:
    td = trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")
    try:
        df = _pull_limit(td)
    except Exception as exc:
        log.warning("limit_list_d %s: %s", td, exc)
        return 0
    if df is None or df.empty:
        return 0

    rows = []
    for _, r in df.iterrows():
        if not r.get("ts_code"):
            continue
        rows.append({
            "trade_date": td,
            "ts_code": str(r["ts_code"]),
            "trade_date_str": str(r.get("trade_date", td)),
            "name": str(r.get("name", "")) or None,
            "close": _safe(r.get("close")),
            "pct_chg_pct": _safe(r.get("pct_chg")),
            "amp": _safe(r.get("amp")),
            "fc_ratio": _safe(r.get("fc_ratio")),
            "fl_ratio": _safe(r.get("fl_ratio")),
            # fd_amount: limit_list_d.amount is 元 (tushare-units-reference §2.7)
            "fd_amount_yuan": _safe(r.get("fd_amount")),
            "first_time": str(r.get("first_time", "")) or None,
            "last_time": str(r.get("last_time", "")) or None,
            "open_times": int(r["open_times"]) if r.get("open_times") is not None and not pd.isna(r["open_times"]) else None,
            "strth": _safe(r.get("strth")),
            "limit": str(r.get("limit", "")) or None,
        })

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ta.stk_limit_daily
                    (trade_date, ts_code, trade_date_str, name, close, pct_chg_pct,
                     amp, fc_ratio, fl_ratio, fd_amount_yuan,
                     first_time, last_time, open_times, strth, limit)
                VALUES
                    (:trade_date, :ts_code, :trade_date_str, :name, :close, :pct_chg_pct,
                     :amp, :fc_ratio, :fl_ratio, :fd_amount_yuan,
                     :first_time, :last_time, :open_times, :strth, :limit)
                ON CONFLICT (trade_date, ts_code) DO UPDATE SET
                    name = EXCLUDED.name,
                    close = EXCLUDED.close,
                    pct_chg_pct = EXCLUDED.pct_chg_pct,
                    amp = EXCLUDED.amp,
                    fc_ratio = EXCLUDED.fc_ratio,
                    fl_ratio = EXCLUDED.fl_ratio,
                    fd_amount_yuan = EXCLUDED.fd_amount_yuan,
                    first_time = EXCLUDED.first_time,
                    last_time = EXCLUDED.last_time,
                    open_times = EXCLUDED.open_times,
                    strth = EXCLUDED.strth,
                    limit = EXCLUDED.limit
            """),
            rows,
        )
    log.info("stk_limit: %d rows for %s", len(rows), td)
    return len(rows)


def fetch_and_store_all(engine: Engine, trade_date: date | str) -> dict[str, int]:
    """Fetch both suspend and limit data for *trade_date*."""
    return {
        "suspend": fetch_and_store_suspend(engine, trade_date),
        "limit": fetch_and_store_limit(engine, trade_date),
    }
