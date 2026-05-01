"""SW (申万) board member fetcher + monthly snapshot materialiser.

Why this module exists
----------------------
SmartMoney needs time-correct point-in-time sector membership to:
  · compute board-level money flow (aggregate per-stock moneyflow → sector)
  · identify sector leaders (filter members on a given trade_date)
  · build training labels for ML models (no look-ahead bias)

DC (东财概念) is unusable: `raw_dc_member` only has ~18 days in production.
THS (同花顺) is unusable: `ths_member` returns current snapshot only, no
                          in_date / out_date.

SW (申万) `index_member_all` provides full L1/L2/L3 classification with
in_date / out_date for every (sector, stock) pair, going back to 1993.

Two-stage architecture
----------------------
Stage 1 — raw_sw_member (source of truth):
  Pull all 31 L1 industries via TuShare `index_member_all`. Each call returns
  rows like:
     {l1_code, l1_name, l2_code, l2_name, l3_code, l3_name,
      ts_code, name, in_date, out_date, is_new}

  A stock may join → leave → rejoin a sector, so PK is (l1_code, ts_code, in_date).

Stage 2 — sw_member_monthly (pre-materialised snapshots):
  For each month-start from `start_month` to `end_month`, materialise the
  members of every L2 active at that point. One row per
  (snapshot_month, l2_code, ts_code) where:
     in_date <= snapshot_month AND (out_date IS NULL OR out_date > snapshot_month)

  Aggregation queries during compute use this table for speed:
     WHERE snapshot_month = date_trunc('month', :trade_date)::date

Update cadence
--------------
  · raw_sw_member: refresh quarterly (申万分类调整频率约半年/次)
  · sw_member_monthly: regenerate after every raw_sw_member refresh
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.tushare import TuShareClient

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_date(s: Any) -> dt.date | None:
    """Parse 'YYYYMMDD' string or pandas Timestamp to dt.date."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    if isinstance(s, dt.date):
        return s
    if isinstance(s, pd.Timestamp):
        return s.date()
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return None
    if len(s) == 8 and s.isdigit():
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    try:
        return pd.to_datetime(s).date()
    except Exception:
        return None


def _list_sw_l1_codes(client: TuShareClient) -> list[str]:
    """Return all 31 申万 L1 industry codes via index_classify."""
    df = client.call("index_classify", level="L1", src="SW2021")
    if df is None or df.empty:
        raise RuntimeError("index_classify(L1, SW2021) returned empty")
    return df["index_code"].dropna().unique().tolist()


# ── Stage 1: raw_sw_member ───────────────────────────────────────────────────

def fetch_raw_sw_member(client: TuShareClient, engine: Engine) -> int:
    """Pull SW member full history for all 31 L1 industries.

    One TuShare call per L1 (via `index_member_all`). Each call returns the
    L1's complete L1/L2/L3/stock × time-range rows.

    Returns total rows upserted into raw_sw_member.
    """
    l1_codes = _list_sw_l1_codes(client)
    log.info("[sw_member] fetching %d L1 industries", len(l1_codes))

    all_rows: list[dict] = []
    for i, l1 in enumerate(l1_codes, 1):
        try:
            df = client.call("index_member_all", l1_code=l1)
        except Exception as e:
            log.warning("[sw_member] %s FAIL: %s", l1, e)
            continue
        if df is None or df.empty:
            log.warning("[sw_member] %s returned empty", l1)
            continue
        for r in df.itertuples(index=False):
            in_d = _to_date(r.in_date)
            if in_d is None:
                # in_date is required (part of PK); skip if missing
                continue
            all_rows.append({
                "l1_code": str(r.l1_code) if r.l1_code else None,
                "l1_name": str(r.l1_name) if r.l1_name else None,
                "l2_code": str(r.l2_code) if r.l2_code else None,
                "l2_name": str(r.l2_name) if r.l2_name else None,
                "l3_code": str(r.l3_code) if r.l3_code else None,
                "l3_name": str(r.l3_name) if r.l3_name else None,
                "ts_code": str(r.ts_code) if r.ts_code else None,
                "name": str(r.name) if r.name else None,
                "in_date": in_d,
                "out_date": _to_date(r.out_date),
                "is_new": str(r.is_new) if r.is_new else None,
            })
        if i % 5 == 0:
            log.info("[sw_member] progress %d/%d L1s, %d rows so far",
                     i, len(l1_codes), len(all_rows))

    if not all_rows:
        log.warning("[sw_member] no rows collected")
        return 0

    return _bulk_upsert_sw_member(engine, all_rows)


def _bulk_upsert_sw_member(engine: Engine, rows: list[dict]) -> int:
    """Idempotent upsert into raw_sw_member.

    PK: (l1_code, ts_code, in_date). Update non-PK fields on conflict.
    Batched to avoid hitting DB statement-size limits with 30K+ rows.
    """
    if not rows:
        return 0

    sql = text(f"""
        INSERT INTO {SCHEMA}.raw_sw_member
            (l1_code, l1_name, l2_code, l2_name, l3_code, l3_name,
             ts_code, name, in_date, out_date, is_new)
        VALUES
            (:l1_code, :l1_name, :l2_code, :l2_name, :l3_code, :l3_name,
             :ts_code, :name, :in_date, :out_date, :is_new)
        ON CONFLICT (l1_code, ts_code, in_date) DO UPDATE SET
            l1_name  = EXCLUDED.l1_name,
            l2_code  = EXCLUDED.l2_code,
            l2_name  = EXCLUDED.l2_name,
            l3_code  = EXCLUDED.l3_code,
            l3_name  = EXCLUDED.l3_name,
            name     = EXCLUDED.name,
            out_date = EXCLUDED.out_date,
            is_new   = EXCLUDED.is_new,
            fetched_at = NOW()
    """)

    BATCH = 1000
    total = 0
    with engine.begin() as conn:
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            conn.execute(sql, chunk)
            total += len(chunk)
    log.info("[sw_member] upserted %d rows", total)
    return total


# ── Stage 2: sw_member_monthly ───────────────────────────────────────────────

def materialise_monthly_snapshots(
    engine: Engine,
    *,
    start_month: dt.date,
    end_month: dt.date,
) -> int:
    """Materialise sw_member_monthly for each month-start between start_month
    and end_month (inclusive). Both must be the 1st of a month.

    Logic per snapshot_month:
        SELECT l1_code, l1_name, l2_code, l2_name, ts_code, name
        FROM raw_sw_member
        WHERE l2_code IS NOT NULL
          AND in_date <= :snapshot_month
          AND (out_date IS NULL OR out_date > :snapshot_month)

    Idempotent: deletes the snapshot_month first, then inserts fresh.
    """
    if start_month.day != 1 or end_month.day != 1:
        raise ValueError("start_month and end_month must be first-of-month")
    if start_month > end_month:
        raise ValueError("start_month > end_month")

    months: list[dt.date] = []
    cur = start_month
    while cur <= end_month:
        months.append(cur)
        # advance one month
        if cur.month == 12:
            cur = dt.date(cur.year + 1, 1, 1)
        else:
            cur = dt.date(cur.year, cur.month + 1, 1)

    log.info("[sw_monthly] materialising %d monthly snapshots %s → %s",
             len(months), start_month, end_month)

    total = 0
    for snapshot_month in months:
        n = _materialise_one_month(engine, snapshot_month)
        total += n
        log.info("[sw_monthly] %s → %d rows", snapshot_month, n)
    return total


def _materialise_one_month(engine: Engine, snapshot_month: dt.date) -> int:
    """Build / refresh one monthly snapshot."""
    delete_sql = text(f"""
        DELETE FROM {SCHEMA}.sw_member_monthly
        WHERE snapshot_month = :sm
    """)
    insert_sql = text(f"""
        INSERT INTO {SCHEMA}.sw_member_monthly
            (snapshot_month, l1_code, l1_name, l2_code, l2_name, ts_code, name)
        SELECT
            :sm AS snapshot_month,
            l1_code, l1_name, l2_code, l2_name, ts_code, name
        FROM (
            -- A stock may have multiple rows per (l1, ts) due to leave/rejoin.
            -- Pick the row whose [in_date, out_date] window covers :sm.
            SELECT DISTINCT ON (l1_code, l2_code, ts_code)
                l1_code, l1_name, l2_code, l2_name, ts_code, name, in_date
            FROM {SCHEMA}.raw_sw_member
            WHERE l2_code IS NOT NULL
              AND in_date <= :sm
              AND (out_date IS NULL OR out_date > :sm)
            ORDER BY l1_code, l2_code, ts_code, in_date DESC
        ) latest
        ON CONFLICT (snapshot_month, l2_code, ts_code) DO UPDATE SET
            l1_code = EXCLUDED.l1_code,
            l1_name = EXCLUDED.l1_name,
            l2_name = EXCLUDED.l2_name,
            name    = EXCLUDED.name
    """)
    with engine.begin() as conn:
        conn.execute(delete_sql, {"sm": snapshot_month})
        result = conn.execute(insert_sql, {"sm": snapshot_month})
        return result.rowcount or 0


# ── Public driver: A1 + A2 in one call ───────────────────────────────────────

def run_sw_member_full_refresh(
    engine: Engine,
    client: TuShareClient | None = None,
    *,
    start_month: dt.date | None = None,
    end_month: dt.date | None = None,
) -> dict[str, int]:
    """One-shot driver: fetch raw_sw_member then materialise monthly snapshots.

    Defaults:
      · start_month: 2021-01-01 (matches pipeline-2 backfill range)
      · end_month:   first day of next month (forward-looking buffer)

    Returns dict with row counts.
    """
    client = client or TuShareClient()
    if start_month is None:
        start_month = dt.date(2021, 1, 1)
    if end_month is None:
        today = dt.date.today()
        if today.month == 12:
            end_month = dt.date(today.year + 1, 1, 1)
        else:
            end_month = dt.date(today.year, today.month + 1, 1)

    log.info("[sw_member] starting full refresh; monthly window %s → %s",
             start_month, end_month)

    raw_n = fetch_raw_sw_member(client, engine)
    monthly_n = materialise_monthly_snapshots(
        engine, start_month=start_month, end_month=end_month,
    )
    return {"raw_sw_member_rows": raw_n, "sw_member_monthly_rows": monthly_n}
