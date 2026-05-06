"""Pre-flight ETL freshness check for report generation.

Before a family fetches data, we ask the DB: "do the raw tables this report
depends on actually have data for the expected trade date?" If not, the
report will run anyway (the data layer's staleness gates will leave fields
None and the banner will warn) — but operators get a clear log line listing
exactly which table is behind and how stale it is.

This is intentionally informational, not a hard fail:
- Holidays cause legitimate "missing today" rows; failing would block manual
  historical replay.
- The data layer already fails closed at the snap-field level.
- Banner staleness warning already surfaces to the reader.

Use from a family runner:

    from ifa.core.report.freshness import preflight_freshness_check
    issues = preflight_freshness_check(engine, family="market", expected_date=on_date)
    for line in issues:
        on_log(f"[freshness] {line}")
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine


# Per-family list of (table, friendly_name, allowed_lag_days).
# allowed_lag_days = 0 means "must have today's row to be fresh".
# allowed_lag_days = N means "any row within last N days is acceptable" — use
# for tables that publish monthly / weekly rather than daily.
_FAMILY_TABLES: dict[str, list[tuple[str, str, int]]] = {
    "market": [
        ("smartmoney.raw_daily",      "全A日行情",     0),
        ("smartmoney.raw_moneyflow",  "全A资金流",     0),
        ("smartmoney.raw_index_daily","指数日行情",     0),
        ("smartmoney.raw_sw_daily",   "申万板块日行情", 0),
    ],
    "macro": [
        ("smartmoney.raw_index_daily","指数日行情",   0),
        ("smartmoney.raw_daily",      "全A日行情",   0),
    ],
    "asset": [
        ("smartmoney.raw_sw_daily",   "申万板块日行情", 0),
    ],
    "tech": [
        ("smartmoney.raw_sw_daily",   "申万板块日行情", 0),
        ("smartmoney.raw_daily",      "全A日行情",     0),
        ("smartmoney.raw_moneyflow",  "全A资金流",     0),
    ],
}


def _max_trade_date(engine: Engine, table: str) -> dt.date | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(text(f"SELECT MAX(trade_date) FROM {table}")).first()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def preflight_freshness_check(
    engine: Engine,
    *,
    family: str,
    expected_date: dt.date,
) -> list[str]:
    """Return a list of human-readable warning strings (empty when all fresh).

    Strings have the shape:
      "申万板块日行情 (smartmoney.raw_sw_daily) latest=2026-04-30, 期望 2026-05-06，落后 4 天"

    Caller decides what to do — log and continue, or escalate to fail-fast.
    """
    spec = _FAMILY_TABLES.get(family, [])
    if not spec:
        return []
    issues: list[str] = []
    for table, friendly, allowed_lag in spec:
        latest = _max_trade_date(engine, table)
        if latest is None:
            issues.append(f"{friendly} ({table}) 无数据可读 — 检查 ETL 是否曾运行")
            continue
        lag_days = (expected_date - latest).days
        if lag_days > allowed_lag:
            issues.append(
                f"{friendly} ({table}) latest={latest}, 期望 {expected_date}，"
                f"落后 {lag_days} 天"
            )
    return issues
