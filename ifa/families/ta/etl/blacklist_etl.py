"""TA ETL: detect adverse events from Tushare → ta.blacklist_daily.

Sources:
  · anns_d (公告日报): scan title keywords for 立案/重组/减持
  · forecast (业绩预告): pull p_change_min < threshold

Run daily after market close. Pairs with context_loader's hard-cut /
soft-warn logic.

Title keyword rules:
  · '立案调查' / '立案侦查' / '被调查'                → reason='investigation', hard
  · '重大资产重组' AND ('停牌' or '终止' not in title) → reason='major_restructuring', hard
  · '减持' AND ('股东' or '高管')                     → reason='insider_selling', soft
  · '业绩预告'... uses forecast endpoint instead, not anns_d
"""
from __future__ import annotations

import logging
import re
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.tushare.client import TuShareClient

log = logging.getLogger(__name__)

_INVESTIGATION_RE = re.compile(r"立案调查|立案侦查|被调查|被立案|涉嫌违规")
_RESTRUCTURE_RE = re.compile(r"重大资产重组")
_INSIDER_SELL_RE = re.compile(r"减持(计划|进展|结果|预披露)|股东.{0,8}减持")

_UPSERT = text("""
    INSERT INTO ta.blacklist_daily
        (trade_date, ts_code, reason, severity, ann_title,
         source_ann_date, ref_value)
    VALUES
        (:trade_date, :ts_code, :reason, :severity, :ann_title,
         :source_ann_date, :ref_value)
    ON CONFLICT (trade_date, ts_code, reason) DO UPDATE SET
        severity = EXCLUDED.severity,
        ann_title = EXCLUDED.ann_title,
        source_ann_date = EXCLUDED.source_ann_date,
        ref_value = EXCLUDED.ref_value
""")


def _classify_title(title: str) -> tuple[str, str] | None:
    """Returns (reason, severity) or None if not adverse."""
    if not title:
        return None
    if _INVESTIGATION_RE.search(title):
        return ("investigation", "hard")
    if _RESTRUCTURE_RE.search(title):
        return ("major_restructuring", "hard")
    if _INSIDER_SELL_RE.search(title):
        return ("insider_selling", "soft")
    return None


def fetch_blacklist(
    client: TuShareClient,
    engine: Engine,
    *,
    trade_date: date,
) -> int:
    """Populate ta.blacklist_daily for one trade_date. Returns rows written."""
    yyyymmdd = trade_date.strftime("%Y%m%d")
    rows: list[dict] = []

    # 1. anns_d — title keyword scan
    try:
        df = client.call("anns_d", ann_date=yyyymmdd)
        for _, r in df.iterrows():
            cls = _classify_title(r.get("title") or "")
            if not cls:
                continue
            reason, severity = cls
            rows.append({
                "trade_date": trade_date,
                "ts_code": r.get("ts_code"),
                "reason": reason,
                "severity": severity,
                "ann_title": (r.get("title") or "")[:500],
                "source_ann_date": trade_date,
                "ref_value": None,
            })
    except Exception as e:
        log.warning("anns_d fetch failed for %s: %s", trade_date, e)

    # 2. forecast — severe loss-warning
    try:
        df = client.call("forecast", ann_date=yyyymmdd)
        for _, r in df.iterrows():
            try:
                pct = float(r.get("p_change_min")) if r.get("p_change_min") is not None else None
            except (TypeError, ValueError):
                pct = None
            if pct is None or pct >= -50.0:
                continue
            rows.append({
                "trade_date": trade_date,
                "ts_code": r.get("ts_code"),
                "reason": "severe_forecast_miss",
                "severity": "soft",
                "ann_title": (r.get("type") or "业绩预告"),
                "source_ann_date": trade_date,
                "ref_value": round(pct, 2),
            })
    except Exception as e:
        log.warning("forecast fetch failed for %s: %s", trade_date, e)

    if not rows:
        return 0
    n = 0
    with engine.begin() as conn:
        for row in rows:
            if not row.get("ts_code"):
                continue
            conn.execute(_UPSERT, row)
            n += 1
    log.info("blacklist_daily: wrote %d rows for %s", n, trade_date)
    return n
