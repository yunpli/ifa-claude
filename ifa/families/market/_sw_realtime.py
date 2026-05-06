"""Realtime SW (申万) sector aggregation from member-stock snapshots.

TuShare's `sw_daily` is EOD only. `rt_min_daily` and `stk_mins` reject SW codes
("只适用上交所，深交所和北交所代码"), so we cannot pull a SW sector index
intraday directly. Instead we synthesize:

  agg_pct[sw_code] = SUM(member.close * member.total_mv_w) / SUM(member.pre_close * member.total_mv_w) - 1

with weights from T-1 `daily_basic.total_mv` (free-float-aware MV in 万元).
member.close and member.pre_close come from `rt_k` snapshots (whole-A scan).

Synthetic close for display: `prior_eod_sw_close * (1 + agg_pct)`.

Used by macro/asset/tech families when slot ∈ {noon, evening} on a live trading
day. Historical replay still uses `sw_daily` EOD (intraday SW reconstruction
would require ~thousands of stk_mins calls — not feasible).
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.tushare import TuShareClient


# Module-level memo so a single report run only pulls rt_k once across families.
_RT_K_CACHE: dict[tuple, pd.DataFrame] = {}


def _load_whole_a_rt_k(client: TuShareClient, *, on_date: dt.date) -> pd.DataFrame | None:
    """Fetch rt_k for entire A-share market (3 exchange wildcards). Cached per
    on_date within a process so repeated calls during one report are free."""
    key = ("rt_k", on_date)
    if key in _RT_K_CACHE:
        return _RT_K_CACHE[key]
    chunks = []
    for pattern in ("6*.SH", "0*.SZ", "3*.SZ"):
        try:
            df = client.call("rt_k", ts_code=pattern)
            if df is not None and not df.empty:
                chunks.append(df)
        except Exception:
            continue
    if not chunks:
        _RT_K_CACHE[key] = pd.DataFrame()
        return None
    df = pd.concat(chunks, ignore_index=True).drop_duplicates(subset=["ts_code"])
    df = df[df["pre_close"].notna() & df["close"].notna() & (df["pre_close"] > 0)]
    _RT_K_CACHE[key] = df
    return df


def _load_member_map(engine: Engine, *, on_date: dt.date,
                      level: str = "l1") -> dict[str, list[str]]:
    """Return {sw_code: [ts_code, ...]} for the snapshot_month covering on_date."""
    snapshot_month = on_date.replace(day=1)
    code_col = "l1_code" if level == "l1" else "l2_code"
    sql = text(f"""
        SELECT {code_col} AS code, ts_code
          FROM smartmoney.sw_member_monthly
         WHERE snapshot_month = :sm
    """)
    members: dict[str, list[str]] = {}
    with engine.connect() as conn:
        rows = conn.execute(sql, {"sm": snapshot_month}).all()
    for r in rows:
        members.setdefault(r.code, []).append(r.ts_code)
    return members


def _load_mv_weights(client: TuShareClient, *, on_date: dt.date) -> dict[str, float]:
    """T-1 daily_basic total_mv (单位 万元) keyed by ts_code. Returns {} on failure."""
    for back in range(1, 8):
        td = (on_date - dt.timedelta(days=back)).strftime("%Y%m%d")
        try:
            df = client.call("daily_basic", trade_date=td)
            if df is not None and not df.empty and "total_mv" in df.columns:
                return {r.ts_code: float(r.total_mv) for r in df.itertuples()
                        if r.total_mv is not None and not pd.isna(r.total_mv)}
        except Exception:
            continue
    return {}


def _load_sw_prior_close(client: TuShareClient, *, on_date: dt.date,
                          sw_codes: Iterable[str]) -> dict[str, float]:
    """T-1 sw_daily close per SW code, for synthetic close display."""
    out: dict[str, float] = {}
    end = (on_date - dt.timedelta(days=1)).strftime("%Y%m%d")
    start = (on_date - dt.timedelta(days=10)).strftime("%Y%m%d")
    for code in sw_codes:
        try:
            df = client.call("sw_daily", ts_code=code, start_date=start, end_date=end)
            if df is not None and not df.empty:
                df = df.sort_values("trade_date")
                out[code] = float(df.iloc[-1]["close"])
        except Exception:
            continue
    return out


def compute_sw_realtime_snapshot(
    client: TuShareClient, engine: Engine, *,
    on_date: dt.date, sw_codes: list[str], level: str = "l1",
) -> dict[str, dict]:
    """Aggregate realtime SW sector pct/close from member rt_k snapshots.

    Returns {sw_code: {"close": float|None, "pct_change": float|None,
                         "trade_date": date|None, "member_count": int}}.

    Methodology:
      pct = SUM(close * w) / SUM(pre_close * w) - 1
      where w = T-1 daily_basic.total_mv (zero/NaN MV stocks fall back to equal weight).
      synthetic_close = prior_eod_sw_close * (1 + pct).

    Returns empty dict for codes whose member set has no rt_k coverage
    (caller should fall back to sw_daily EOD).
    """
    rt = _load_whole_a_rt_k(client, on_date=on_date)
    if rt is None or rt.empty:
        return {c: {"close": None, "pct_change": None,
                     "trade_date": None, "member_count": 0} for c in sw_codes}
    rt_idx = rt.set_index("ts_code")[["close", "pre_close"]]
    members = _load_member_map(engine, on_date=on_date, level=level)
    weights = _load_mv_weights(client, on_date=on_date)
    prior = _load_sw_prior_close(client, on_date=on_date, sw_codes=sw_codes)

    result: dict[str, dict] = {}
    for code in sw_codes:
        ms = members.get(code, [])
        if not ms:
            result[code] = {"close": None, "pct_change": None,
                             "trade_date": None, "member_count": 0}
            continue
        sub = rt_idx.reindex(ms).dropna(subset=["close", "pre_close"])
        if sub.empty:
            result[code] = {"close": None, "pct_change": None,
                             "trade_date": None, "member_count": 0}
            continue
        # Build weight series (default 1.0 if MV missing/zero)
        w = pd.Series([weights.get(tc, 1.0) or 1.0 for tc in sub.index], index=sub.index)
        num = (sub["close"] * w).sum()
        den = (sub["pre_close"] * w).sum()
        pct = (num / den - 1.0) * 100 if den > 0 else None
        prior_close = prior.get(code)
        syn_close = prior_close * (1 + pct / 100) if (prior_close and pct is not None) else None
        result[code] = {
            "close": syn_close,
            "pct_change": pct,
            "trade_date": on_date if pct is not None else None,
            "member_count": int(len(sub)),
        }
    return result
