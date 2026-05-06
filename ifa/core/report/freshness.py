"""Pre-flight ETL freshness check for report generation.

Before a family fetches data, ask the DB: "do the raw tables this report
depends on have data through the most recent trading day?"

Trading-day-aware: we use `smartmoney.trade_cal` (via ifa.core.calendar) to
translate "落后" into trading days, not calendar days. This naturally handles
weekends, May Day, Chinese New Year, 调休 — without per-call --skip-on-holiday
gymnastics.

Concretely, for a report scheduled on `expected_date`:
  - if expected_date is itself a trading day → reference = expected_date
  - else → reference = prev_trading_day(expected_date)
  - lag = number of trading days between latest(table.trade_date) and reference
  - lag = 0  → fresh, no warning
  - lag ≥ 1 → ETL is behind by N trading days; warn

This is intentionally informational, not a hard fail:
- Historical replay (manual run for a past date) reads tables that legitimately
  have no rows past that past date — failing would block the use case.
- The data layer's per-snap staleness gate already fails closed at field level.
- Banner staleness warning (Bug #4) already surfaces to the reader.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.calendar import (
    is_trading_day,
    prev_trading_day,
    trading_days_between,
)


# Per-family AND per-slot list of (table, friendly_name, allowed_lag_trading_days).
# Slot-aware so noon doesn't warn about tables it doesn't actually depend on
# (e.g., raw_moneyflow is only consumed by evening's fund_flow_top section).
# Tables that fail to populate for noon's reference date won't be flagged
# unless noon legitimately reads them.
_FAMILY_TABLES: dict[str, dict[str, list[tuple[str, str, int]]]] = {
    "market": {
        # Morning + noon both read historical context for sparkline + SW MV weights.
        # Noon does NOT use raw_moneyflow (no fund_flow_top section), no top_list,
        # no top_inst — drop them.
        "morning": [
            ("smartmoney.raw_daily",       "全A日行情",       0),
            ("smartmoney.raw_index_daily", "指数日行情",       0),
            ("smartmoney.raw_sw_daily",    "申万板块日行情",   0),
        ],
        "noon": [
            ("smartmoney.raw_daily",       "全A日行情",       0),
            ("smartmoney.raw_index_daily", "指数日行情",       0),
            ("smartmoney.raw_sw_daily",    "申万板块日行情",   0),
        ],
        # Evening adds fund_flow_top (moneyflow), dragon_tiger (top_list/top_inst).
        "evening": [
            ("smartmoney.raw_daily",       "全A日行情",       0),
            ("smartmoney.raw_moneyflow",   "全A资金流",       0),
            ("smartmoney.raw_index_daily", "指数日行情",       0),
            ("smartmoney.raw_sw_daily",    "申万板块日行情",   0),
        ],
    },
    "macro": {
        "morning": [
            ("smartmoney.raw_index_daily", "指数日行情",   0),
        ],
        "evening": [
            ("smartmoney.raw_index_daily", "指数日行情",   0),
            ("smartmoney.raw_daily",       "全A日行情",   0),
        ],
    },
    "asset": {
        "morning": [
            ("smartmoney.raw_sw_daily",    "申万板块日行情", 0),
        ],
        "evening": [
            ("smartmoney.raw_sw_daily",    "申万板块日行情", 0),
        ],
    },
    "tech": {
        "morning": [
            ("smartmoney.raw_sw_daily",    "申万板块日行情", 0),
            ("smartmoney.raw_daily",       "全A日行情",     0),
        ],
        "evening": [
            ("smartmoney.raw_sw_daily",    "申万板块日行情", 0),
            ("smartmoney.raw_daily",       "全A日行情",     0),
            ("smartmoney.raw_moneyflow",   "全A资金流",     0),
        ],
    },
}


def _max_trade_date(engine: Engine, table: str) -> dt.date | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(text(f"SELECT MAX(trade_date) FROM {table}")).first()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _resolve_reference_trading_day(engine: Engine, expected_date: dt.date) -> dt.date:
    """If expected_date is a trading day, use it; otherwise take the most
    recent trading day on or before it."""
    try:
        if is_trading_day(engine, expected_date):
            return expected_date
    except Exception:
        # trade_cal lookup failed — fall through to prev_trading_day which has
        # its own fallback path; if that also fails we just return expected_date
        # and lag will be in calendar days at worst.
        pass
    try:
        return prev_trading_day(engine, expected_date)
    except Exception:
        return expected_date


def _trading_day_lag(engine: Engine, *, latest: dt.date, reference: dt.date) -> int:
    """Number of trading days strictly after `latest` up to and including
    `reference`. Returns 0 when latest >= reference."""
    if latest >= reference:
        return 0
    try:
        days = trading_days_between(engine, latest + dt.timedelta(days=1), reference)
        return len(days)
    except Exception:
        # Fall back to calendar-day diff if trade_cal not available
        return (reference - latest).days


def preflight_freshness_check(
    engine: Engine,
    *,
    family: str,
    expected_date: dt.date,
    slot: str = "evening",
) -> list[str]:
    """Return human-readable warning strings (empty when all fresh).

    Slot-aware: each report slot only validates the tables it actually reads.
    Noon doesn't load raw_moneyflow / top_list / top_inst etc. — checking
    those would warn about ETL gaps that noon doesn't care about.

    Strings have the shape:
      "申万板块日行情 (smartmoney.raw_sw_daily) latest=2026-04-30,
       期望最近交易日 2026-05-06，落后 1 个交易日"

    A trading-day lag of 0 means "ETL is up to date with the most recent
    trading day, even if today happens to be a weekend/holiday."
    """
    family_spec = _FAMILY_TABLES.get(family, {})
    spec = family_spec.get(slot, [])
    if not spec:
        return []
    reference = _resolve_reference_trading_day(engine, expected_date)
    issues: list[str] = []
    for table, friendly, allowed_lag in spec:
        latest = _max_trade_date(engine, table)
        if latest is None:
            issues.append(f"{friendly} ({table}) 无数据可读 — 检查 ETL 是否曾运行")
            continue
        lag = _trading_day_lag(engine, latest=latest, reference=reference)
        if lag > allowed_lag:
            issues.append(
                f"{friendly} ({table}) latest={latest}, 期望最近交易日 {reference}，"
                f"落后 {lag} 个交易日"
            )
    return issues
