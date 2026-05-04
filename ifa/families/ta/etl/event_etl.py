"""TA ETL: pull earnings/disclosure events from Tushare → ta.event_signal_daily.

Three sources (Tushare Pro):
  · forecast (业绩预告) — period × ts_code × ann_date with p_change_min/max
  · express  (业绩快报) — period × ts_code × ann_date with revenue/profit yoy
  · disclosure_date    — pre_date / actual_date for upcoming earnings windows

Strategy:
  · For a given trade_date, find events whose ann_date == trade_date,
    or whose pre/actual disclosure date falls within [trade_date, trade_date+10].
  · Polarity from forecast.p_change_min: positive if >= 30%, negative if <= 0,
    else neutral. For express, use n_income yoy proxy.
  · disclosure_pre events: polarity null, days_to_disclosure populated.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.tushare.client import TuShareClient

log = logging.getLogger(__name__)

_UPSERT = text("""
    INSERT INTO ta.event_signal_daily
        (trade_date, ts_code, event_type, polarity, days_to_disclosure,
         ref_value, source_ann_date)
    VALUES (:trade_date, :ts_code, :event_type, :polarity, :days,
            :ref_value, :source_ann_date)
    ON CONFLICT (trade_date, ts_code, event_type) DO UPDATE SET
        polarity = EXCLUDED.polarity,
        days_to_disclosure = EXCLUDED.days_to_disclosure,
        ref_value = EXCLUDED.ref_value,
        source_ann_date = EXCLUDED.source_ann_date
""")


def _polarity_from_pct(p: float | None) -> str | None:
    if p is None:
        return None
    if p >= 30:
        return "positive"
    if p <= 0:
        return "negative"
    return "neutral"


def fetch_event_signals(
    client: TuShareClient,
    engine: Engine,
    *,
    trade_date: date,
) -> int:
    """Populate ta.event_signal_daily for a single trade_date. Returns rows written."""
    yyyymmdd = trade_date.strftime("%Y%m%d")
    end_window = (trade_date + timedelta(days=14)).strftime("%Y%m%d")
    rows: list[dict[str, Any]] = []

    # ── 1. forecast — events whose ann_date == trade_date ───────────────────
    try:
        df = client.call("forecast", ann_date=yyyymmdd)
        for _, r in df.iterrows():
            rows.append({
                "trade_date": trade_date,
                "ts_code": r.get("ts_code"),
                "event_type": "forecast",
                "polarity": _polarity_from_pct(_safe_float(r.get("p_change_min"))),
                "days": None,
                "ref_value": _safe_float(r.get("p_change_min")),
                "source_ann_date": _parse_yyyymmdd(r.get("ann_date")),
            })
    except Exception as e:
        log.warning("forecast fetch failed for %s: %s", trade_date, e)

    # ── 2. express ───────────────────────────────────────────────────────────
    try:
        df = client.call("express", ann_date=yyyymmdd)
        for _, r in df.iterrows():
            yoy = _safe_float(r.get("yoy_net_profit"))
            rows.append({
                "trade_date": trade_date,
                "ts_code": r.get("ts_code"),
                "event_type": "express",
                "polarity": _polarity_from_pct(yoy),
                "days": None,
                "ref_value": yoy,
                "source_ann_date": _parse_yyyymmdd(r.get("ann_date")),
            })
    except Exception as e:
        log.warning("express fetch failed for %s: %s", trade_date, e)

    # ── 3. disclosure_date — upcoming windows within +14 days ────────────────
    try:
        df = client.call("disclosure_date", end_date=end_window)
        for _, r in df.iterrows():
            pre = _parse_yyyymmdd(r.get("pre_date"))
            actual = _parse_yyyymmdd(r.get("actual_date"))
            target = actual or pre
            if not target:
                continue
            d2d = (target - trade_date).days
            if d2d < 0 or d2d > 14:
                continue
            rows.append({
                "trade_date": trade_date,
                "ts_code": r.get("ts_code"),
                "event_type": "disclosure_pre",
                "polarity": None,
                "days": d2d,
                "ref_value": None,
                "source_ann_date": target,
            })
    except Exception as e:
        log.warning("disclosure_date fetch failed for %s: %s", trade_date, e)

    if not rows:
        return 0
    written = 0
    with engine.begin() as conn:
        for row in rows:
            if not row.get("ts_code"):
                continue
            conn.execute(_UPSERT, row)
            written += 1
    log.info("event_signal_daily: %d rows for %s", written, trade_date)
    return written


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _parse_yyyymmdd(s: Any) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(f"{str(s)[:4]}-{str(s)[4:6]}-{str(s)[6:8]}")
    except Exception:
        return None
