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
    """Fetch rt_k for entire A-share market (3 exchange wildcards).

    Fail-closed: if any of the 3 wildcards fails or returns implausibly few
    rows, return None so the caller propagates "no data" rather than computing
    sector aggregates from 2/3 of the universe (which would be silently wrong).

    Cached per on_date within a process — three families share one pull.
    """
    key = ("rt_k", on_date)
    if key in _RT_K_CACHE:
        cached = _RT_K_CACHE[key]
        return cached if cached is not None and not cached.empty else None
    min_rows = {"6*.SH": 1000, "0*.SZ": 1000, "3*.SZ": 1000}
    chunks = []
    for pattern in ("6*.SH", "0*.SZ", "3*.SZ"):
        try:
            df = client.call("rt_k", ts_code=pattern)
        except Exception:
            _RT_K_CACHE[key] = None
            return None
        if df is None or df.empty or len(df) < min_rows[pattern]:
            _RT_K_CACHE[key] = None
            return None
        chunks.append(df)
    df = pd.concat(chunks, ignore_index=True).drop_duplicates(subset=["ts_code"])
    df = df[df["pre_close"].notna() & df["close"].notna() & (df["pre_close"] > 0)]
    if df.empty or len(df) < 3000:
        _RT_K_CACHE[key] = None
        return None
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


def _load_mv_weights(client: TuShareClient, *, on_date: dt.date,
                       engine: Engine | None = None) -> dict[str, float]:
    """T-1 daily_basic total_mv (单位 万元) keyed by ts_code. Returns {} on failure.

    Trade-day-aware: prev_trading_day handles 调休 + holidays correctly.
    Falls back to a 14-calendar-day brute-force walk if trade_cal unavailable
    (covers Spring Festival 7-day holiday which would have failed the old
    range(1, 8) loop)."""
    candidates: list[dt.date] = []
    if engine is not None:
        try:
            from ifa.core.calendar import prev_trading_day
            prev = prev_trading_day(engine, on_date)
            candidates.append(prev)
            # also queue prev-prev as fallback in case T-1 daily_basic isn't out yet
            try:
                candidates.append(prev_trading_day(engine, prev))
            except Exception:
                pass
        except Exception:
            pass
    if not candidates:
        candidates = [on_date - dt.timedelta(days=b) for b in range(1, 14)]
    for td in candidates:
        try:
            df = client.call("daily_basic", trade_date=td.strftime("%Y%m%d"))
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
      amount_yuan / up_count / down_count / flat_count / up_ratio are constituent
      proxy fields from the same rt_k snapshot, not official SW index prints.

    Returns None-valued entries for codes whose member set has no rt_k coverage
    (caller should fall back to sw_daily EOD or surface an unavailable reason).
    """
    rt = _load_whole_a_rt_k(client, on_date=on_date)
    if rt is None or rt.empty:
        return {c: {"close": None, "pct_change": None,
                     "trade_date": None, "member_count": 0, "covered_count": 0,
                     "amount_yuan": None, "up_count": None, "down_count": None,
                     "flat_count": None, "up_ratio": None,
                     "source_method": "constituent_rt_k_proxy",
                     "source_confidence": "low",
                     "unavailable_reason": "实时成分股行情不可用"} for c in sw_codes}
    rt = rt.copy()
    for col in ("close", "pre_close", "amount"):
        if col in rt.columns:
            rt[col] = pd.to_numeric(rt[col], errors="coerce")
    rt_cols = ["close", "pre_close"]
    if "amount" in rt.columns:
        rt_cols.append("amount")
    rt_idx = rt.set_index("ts_code")[rt_cols]
    members = _load_member_map(engine, on_date=on_date, level=level)
    weights = _load_mv_weights(client, on_date=on_date, engine=engine)
    prior = _load_sw_prior_close(client, on_date=on_date, sw_codes=sw_codes)

    result: dict[str, dict] = {}
    for code in sw_codes:
        ms = members.get(code, [])
        if not ms:
            result[code] = {"close": None, "pct_change": None,
                             "trade_date": None, "member_count": 0, "covered_count": 0,
                             "amount_yuan": None, "up_count": None, "down_count": None,
                             "flat_count": None, "up_ratio": None,
                             "source_method": "constituent_rt_k_proxy",
                             "source_confidence": "low",
                             "unavailable_reason": "缺申万成分股快照"}
            continue
        sub = rt_idx.reindex(ms).dropna(subset=["close", "pre_close"])
        sub = sub[sub["pre_close"] > 0]
        if sub.empty:
            result[code] = {"close": None, "pct_change": None,
                             "trade_date": None, "member_count": len(ms), "covered_count": 0,
                             "amount_yuan": None, "up_count": None, "down_count": None,
                             "flat_count": None, "up_ratio": None,
                             "source_method": "constituent_rt_k_proxy",
                             "source_confidence": "low",
                             "unavailable_reason": "成分股实时行情缺失"}
            continue
        # Build weight series (default 1.0 if MV missing/zero)
        w = pd.Series([weights.get(tc, 1.0) or 1.0 for tc in sub.index], index=sub.index)
        num = (sub["close"] * w).sum()
        den = (sub["pre_close"] * w).sum()
        pct = (num / den - 1.0) * 100 if den > 0 else None
        prior_close = prior.get(code)
        syn_close = prior_close * (1 + pct / 100) if (prior_close and pct is not None) else None
        member_count = int(len(ms))
        covered_count = int(len(sub))
        coverage = covered_count / member_count if member_count > 0 else 0.0
        if coverage >= 0.8 and covered_count >= 5:
            confidence = "high"
        elif coverage >= 0.5 and covered_count >= 3:
            confidence = "medium"
        else:
            confidence = "low"
        pct_member = (sub["close"] - sub["pre_close"]) / sub["pre_close"] * 100
        active = pct_member.dropna()
        up_count = int((active > 0).sum()) if not active.empty else None
        down_count = int((active < 0).sum()) if not active.empty else None
        flat_count = int((active == 0).sum()) if not active.empty else None
        denom = (up_count or 0) + (down_count or 0) + (flat_count or 0)
        up_ratio = (up_count / denom) if denom > 0 and up_count is not None else None
        amount_yuan = float(sub["amount"].sum()) if "amount" in sub.columns and sub["amount"].notna().any() else None
        result[code] = {
            "close": syn_close,
            "pct_change": pct,
            "trade_date": on_date if pct is not None else None,
            "member_count": member_count,
            "covered_count": covered_count,
            "coverage_ratio": coverage,
            "amount_yuan": amount_yuan,
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "up_ratio": up_ratio,
            "source_method": "constituent_rt_k_proxy",
            "source_confidence": confidence,
            "unavailable_reason": None if pct is not None else "成分股昨收或现价缺失",
        }
    return result
