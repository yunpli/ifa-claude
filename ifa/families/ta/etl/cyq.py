"""TA ETL: fetch cyq_chips + cyq_perf and store into ta.cyq_chips_daily / ta.cyq_perf_daily.

Unit conversions (data-accuracy-guidelines.md Rule 2):
  · cyq_chips.percent: Tushare returns 0-1 decimal → × 100 → 0-100 pct, stored as percent_pct
  · cyq_perf.winner_rate: also 0-1 → × 100 → stored as winner_rate_pct
  · cyq_perf prices (his_low, his_high, cost_*pct, weight_avg): 元/股, stored as-is
"""
from __future__ import annotations

import json
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
def _pull_chips(ts_code: str, trade_date: str) -> pd.DataFrame:
    return _get_pro().cyq_chips(ts_code=ts_code, trade_date=trade_date)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _pull_perf(ts_code: str, trade_date: str) -> pd.DataFrame:
    return _get_pro().cyq_perf(ts_code=ts_code, trade_date=trade_date)


def fetch_and_store_cyq(engine: Engine, ts_code: str, trade_date: date | str) -> tuple[bool, bool]:
    """Fetch cyq_chips + cyq_perf for one stock on one date. Returns (chips_ok, perf_ok)."""
    td = trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")
    chips_ok = _store_chips(engine, ts_code, td)
    perf_ok = _store_perf(engine, ts_code, td)
    return chips_ok, perf_ok


def _store_chips(engine: Engine, ts_code: str, trade_date: str) -> bool:
    try:
        df = _pull_chips(ts_code, trade_date)
    except Exception as exc:
        log.warning("cyq_chips fetch failed %s %s: %s", ts_code, trade_date, exc)
        return False
    if df is None or df.empty:
        return False

    # Convert: percent 0-1 → 0-100
    chips = []
    for _, r in df.iterrows():
        raw_pct = r.get("percent")
        pct_0_100 = float(_safe(raw_pct) * 100) if _safe(raw_pct) is not None else None
        price = float(_safe(r.get("price"))) if _safe(r.get("price")) is not None else None
        if price is not None:
            chips.append({"price": price, "percent_pct": pct_0_100})

    chips_json = json.dumps(chips)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ta.cyq_chips_daily (trade_date, ts_code, chips_json)
                VALUES (:td, :tc, :cj::jsonb)
                ON CONFLICT (trade_date, ts_code) DO UPDATE SET chips_json = EXCLUDED.chips_json
            """),
            {"td": trade_date, "tc": ts_code, "cj": chips_json},
        )
    return True


def _store_perf(engine: Engine, ts_code: str, trade_date: str) -> bool:
    try:
        df = _pull_perf(ts_code, trade_date)
    except Exception as exc:
        log.warning("cyq_perf fetch failed %s %s: %s", ts_code, trade_date, exc)
        return False
    if df is None or df.empty:
        return False

    r = df.iloc[0]
    raw_wr = r.get("winner_rate")
    winner_rate_pct = _safe(raw_wr) * 100 if _safe(raw_wr) is not None else None

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ta.cyq_perf_daily
                    (trade_date, ts_code,
                     his_low, his_high,
                     cost_5pct, cost_15pct, cost_50pct, cost_85pct, cost_95pct,
                     weight_avg, winner_rate_pct)
                VALUES
                    (:td, :tc,
                     :his_low, :his_high,
                     :c5, :c15, :c50, :c85, :c95,
                     :wa, :wr)
                ON CONFLICT (trade_date, ts_code) DO UPDATE SET
                    his_low = EXCLUDED.his_low,
                    his_high = EXCLUDED.his_high,
                    cost_5pct = EXCLUDED.cost_5pct,
                    cost_15pct = EXCLUDED.cost_15pct,
                    cost_50pct = EXCLUDED.cost_50pct,
                    cost_85pct = EXCLUDED.cost_85pct,
                    cost_95pct = EXCLUDED.cost_95pct,
                    weight_avg = EXCLUDED.weight_avg,
                    winner_rate_pct = EXCLUDED.winner_rate_pct
            """),
            {
                "td": trade_date, "tc": ts_code,
                "his_low": _safe(r.get("his_low")),
                "his_high": _safe(r.get("his_high")),
                "c5": _safe(r.get("cost_5pct")),
                "c15": _safe(r.get("cost_15pct")),
                "c50": _safe(r.get("cost_50pct")),
                "c85": _safe(r.get("cost_85pct")),
                "c95": _safe(r.get("cost_95pct")),
                "wa": _safe(r.get("weight_avg")),
                "wr": winner_rate_pct,
            },
        )
    return True


def fetch_cyq_batch(engine: Engine, ts_codes: list[str], trade_date: date | str) -> dict[str, tuple[bool, bool]]:
    """Fetch cyq for a batch of stocks. Returns {ts_code: (chips_ok, perf_ok)}."""
    td = trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")
    results = {}
    for ts_code in ts_codes:
        results[ts_code] = fetch_and_store_cyq(engine, ts_code, td)
    return results
