"""TA ETL: fetch ths_hot + dc_hot and store into ta.hot_rank_daily.

Unit notes (tushare-units-reference.md §2.11):
  · hot: integer heat score (no unit conversion needed)
  · rank: integer rank (no conversion)
  · pct_change: 0-100 pct (already in correct format)
  · current_price: 元/股 (not stored in hot_rank — use factor_pro instead)
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
def _pull_ths_hot(trade_date: str, data_type: str) -> pd.DataFrame:
    return _get_pro().ths_hot(trade_date=trade_date, data_type=data_type)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _pull_dc_hot(trade_date: str, data_type: str) -> pd.DataFrame:
    return _get_pro().dc_hot(trade_date=trade_date, data_type=data_type)


def _upsert_rows(engine: Engine, rows: list[dict]) -> int:
    if not rows:
        return 0
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ta.hot_rank_daily
                    (trade_date, src, data_type, ts_code, rank, hot, pct_change_pct)
                VALUES (:trade_date, :src, :data_type, :ts_code, :rank, :hot, :pct_change_pct)
                ON CONFLICT (trade_date, src, data_type, ts_code) DO UPDATE SET
                    rank = EXCLUDED.rank,
                    hot = EXCLUDED.hot,
                    pct_change_pct = EXCLUDED.pct_change_pct
            """),
            rows,
        )
    return len(rows)


def fetch_and_store_hot_rank(engine: Engine, trade_date: date | str) -> int:
    """Fetch THS + DC hot rank for *trade_date*. Returns total rows written."""
    td = trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")
    total = 0

    # THS hot — data_type: 'N' normal hot, 'Z' concept hot
    for dtype in ("N", "Z"):
        try:
            df = _pull_ths_hot(td, dtype)
            if df is not None and not df.empty:
                rows = [
                    {
                        "trade_date": td,
                        "src": "ths",
                        "data_type": dtype,
                        "ts_code": str(r.get("ts_code", "")),
                        "rank": int(r["rank"]) if r.get("rank") is not None and not pd.isna(r["rank"]) else None,
                        "hot": _safe(r.get("hot")),
                        "pct_change_pct": _safe(r.get("pct_change")),
                    }
                    for _, r in df.iterrows()
                    if r.get("ts_code")
                ]
                total += _upsert_rows(engine, rows)
        except Exception as exc:
            log.warning("ths_hot %s dtype=%s: %s", td, dtype, exc)

    # DC hot — data_type: 'N' normal hot
    for dtype in ("N",):
        try:
            df = _pull_dc_hot(td, dtype)
            if df is not None and not df.empty:
                rows = [
                    {
                        "trade_date": td,
                        "src": "dc",
                        "data_type": dtype,
                        "ts_code": str(r.get("ts_code", "")),
                        "rank": int(r["rank"]) if r.get("rank") is not None and not pd.isna(r["rank"]) else None,
                        "hot": _safe(r.get("hot")),
                        "pct_change_pct": _safe(r.get("pct_change")),
                    }
                    for _, r in df.iterrows()
                    if r.get("ts_code")
                ]
                total += _upsert_rows(engine, rows)
        except Exception as exc:
            log.warning("dc_hot %s dtype=%s: %s", td, dtype, exc)

    log.info("hot_rank: %d rows for %s", total, td)
    return total
