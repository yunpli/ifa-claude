"""Raw TuShare → smartmoney.raw_* upsert layer.

One function per raw table. Each fetcher is invoked by the daily_etl /
backfill driver with a trade_date (or date range), pulls data from TuShare,
normalises types, and upserts into the corresponding smartmoney.raw_* table.

Conventions:
  - All fetchers return the row count loaded (for stats / watermark update)
  - Date params are passed as YYYYMMDD strings (TuShare convention)
  - Idempotent: ON CONFLICT DO UPDATE (or DO NOTHING for append-only tables
    like top_inst, ths_hot, dc_hot, block_trade which use UUID PKs and
    are bulk-replaced for the date)
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


# ─── Helpers ────────────────────────────────────────────────────────────────

def _to_date(s: Any) -> dt.date | None:
    if s is None or pd.isna(s):
        return None
    s = str(s)
    if len(s) == 8 and s.isdigit():
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    try:
        return pd.to_datetime(s).date()
    except (ValueError, TypeError):
        return None


def _f(v: Any) -> float | None:
    if v is None or pd.isna(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    if v is None or pd.isna(v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _s(v: Any) -> str | None:
    if v is None or pd.isna(v):
        return None
    return str(v)


def _replace_for_date(engine: Engine, table: str, trade_date: dt.date) -> None:
    """Delete existing rows for this date so we can bulk-insert idempotently
    for tables with UUID PKs (top_inst, ths_hot, dc_hot, block_trade)."""
    with engine.begin() as conn:
        conn.execute(
            text(f"DELETE FROM {SCHEMA}.{table} WHERE trade_date = :d"),
            {"d": trade_date},
        )


def _bulk_upsert(
    engine: Engine,
    table: str,
    rows: list[dict],
    *,
    pk_cols: list[str],
    update_cols: list[str] | None = None,
) -> int:
    """ON CONFLICT (pk_cols) DO UPDATE SET ... — works for tables with
    natural PKs."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    col_list = ", ".join(cols)
    pk_list = ", ".join(pk_cols)
    if update_cols is None:
        update_cols = [c for c in cols if c not in pk_cols]
    if update_cols:
        update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        on_conflict = f"ON CONFLICT ({pk_list}) DO UPDATE SET {update_set}"
    else:
        on_conflict = f"ON CONFLICT ({pk_list}) DO NOTHING"
    sql = text(f"""
        INSERT INTO {SCHEMA}.{table} ({col_list})
        VALUES ({placeholders})
        {on_conflict}
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


# ─── 1. raw_daily ───────────────────────────────────────────────────────────

def fetch_raw_daily(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("daily", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "ts_code": _s(r.ts_code), "trade_date": trade_date,
            "open": _f(r.open), "high": _f(r.high), "low": _f(r.low), "close": _f(r.close),
            "pre_close": _f(r.pre_close), "change_": _f(r.change), "pct_chg": _f(r.pct_chg),
            "vol": _f(r.vol), "amount": _f(r.amount),
        })
    return _bulk_upsert(engine, "raw_daily", rows, pk_cols=["trade_date", "ts_code"])


# ─── 2. raw_daily_basic ─────────────────────────────────────────────────────

def fetch_raw_daily_basic(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("daily_basic", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "ts_code": _s(r.ts_code), "trade_date": trade_date,
            "close": _f(r.close), "turnover_rate": _f(r.turnover_rate),
            "turnover_rate_f": _f(r.turnover_rate_f), "volume_ratio": _f(r.volume_ratio),
            "pe": _f(r.pe), "pe_ttm": _f(r.pe_ttm),
            "pb": _f(r.pb), "ps": _f(r.ps), "ps_ttm": _f(r.ps_ttm),
            "dv_ratio": _f(r.dv_ratio), "dv_ttm": _f(r.dv_ttm),
            "total_share": _f(r.total_share), "float_share": _f(r.float_share),
            "free_share": _f(r.free_share),
            "total_mv": _f(r.total_mv), "circ_mv": _f(r.circ_mv),
        })
    return _bulk_upsert(engine, "raw_daily_basic", rows, pk_cols=["trade_date", "ts_code"])


# ─── 3. raw_moneyflow ───────────────────────────────────────────────────────

def fetch_raw_moneyflow(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("moneyflow", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    cols = [
        "buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
        "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
        "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
        "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
        "net_mf_vol", "net_mf_amount",
    ]
    rows = []
    for r in df.itertuples():
        row = {"ts_code": _s(r.ts_code), "trade_date": trade_date}
        for c in cols:
            row[c] = _f(getattr(r, c, None))
        rows.append(row)
    return _bulk_upsert(engine, "raw_moneyflow", rows, pk_cols=["trade_date", "ts_code"])


# ─── 4. raw_moneyflow_ind_dc ────────────────────────────────────────────────

def fetch_raw_moneyflow_ind_dc(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("moneyflow_ind_dc", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "ts_code": _s(r.ts_code), "trade_date": trade_date,
            "content_type": _s(r.content_type), "name": _s(r.name),
            "pct_change": _f(r.pct_change), "close": _f(r.close),
            "net_amount": _f(r.net_amount), "net_amount_rate": _f(r.net_amount_rate),
            "buy_elg_amount": _f(r.buy_elg_amount), "buy_elg_amount_rate": _f(r.buy_elg_amount_rate),
            "buy_lg_amount": _f(r.buy_lg_amount), "buy_lg_amount_rate": _f(r.buy_lg_amount_rate),
            "buy_md_amount": _f(r.buy_md_amount), "buy_md_amount_rate": _f(r.buy_md_amount_rate),
            "buy_sm_amount": _f(r.buy_sm_amount), "buy_sm_amount_rate": _f(r.buy_sm_amount_rate),
            "buy_sm_amount_stock": _s(r.buy_sm_amount_stock), "rank": _i(r.rank),
        })
    return _bulk_upsert(engine, "raw_moneyflow_ind_dc", rows, pk_cols=["trade_date", "ts_code"])


# ─── 5. raw_moneyflow_ind_ths ───────────────────────────────────────────────

def fetch_raw_moneyflow_ind_ths(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("moneyflow_ind_ths", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "ts_code": _s(r.ts_code), "trade_date": trade_date,
            "industry": _s(r.industry), "lead_stock": _s(r.lead_stock),
            "close": _f(r.close), "pct_change": _f(r.pct_change),
            "company_num": _i(r.company_num), "pct_change_stock": _f(r.pct_change_stock),
            "close_price": _f(r.close_price), "net_buy_amount": _f(r.net_buy_amount),
            "net_sell_amount": _f(r.net_sell_amount), "net_amount": _f(r.net_amount),
        })
    return _bulk_upsert(engine, "raw_moneyflow_ind_ths", rows, pk_cols=["trade_date", "ts_code"])


# ─── 6. raw_moneyflow_hsgt ──────────────────────────────────────────────────

def fetch_raw_moneyflow_hsgt(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("moneyflow_hsgt",
                     start_date=trade_date.strftime("%Y%m%d"),
                     end_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "trade_date": _to_date(r.trade_date),
            "ggt_ss": _f(r.ggt_ss), "ggt_sz": _f(r.ggt_sz),
            "hgt": _f(r.hgt), "sgt": _f(r.sgt),
            "north_money": _f(r.north_money), "south_money": _f(r.south_money),
        })
    return _bulk_upsert(engine, "raw_moneyflow_hsgt", rows, pk_cols=["trade_date"])


# ─── 7. raw_margin ──────────────────────────────────────────────────────────

def fetch_raw_margin(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("margin",
                     start_date=trade_date.strftime("%Y%m%d"),
                     end_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "trade_date": _to_date(r.trade_date), "exchange_id": _s(r.exchange_id),
            "rzye": _f(r.rzye), "rzmre": _f(r.rzmre), "rzche": _f(r.rzche),
            "rqye": _f(r.rqye), "rqmcl": _f(r.rqmcl),
            "rzrqye": _f(getattr(r, "rzrqye", None)),
            "rqyl": _f(getattr(r, "rqyl", None)),
        })
    return _bulk_upsert(engine, "raw_margin", rows, pk_cols=["trade_date", "exchange_id"])


# ─── 8. raw_limit_list_d ────────────────────────────────────────────────────

def fetch_raw_limit_list_d(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("limit_list_d", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "ts_code": _s(r.ts_code), "trade_date": trade_date,
            "industry": _s(getattr(r, "industry", None)),
            "name": _s(getattr(r, "name", None)),
            "close": _f(getattr(r, "close", None)),
            "pct_chg": _f(getattr(r, "pct_chg", None)),
            "amount": _f(getattr(r, "amount", None)),
            "limit_amount": _f(getattr(r, "limit_amount", None)),
            "fc_ratio": _f(getattr(r, "fc_ratio", None)),
            "fl_ratio": _f(getattr(r, "fl_ratio", None)),
            "fd_amount": _f(getattr(r, "fd_amount", None)),
            "first_time": _s(getattr(r, "first_time", None)),
            "last_time": _s(getattr(r, "last_time", None)),
            "open_times": _i(getattr(r, "open_times", None)),
            "up_stat": _s(getattr(r, "up_stat", None)),
            "limit_times": _i(getattr(r, "limit_times", None)),
            "limit_": _s(getattr(r, "limit", None)),
        })
    return _bulk_upsert(engine, "raw_limit_list_d", rows, pk_cols=["trade_date", "ts_code"])


# ─── 9. raw_kpl_concept ─────────────────────────────────────────────────────

def fetch_raw_kpl_concept(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("kpl_concept", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "ts_code": _s(r.ts_code), "trade_date": trade_date,
            "name": _s(r.name), "z_t_num": _i(r.z_t_num), "up_num": _i(r.up_num),
        })
    return _bulk_upsert(engine, "raw_kpl_concept", rows, pk_cols=["trade_date", "ts_code"])


# ─── 10. raw_kpl_concept_cons ──────────────────────────────────────────────

def fetch_raw_kpl_concept_cons(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("kpl_concept_cons", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "trade_date": trade_date,
            "con_code": _s(r.con_code), "ts_code": _s(r.ts_code),
            "name": _s(r.name), "con_name": _s(r.con_name),
            "description": _s(getattr(r, "desc", None)),
            "hot_num": _i(getattr(r, "hot_num", None)),
        })
    return _bulk_upsert(engine, "raw_kpl_concept_cons", rows,
                        pk_cols=["trade_date", "con_code", "ts_code"])


# ─── 11. raw_kpl_list ──────────────────────────────────────────────────────

def fetch_raw_kpl_list(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("kpl_list", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "ts_code": _s(r.ts_code), "trade_date": trade_date,
            "name": _s(r.name),
            "lu_time": _s(r.lu_time), "ld_time": _s(r.ld_time),
            "open_time": _s(r.open_time), "last_time": _s(r.last_time),
            "lu_desc": _s(r.lu_desc), "tag": _s(r.tag), "theme": _s(r.theme),
            "net_change": _f(r.net_change), "bid_amount": _f(r.bid_amount),
            "status": _s(r.status), "bid_change": _f(r.bid_change),
            "bid_turnover": _f(r.bid_turnover), "lu_bid_vol": _f(r.lu_bid_vol),
            "pct_chg": _f(r.pct_chg), "bid_pct_chg": _f(r.bid_pct_chg),
            "rt_pct_chg": _f(r.rt_pct_chg), "limit_order": _f(r.limit_order),
            "amount": _f(r.amount), "turnover_rate": _f(r.turnover_rate),
            "free_float": _f(r.free_float), "lu_limit_order": _f(r.lu_limit_order),
        })
    return _bulk_upsert(engine, "raw_kpl_list", rows, pk_cols=["trade_date", "ts_code"])


# ─── 12. raw_top_list ──────────────────────────────────────────────────────

def fetch_raw_top_list(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("top_list", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        reason = _s(r.reason) or ""
        rows.append({
            "trade_date": trade_date, "ts_code": _s(r.ts_code), "reason": reason,
            "name": _s(r.name), "close": _f(r.close), "pct_change": _f(r.pct_change),
            "turnover_rate": _f(r.turnover_rate), "amount": _f(r.amount),
            "l_sell": _f(r.l_sell), "l_buy": _f(r.l_buy), "l_amount": _f(r.l_amount),
            "net_amount": _f(r.net_amount), "net_rate": _f(r.net_rate),
            "amount_rate": _f(r.amount_rate), "float_values": _f(r.float_values),
        })
    return _bulk_upsert(engine, "raw_top_list", rows,
                        pk_cols=["trade_date", "ts_code", "reason"])


# ─── 13. raw_top_inst (UUID PK, replace-by-date) ─────────────────────────

def fetch_raw_top_inst(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("top_inst", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    _replace_for_date(engine, "raw_top_inst", trade_date)
    rows = []
    for r in df.itertuples():
        rows.append({
            "trade_date": trade_date, "ts_code": _s(r.ts_code),
            "exalter": _s(r.exalter),
            "buy": _f(r.buy), "buy_rate": _f(r.buy_rate),
            "sell": _f(r.sell), "sell_rate": _f(r.sell_rate),
            "net_buy": _f(r.net_buy),
            "side": _s(r.side), "reason": _s(r.reason),
        })
    sql = text(f"""
        INSERT INTO {SCHEMA}.raw_top_inst
            (trade_date, ts_code, exalter, buy, buy_rate, sell, sell_rate, net_buy, side, reason)
        VALUES
            (:trade_date, :ts_code, :exalter, :buy, :buy_rate, :sell, :sell_rate, :net_buy, :side, :reason)
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


# ─── 14. raw_ths_hot (UUID PK) ─────────────────────────────────────────────

def fetch_raw_ths_hot(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("ths_hot", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    _replace_for_date(engine, "raw_ths_hot", trade_date)
    rows = []
    for r in df.itertuples():
        rows.append({
            "trade_date": trade_date, "data_type": _s(r.data_type),
            "ts_code": _s(r.ts_code), "ts_name": _s(r.ts_name),
            "rank": _i(r.rank), "pct_change": _f(r.pct_change),
            "current_price": _f(r.current_price), "hot": _f(r.hot),
            "concept": _s(r.concept), "rank_time": _s(r.rank_time),
            "rank_reason": _s(getattr(r, "rank_reason", None)),
        })
    sql = text(f"""
        INSERT INTO {SCHEMA}.raw_ths_hot
            (trade_date, data_type, ts_code, ts_name, rank, pct_change, current_price, hot, concept, rank_time, rank_reason)
        VALUES
            (:trade_date, :data_type, :ts_code, :ts_name, :rank, :pct_change, :current_price, :hot, :concept, :rank_time, :rank_reason)
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


# ─── 15. raw_dc_hot (UUID PK) ───────────────────────────────────────────────

def fetch_raw_dc_hot(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("dc_hot", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    _replace_for_date(engine, "raw_dc_hot", trade_date)
    rows = []
    for r in df.itertuples():
        rows.append({
            "trade_date": trade_date, "data_type": _s(r.data_type),
            "ts_code": _s(r.ts_code), "ts_name": _s(r.ts_name),
            "rank": _i(r.rank), "pct_change": _f(r.pct_change),
            "current_price": _f(r.current_price), "hot": _f(r.hot),
            "concept": _s(r.concept), "rank_time": _s(r.rank_time),
        })
    sql = text(f"""
        INSERT INTO {SCHEMA}.raw_dc_hot
            (trade_date, data_type, ts_code, ts_name, rank, pct_change, current_price, hot, concept, rank_time)
        VALUES
            (:trade_date, :data_type, :ts_code, :ts_name, :rank, :pct_change, :current_price, :hot, :concept, :rank_time)
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


# ─── 16. raw_dc_index ──────────────────────────────────────────────────────

def fetch_raw_dc_index(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("dc_index", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = []
    for r in df.itertuples():
        rows.append({
            "ts_code": _s(r.ts_code), "trade_date": trade_date,
            "name": _s(r.name), "leading_name": _s(r.leading), "leading_code": _s(r.leading_code),
            "pct_change": _f(r.pct_change), "leading_pct": _f(r.leading_pct),
            "total_mv": _f(r.total_mv), "turnover_rate": _f(r.turnover_rate),
            "up_num": _i(r.up_num), "down_num": _i(r.down_num),
            "idx_type": _s(r.idx_type), "level": _s(r.level),
        })
    return _bulk_upsert(engine, "raw_dc_index", rows, pk_cols=["trade_date", "ts_code"])


# ─── 17. raw_dc_member ─────────────────────────────────────────────────────

def fetch_raw_dc_member(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("dc_member")
    if df is None or df.empty:
        return 0
    # dc_member returns its own trade_date column; tag with the requested date
    # only when not present (compat with API variants).
    rows = []
    for r in df.itertuples():
        td = _to_date(r.trade_date) or trade_date
        rows.append({
            "trade_date": td,
            "ts_code": _s(r.ts_code), "con_code": _s(r.con_code),
            "name": _s(r.name),
        })
    return _bulk_upsert(engine, "raw_dc_member", rows,
                        pk_cols=["trade_date", "ts_code", "con_code"])


# ─── 18. raw_sw_daily ──────────────────────────────────────────────────────

def fetch_raw_sw_daily(client: TuShareClient, engine: Engine, *,
                       trade_date: dt.date, sw_codes: list[str]) -> int:
    """sw_daily must be queried per ts_code (no trade_date-only mode that returns
    all SW indexes). Loop over the curated SW universe."""
    rows = []
    end = trade_date.strftime("%Y%m%d")
    for code in sw_codes:
        try:
            df = client.call("sw_daily", ts_code=code, start_date=end, end_date=end)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for r in df.itertuples():
            rows.append({
                "ts_code": _s(r.ts_code), "trade_date": trade_date,
                "name": _s(r.name),
                "open": _f(r.open), "low": _f(r.low), "high": _f(r.high), "close": _f(r.close),
                "change_": _f(r.change), "pct_change": _f(r.pct_change),
                "vol": _f(r.vol), "amount": _f(r.amount),
                "pe": _f(getattr(r, "pe", None)), "pb": _f(getattr(r, "pb", None)),
                "float_mv": _f(getattr(r, "float_mv", None)),
                "total_mv": _f(getattr(r, "total_mv", None)),
            })
    return _bulk_upsert(engine, "raw_sw_daily", rows, pk_cols=["trade_date", "ts_code"])


# ─── 19. raw_index_daily ───────────────────────────────────────────────────

def fetch_raw_index_daily(client: TuShareClient, engine: Engine, *,
                          trade_date: dt.date, index_codes: list[str]) -> int:
    rows = []
    end = trade_date.strftime("%Y%m%d")
    for code in index_codes:
        try:
            df = client.call("index_daily", ts_code=code, start_date=end, end_date=end)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for r in df.itertuples():
            rows.append({
                "ts_code": _s(r.ts_code), "trade_date": trade_date,
                "close": _f(r.close), "open": _f(r.open),
                "high": _f(r.high), "low": _f(r.low),
                "pre_close": _f(r.pre_close),
                "change_": _f(r.change), "pct_chg": _f(r.pct_chg),
                "vol": _f(r.vol), "amount": _f(r.amount),
            })
    return _bulk_upsert(engine, "raw_index_daily", rows, pk_cols=["trade_date", "ts_code"])


# ─── 20. raw_block_trade (UUID PK) ─────────────────────────────────────────

def fetch_raw_block_trade(client: TuShareClient, engine: Engine, *, trade_date: dt.date) -> int:
    df = client.call("block_trade", trade_date=trade_date.strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    _replace_for_date(engine, "raw_block_trade", trade_date)
    rows = []
    for r in df.itertuples():
        rows.append({
            "ts_code": _s(r.ts_code), "trade_date": trade_date,
            "price": _f(r.price), "vol": _f(r.vol), "amount": _f(r.amount),
            "buyer": _s(r.buyer), "seller": _s(r.seller),
        })
    sql = text(f"""
        INSERT INTO {SCHEMA}.raw_block_trade
            (ts_code, trade_date, price, vol, amount, buyer, seller)
        VALUES
            (:ts_code, :trade_date, :price, :vol, :amount, :buyer, :seller)
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


# ─── 21. raw_cyq_chips (per-stock) ─────────────────────────────────────────

def fetch_raw_cyq_chips(client: TuShareClient, engine: Engine, *,
                        trade_date: dt.date, ts_codes: list[str]) -> int:
    """Fetch chip distribution for a curated list of stocks (typically the
    Top-N active candidates from kpl_list / limit-up). Per-stock single-day."""
    rows = []
    d = trade_date.strftime("%Y%m%d")
    for code in ts_codes:
        try:
            df = client.call("cyq_chips", ts_code=code, start_date=d, end_date=d)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for r in df.itertuples():
            rows.append({
                "ts_code": _s(r.ts_code), "trade_date": trade_date,
                "price": _f(r.price), "percent": _f(r.percent),
            })
    return _bulk_upsert(engine, "raw_cyq_chips", rows,
                        pk_cols=["trade_date", "ts_code", "price"])


# ─── Registry ──────────────────────────────────────────────────────────────

# Trade-date-only fetchers (single call covers full universe for that date)
TRADE_DATE_FETCHERS: list[tuple[str, Any]] = [
    ("raw_daily",              fetch_raw_daily),
    ("raw_daily_basic",        fetch_raw_daily_basic),
    ("raw_moneyflow",          fetch_raw_moneyflow),
    ("raw_moneyflow_ind_dc",   fetch_raw_moneyflow_ind_dc),
    ("raw_moneyflow_ind_ths",  fetch_raw_moneyflow_ind_ths),
    ("raw_moneyflow_hsgt",     fetch_raw_moneyflow_hsgt),
    ("raw_margin",             fetch_raw_margin),
    ("raw_limit_list_d",       fetch_raw_limit_list_d),
    ("raw_kpl_concept",        fetch_raw_kpl_concept),
    ("raw_kpl_concept_cons",   fetch_raw_kpl_concept_cons),
    ("raw_kpl_list",           fetch_raw_kpl_list),
    ("raw_top_list",           fetch_raw_top_list),
    ("raw_top_inst",           fetch_raw_top_inst),
    ("raw_ths_hot",            fetch_raw_ths_hot),
    ("raw_dc_hot",             fetch_raw_dc_hot),
    ("raw_dc_index",           fetch_raw_dc_index),
    ("raw_dc_member",          fetch_raw_dc_member),
    ("raw_block_trade",        fetch_raw_block_trade),
]

# Per-code fetchers (need explicit code list)
PER_CODE_FETCHERS = {
    "raw_sw_daily":     fetch_raw_sw_daily,
    "raw_index_daily":  fetch_raw_index_daily,
    "raw_cyq_chips":    fetch_raw_cyq_chips,
}
