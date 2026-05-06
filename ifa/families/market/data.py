"""Main A-share report data layer.

Aggregates everything the morning/noon/evening reports need:
  - Six index family snapshot + 10-day series
  - Whole-A breadth (up/down/flat counts), turnover totals
  - Limit-up / limit-down structure (counts, 连板高度, 炸板率)
  - Top fund-flow stocks (主力资金 ranked)
  - Dragon-tiger list with reason classification
  - North/South capital, margin balance
  - SW industry rotation (via sw_daily, since index_daily returns 0 rows for SW)
  - Main-line candidates: top SW L2 sectors by today's net inflow + price momentum
    (V2.1 migration — replaced THS thematic boards with SW-only dynamic source)
  - News (broad market filter, BJT-tagged)
  - Three-aux summary read from DB (latest macro/asset/tech tone+headline+key)
  - Default focus enrichment (10 important + 20 regular, ALL — not tech-filtered)
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.report.timezones import BJT
from ifa.core.tushare import TuShareClient

from .universe import MAIN_LINE_TOP_N, MARKET_INDICES, SW_LEVEL1


# ─── Index family ─────────────────────────────────────────────────────────

@dataclass
class IndexSnap:
    ts_code: str
    name: str
    role: str
    close: float | None
    pct_change: float | None
    amount: float | None         # 元
    trade_date: dt.date | None
    history_close: list[float | None] = field(default_factory=list)
    history_dates: list[str] = field(default_factory=list)


def _today_bjt() -> dt.date:
    return dt.datetime.now(BJT).date()


def fetch_index_family(client: TuShareClient, *, on_date: dt.date,
                        history_days: int = 10,
                        slot: str = "morning") -> list[IndexSnap]:
    """Fetch index snapshots + history sparkline.

    Data source resolution (driven by on_date vs today and slot):
      Production today (on_date == BJT today):
        - morning  → EOD daily up to T-1; current = T-1 EOD
        - noon     → EOD daily up to T-1; current = ts.realtime_quote (PRICE)
                     today's price appended to sparkline tail
        - evening  → EOD daily up to T (try); fallback to realtime if T missing
      Historical replay (on_date < today):
        - any slot → EOD daily up to on_date; current = EOD on on_date
        - the data is settled, no realtime needed; staleness still verified

    snap.trade_date is set ONLY when verified == on_date; mismatched / missing
    data leaves snap.close as None so downstream renderer shows missing rather
    than fabricating today.
    """
    import tushare as ts

    is_today = on_date == _today_bjt()
    use_realtime = is_today and slot in ("noon", "evening")
    end = on_date.strftime("%Y%m%d")
    # For today/noon: history must end at T-1 (today's EOD not yet out).
    # For today/evening: try T first (post-close), fallback handled below.
    # For historical: end = on_date.
    if not is_today:
        hist_end = end
    elif slot == "morning":
        hist_end = end  # on_date is T-1 already (morning passes prev)
    elif slot == "noon":
        hist_end = (on_date - dt.timedelta(days=1)).strftime("%Y%m%d")
    else:  # evening today
        hist_end = end
    start = (on_date - dt.timedelta(days=history_days * 2 + 5)).strftime("%Y%m%d")

    out: list[IndexSnap] = []
    for ts_code, name, role in MARKET_INDICES:
        snap = IndexSnap(ts_code=ts_code, name=name, role=role,
                         close=None, pct_change=None, amount=None, trade_date=None)

        # Sparkline history
        try:
            df_hist = client.call("index_daily", ts_code=ts_code, start_date=start, end_date=hist_end)
        except Exception:
            df_hist = None
        if df_hist is not None and not df_hist.empty:
            df_hist = df_hist.sort_values("trade_date").tail(history_days)
            snap.history_close = [_f(v) for v in df_hist["close"]]
            snap.history_dates = df_hist["trade_date"].astype(str).tolist()

        # Current snapshot — branch on data source
        if use_realtime:
            # Slot cutoff — noon must reflect 11:30 close, evening must reflect 15:00 close.
            # If the report happens to run at 14:00 (between sessions) for noon,
            # using ts.realtime_quote PRICE would pull the 14:00 print (afternoon
            # session in progress), corrupting "noon" semantics. Same issue at
            # evening slot if realtime keeps ticking after-hours indications.
            # Solution: pull rt_min_daily 5MIN bars and pick the bar at the
            # slot cutoff (or the latest bar before cutoff if cutoff not yet hit).
            cutoff_hhmm = "11:30" if slot == "noon" else "15:00"
            cutoff_str = f"{on_date.strftime('%Y-%m-%d')} {cutoff_hhmm}:00"
            picked_close: float | None = None
            picked_time: str | None = None
            try:
                df_min = client.call("rt_min_daily", ts_code=ts_code, freq="5MIN")
                if df_min is not None and not df_min.empty:
                    time_col = "time" if "time" in df_min.columns else ("trade_time" if "trade_time" in df_min.columns else None)
                    if time_col is not None:
                        df_min = df_min.sort_values(time_col).reset_index(drop=True)
                        df_min = df_min[df_min[time_col] <= cutoff_str]
                        if not df_min.empty:
                            last_bar = df_min.iloc[-1]
                            picked_close = _f(last_bar.get("close"))
                            picked_time = str(last_bar.get(time_col))
            except Exception:
                pass

            # Pre-close (T-1 EOD) — needed to compute pct_change from the picked bar
            if picked_close is not None:
                pre_close: float | None = None
                try:
                    prev_end = (on_date - dt.timedelta(days=1)).strftime("%Y%m%d")
                    prev_start = (on_date - dt.timedelta(days=10)).strftime("%Y%m%d")
                    df_pre = client.call("index_daily", ts_code=ts_code,
                                          start_date=prev_start, end_date=prev_end)
                    if df_pre is not None and not df_pre.empty:
                        df_pre = df_pre.sort_values("trade_date")
                        pre_close = _f(df_pre.iloc[-1].get("close"))
                except Exception:
                    pass
                if pre_close:
                    snap.close = picked_close
                    snap.pct_change = (picked_close - pre_close) / pre_close * 100
                    snap.amount = None  # rt_min_daily doesn't aggregate session amount cleanly
                    snap.trade_date = on_date
                    snap.history_close.append(picked_close)
                    snap.history_dates.append(picked_time or on_date.strftime("%Y%m%d"))

            # Fallback to realtime_quote if minute bars missing (rare — early morning before 09:35)
            if snap.close is None:
                try:
                    rt = ts.realtime_quote(ts_code=ts_code, src="sina")
                    if rt is not None and not rt.empty:
                        row = rt.iloc[0]
                        rt_date = str(row.get("DATE") or "")
                        rt_price = _f(row.get("PRICE"))
                        rt_pre = _f(row.get("PRE_CLOSE"))
                        rt_amount = _f(row.get("AMOUNT"))
                        expected_date = on_date.strftime("%Y%m%d")
                        if rt_date == expected_date and rt_price and rt_pre:
                            snap.close = rt_price
                            snap.pct_change = (rt_price - rt_pre) / rt_pre * 100
                            snap.amount = (rt_amount / 1000.0) if rt_amount is not None else None
                            snap.trade_date = on_date
                            snap.history_close.append(rt_price)
                            snap.history_dates.append(expected_date)
                except Exception:
                    pass

            # Evening fallback: if realtime didn't yield (e.g., post-close API delay), try EOD daily for on_date
            if snap.close is None and slot == "evening":
                try:
                    df_eod = client.call("index_daily", ts_code=ts_code, start_date=end, end_date=end)
                    if df_eod is not None and not df_eod.empty:
                        last = df_eod.iloc[-1]
                        if _d(str(last.get("trade_date"))) == on_date:
                            snap.close = _f(last.get("close"))
                            snap.pct_change = _f(last.get("pct_chg"))
                            snap.amount = _f(last.get("amount"))
                            snap.trade_date = on_date
                            snap.history_close.append(snap.close)
                            snap.history_dates.append(end)
                except Exception:
                    pass
        else:
            # EOD / historical replay path:
            #   morning           → use df_hist last row (T-1 EOD)
            #   historical noon   → pick 11:30 bar from stk_mins (NOT the EOD close)
            #   historical evening → df_hist last row (15:00 EOD)
            if not is_today and slot == "noon":
                # Historical noon — pick the 11:30 bar so replay matches production cutoff
                try:
                    start_dt = f"{on_date.strftime('%Y-%m-%d')} 09:30:00"
                    end_dt = f"{on_date.strftime('%Y-%m-%d')} 11:30:00"
                    df_min = client.call("stk_mins", ts_code=ts_code, freq="5min",
                                          start_date=start_dt, end_date=end_dt)
                    if df_min is not None and not df_min.empty:
                        time_col = "time" if "time" in df_min.columns else ("trade_time" if "trade_time" in df_min.columns else None)
                        if time_col is not None:
                            df_min = df_min.sort_values(time_col)
                            last_bar = df_min.iloc[-1]
                            picked_close = _f(last_bar.get("close"))
                            # pre-close from prior trading day's index_daily
                            prev_end = (on_date - dt.timedelta(days=1)).strftime("%Y%m%d")
                            prev_start = (on_date - dt.timedelta(days=10)).strftime("%Y%m%d")
                            df_pre = client.call("index_daily", ts_code=ts_code,
                                                  start_date=prev_start, end_date=prev_end)
                            pre_close = None
                            if df_pre is not None and not df_pre.empty:
                                pre_close = _f(df_pre.sort_values("trade_date").iloc[-1].get("close"))
                            if picked_close and pre_close:
                                snap.close = picked_close
                                snap.pct_change = (picked_close - pre_close) / pre_close * 100
                                snap.trade_date = on_date
                                snap.history_close.append(picked_close)
                                snap.history_dates.append(str(last_bar.get(time_col)))
                except Exception:
                    pass
            else:
                # Morning, or historical evening — last row of index_daily on/before on_date
                if df_hist is not None and not df_hist.empty:
                    last = df_hist.iloc[-1]
                    last_td = _d(str(last.get("trade_date")))
                    if last_td == on_date:
                        snap.close = _f(last.get("close"))
                        snap.pct_change = _f(last.get("pct_chg"))
                        snap.amount = _f(last.get("amount"))
                        snap.trade_date = last_td
                    # else: stale — keep None
        out.append(snap)
    return out


# ─── Whole-A breadth + sentiment ──────────────────────────────────────────

@dataclass
class BreadthSnap:
    trade_date: dt.date | None
    total_amount: float | None       # 万亿元 (converted)
    total_amount_prev: float | None  # previous trading day, same units
    up_count: int | None
    down_count: int | None
    flat_count: int | None
    avg_pct_change: float | None
    limit_up_count: int | None
    limit_down_count: int | None
    broke_limit_count: int | None    # 炸板（涨停封单未维持）
    broke_limit_pct: float | None    # 炸板率
    max_consec_streak: int | None    # 最高连板
    consec_streak_dist: dict[int, int] = field(default_factory=dict)  # {streak: count}


def _fetch_realtime_breadth_snapshot(client: TuShareClient, *, on_date: dt.date) -> dict | None:
    """Aggregate whole-A breadth from rt_k (intraday cumulative snapshot) +
    stk_limit (today's up/down limit prices). Used during noon/evening of a
    live trading day when EOD `daily` isn't published yet.

    rt_k returns: ts_code, pre_close, open, high, low, close, vol, amount, num
    Cumulative since 09:30 — at 11:30 reflects morning session, post 15:00 reflects
    full day. Slot cutoff is enforced by WHEN this function is called (caller
    decides timing; data itself has no timestamp granularity).

    Returns dict shaped like the EOD `daily` aggregate or None on failure.
    """
    chunks = []
    for pattern in ("6*.SH", "0*.SZ", "3*.SZ"):
        try:
            df = client.call("rt_k", ts_code=pattern)
            if df is not None and not df.empty:
                chunks.append(df)
        except Exception:
            continue
    if not chunks:
        return None
    df = pd.concat(chunks, ignore_index=True).drop_duplicates(subset=["ts_code"])
    # Stocks must have valid pre_close + close
    df = df[df["pre_close"].notna() & df["close"].notna() & (df["pre_close"] > 0)]
    if df.empty:
        return None
    df["pct_chg"] = (df["close"] - df["pre_close"]) / df["pre_close"] * 100
    out = {
        "total_amount": float(df["amount"].sum()) / 1e8 / 1e4,  # 元 → 亿 → 万亿
        "up_count": int((df["pct_chg"] > 0).sum()),
        "down_count": int((df["pct_chg"] < 0).sum()),
        "flat_count": int((df["pct_chg"] == 0).sum()),
        "avg_pct_change": float(df["pct_chg"].mean()),
        "limit_up_count": None,
        "limit_down_count": None,
    }
    # Join with stk_limit to get realtime limit-up / limit-down counts.
    # Prefer pre-market CSV cache (scripts/premarket_warm_cache.py) when present
    # to save the API round trip; fall back to live API if missing.
    df_lim = None
    try:
        from pathlib import Path as _Path
        cache = _Path(__file__).parent.parent.parent.parent / "var" / "cache" / f"stk_limit_{on_date.isoformat()}.csv"
        if cache.exists():
            df_lim = pd.read_csv(cache, dtype={"ts_code": str})
    except Exception:
        pass
    if df_lim is None or df_lim.empty:
        try:
            df_lim = client.call("stk_limit", trade_date=on_date.strftime("%Y%m%d"))
        except Exception:
            df_lim = None
    if df_lim is not None and not df_lim.empty:
        try:
            merged = df.merge(df_lim[["ts_code", "up_limit", "down_limit"]], on="ts_code", how="left")
            # Small tolerance (0.5 cent) handles float rounding
            up_hit = (merged["close"] >= merged["up_limit"] - 0.005) & merged["up_limit"].notna()
            down_hit = (merged["close"] <= merged["down_limit"] + 0.005) & merged["down_limit"].notna()
            out["limit_up_count"] = int(up_hit.sum())
            out["limit_down_count"] = int(down_hit.sum())
        except Exception:
            pass
    return out


def fetch_breadth(client: TuShareClient, *, on_date: dt.date,
                   slot: str = "morning") -> BreadthSnap:
    """Whole-A breadth aggregate.

    slot/today routing:
      morning, or any historical replay → EOD `daily` (settled, full-day).
      today + (noon|evening) → realtime aggregation via rt_k + stk_limit.
        At noon: rt_k cumulative reflects 09:30→now; if called at 14:00 the
                 numbers include early afternoon — caller must run at ~11:35
                 to get true morning-session breadth (or accept the snapshot).
        At evening: rt_k cumulative ≈ full day; falls back to EOD daily
                    when EOD becomes available.
    """
    snap = BreadthSnap(trade_date=on_date, total_amount=None, total_amount_prev=None,
                       up_count=None, down_count=None, flat_count=None,
                       avg_pct_change=None, limit_up_count=None, limit_down_count=None,
                       broke_limit_count=None, broke_limit_pct=None,
                       max_consec_streak=None)
    end = on_date.strftime("%Y%m%d")
    is_today = on_date == _today_bjt()
    used_realtime = False
    if is_today and slot in ("noon", "evening"):
        agg = _fetch_realtime_breadth_snapshot(client, on_date=on_date)
        if agg is not None:
            snap.total_amount = agg["total_amount"]
            snap.up_count = agg["up_count"]
            snap.down_count = agg["down_count"]
            snap.flat_count = agg["flat_count"]
            snap.avg_pct_change = agg["avg_pct_change"]
            snap.limit_up_count = agg["limit_up_count"]
            snap.limit_down_count = agg["limit_down_count"]
            snap.trade_date = on_date
            used_realtime = True
    if not used_realtime:
        try:
            df = client.call("daily", trade_date=end)
            if df is not None and not df.empty:
                snap.total_amount = float(df["amount"].sum()) / 1e5 / 1e4   # 千元→亿→万亿
                snap.up_count = int((df["pct_chg"] > 0).sum())
                snap.down_count = int((df["pct_chg"] < 0).sum())
                snap.flat_count = int((df["pct_chg"] == 0).sum())
                snap.avg_pct_change = float(df["pct_chg"].mean())
        except Exception:
            pass
    # previous day amount
    for back in range(1, 8):
        prev_end = (on_date - dt.timedelta(days=back)).strftime("%Y%m%d")
        try:
            df_prev = client.call("daily", trade_date=prev_end)
            if df_prev is not None and not df_prev.empty:
                snap.total_amount_prev = float(df_prev["amount"].sum()) / 1e5 / 1e4
                break
        except Exception:
            continue
    # Limit-up / limit-down structure on the day
    try:
        df_lim = client.call("limit_list_d", trade_date=end)
        if df_lim is not None and not df_lim.empty:
            ups = df_lim[df_lim["limit"] == "U"]
            downs = df_lim[df_lim["limit"] == "D"]
            snap.limit_up_count = len(ups)
            snap.limit_down_count = len(downs)
            # Use up_stat (e.g. "1/2") to detect 炸板 (succeeded < attempted)
            broke = 0
            for stat in ups["up_stat"].fillna("").astype(str):
                m = re.match(r"^(\d+)/(\d+)$", stat.strip())
                if m:
                    succ, attempts = int(m.group(1)), int(m.group(2))
                    if attempts > succ:
                        broke += attempts - succ
            snap.broke_limit_count = broke
            if snap.limit_up_count and snap.limit_up_count > 0:
                snap.broke_limit_pct = broke / max(snap.limit_up_count + broke, 1)
    except Exception:
        pass
    # 连板高度: scan last 5 trading days of limit_list_d, group by ts_code
    try:
        streaks_by_code: dict[str, int] = defaultdict(int)
        for back in range(0, 5):
            d = on_date - dt.timedelta(days=back)
            try:
                df_d = client.call("limit_list_d", trade_date=d.strftime("%Y%m%d"))
            except Exception:
                continue
            if df_d is None or df_d.empty:
                continue
            ups = df_d[df_d["limit"] == "U"]
            for ts_code in ups["ts_code"]:
                streaks_by_code[ts_code] += 1
        if streaks_by_code:
            snap.max_consec_streak = max(streaks_by_code.values())
            dist: dict[int, int] = defaultdict(int)
            for v in streaks_by_code.values():
                dist[v] += 1
            snap.consec_streak_dist = dict(dist)
    except Exception:
        pass
    return snap


# ─── Top movers / fund flow / dragon-tiger ────────────────────────────────

@dataclass
class StockSnap:
    ts_code: str
    name: str | None
    pct_change: float | None
    amount: float | None
    moneyflow_net: float | None
    role: str | None       # 'limit_up' / 'gainer' / 'fund_top' / 'dragon_tiger'
    sector: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def fetch_fund_flow_top(client: TuShareClient, *, on_date: dt.date,
                         top_n: int = 30) -> list[StockSnap]:
    end = on_date.strftime("%Y%m%d")
    try:
        df = client.call("moneyflow", trade_date=end)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    df = df.copy()
    df["abs_net"] = df["net_mf_amount"].fillna(0).abs()
    top = df.nlargest(top_n, "abs_net")
    out: list[StockSnap] = []
    for _, r in top.iterrows():
        out.append(StockSnap(
            ts_code=r.get("ts_code"), name=None,
            pct_change=None, amount=None,
            moneyflow_net=_f(r.get("net_mf_amount")),
            role="fund_top",
        ))
    return out


def fetch_dragon_tiger(client: TuShareClient, *, on_date: dt.date,
                        top_n: int = 20) -> list[StockSnap]:
    end = on_date.strftime("%Y%m%d")
    try:
        df = client.call("top_list", trade_date=end)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    # Aggregate by ts_code (one stock may appear under multiple reasons)
    df = df.copy()
    if "net_amount" not in df.columns:
        df["net_amount"] = 0
    df = df.sort_values("net_amount", ascending=False, key=lambda s: s.abs())
    seen: set[str] = set()
    out: list[StockSnap] = []
    for _, r in df.iterrows():
        ts_code = r.get("ts_code")
        if ts_code in seen or len(out) >= top_n:
            continue
        seen.add(ts_code)
        out.append(StockSnap(
            ts_code=ts_code, name=str(r.get("name", "")),
            pct_change=_f(r.get("pct_change")) or _f(r.get("pct_chg")),
            amount=_f(r.get("amount")),
            moneyflow_net=_f(r.get("net_amount")),
            role="dragon_tiger",
            extra={
                "reason": str(r.get("reason", "") or ""),
                "turnover_rate": _f(r.get("turnover_rate")),
            },
        ))
    return out


# ─── SW industry daily (rotation source) ─────────────────────────────────

@dataclass
class SectorBar:
    code: str
    name: str
    close: float | None
    pct_change: float | None
    trade_date: dt.date | None
    rank: int | None = None


def fetch_sw_rotation(client: TuShareClient, *, on_date: dt.date) -> list[SectorBar]:
    """All 31 SW level-1 industries via sw_daily (uses pct_change, not pct_chg)."""
    end = on_date.strftime("%Y%m%d")
    start = (on_date - dt.timedelta(days=8)).strftime("%Y%m%d")
    out: list[SectorBar] = []
    for code, name in SW_LEVEL1:
        try:
            df = client.call("sw_daily", ts_code=code, start_date=start, end_date=end)
        except Exception:
            out.append(SectorBar(code, name, None, None, None)); continue
        if df is None or df.empty:
            out.append(SectorBar(code, name, None, None, None)); continue
        df = df.sort_values("trade_date")
        row = df.iloc[-1]
        out.append(SectorBar(
            code=code, name=name,
            close=_f(row.get("close")),
            pct_change=_f(row.get("pct_change")),
            trade_date=_d(str(row["trade_date"])),
        ))
    # Rank by pct_change desc
    valid = [s for s in out if s.pct_change is not None]
    valid.sort(key=lambda s: s.pct_change or 0, reverse=True)
    for i, s in enumerate(valid):
        s.rank = i + 1
    return out


# ─── Main-line candidates (动态 SW L2，V2.1) ──────────────────────────────

def fetch_main_lines(engine: Engine, *, on_date: dt.date,
                     top_n: int = MAIN_LINE_TOP_N) -> list[SectorBar]:
    """Top-N SW L2 sectors representing today's "main lines".

    V2.1 migration: replaces the fixed 15-element THS thematic-board list
    with a dynamic SW-only selection.
    V2.1.1 enhancement: prefers direct SW L2 OHLC from `raw_sw_daily` (now
    backfilled to L2). Falls back to member-stock aggregation if the L2 row
    is missing for `on_date`. Final fallback: rank by `raw_sw_daily.pct_change`.

    Returns a list of SectorBar with rank populated.
    """
    out: list[SectorBar] = []
    snapshot_month = on_date.replace(day=1)
    # Primary path: net_amount-based ranking from sector_moneyflow_sw_daily.
    # close/pct_change come from raw_sw_daily L2 row (V2.1.1); if absent
    # (e.g. L2 backfill incomplete), aggregate from member stocks.
    sql_primary = text("""
        WITH ranked AS (
            SELECT l2_code, l2_name, net_amount
              FROM smartmoney.sector_moneyflow_sw_daily
             WHERE trade_date = :td
               AND l2_code IS NOT NULL
               AND net_amount IS NOT NULL
             ORDER BY net_amount DESC
             LIMIT :n
        ),
        agg AS (
            SELECT s.l2_code,
                   AVG(d.pct_chg) AS pct_change,
                   AVG(d.close)   AS close
              FROM smartmoney.sw_member_monthly s
              JOIN smartmoney.raw_daily d
                ON d.ts_code = s.ts_code
               AND d.trade_date = :td
             WHERE s.snapshot_month = :sm
               AND s.l2_code IN (SELECT l2_code FROM ranked)
             GROUP BY s.l2_code
        )
        SELECT r.l2_code, r.l2_name, r.net_amount,
               COALESCE(sw.close,      a.close)      AS close,
               COALESCE(sw.pct_change, a.pct_change) AS pct_change
          FROM ranked r
          LEFT JOIN smartmoney.raw_sw_daily sw
                 ON sw.ts_code = r.l2_code
                AND sw.trade_date = :td
          LEFT JOIN agg a USING (l2_code)
         ORDER BY r.net_amount DESC
    """)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql_primary, {"td": on_date, "n": top_n, "sm": snapshot_month}).all()
        for r in rows:
            out.append(SectorBar(
                code=r.l2_code, name=r.l2_name or r.l2_code,
                close=_f(r.close),
                pct_change=_f(r.pct_change),
                trade_date=on_date,
            ))
    except Exception:
        out = []

    # Fallback: rank SW L2 by pct_change directly from raw_sw_daily
    if not out:
        sql_fb = text("""
            SELECT ts_code, name, close, pct_change
              FROM smartmoney.raw_sw_daily
             WHERE trade_date = :td
               AND pct_change IS NOT NULL
               AND (
                    ts_code LIKE '8011%' OR ts_code LIKE '8012%' OR
                    ts_code LIKE '8017%' OR ts_code LIKE '8018%' OR
                    ts_code LIKE '8019%'
               )
             ORDER BY pct_change DESC
             LIMIT :n
        """)
        try:
            with engine.connect() as conn:
                rows = conn.execute(sql_fb, {"td": on_date, "n": top_n}).all()
            for r in rows:
                out.append(SectorBar(
                    code=r.ts_code, name=str(r.name) if r.name else r.ts_code,
                    close=_f(r.close),
                    pct_change=_f(r.pct_change),
                    trade_date=on_date,
                ))
        except Exception:
            pass

    # Rank for display ordering: prefer pct_change desc; if all missing, keep
    # the upstream net_amount order (rank by position).
    valid = [s for s in out if s.pct_change is not None]
    if valid:
        valid.sort(key=lambda s: s.pct_change or 0, reverse=True)
        for i, s in enumerate(valid):
            s.rank = i + 1
    else:
        for i, s in enumerate(out):
            s.rank = i + 1
    return out


# ─── North / South / margin (re-use macro pattern) ────────────────────────

@dataclass
class FlowsSnap:
    north_money: float | None         # 亿元
    south_money: float | None
    hsgt_date: dt.date | None
    margin_total: float | None        # 万亿元
    margin_change: float | None       # 万亿元 vs prior
    margin_date: dt.date | None


def fetch_flows(client: TuShareClient, *, on_date: dt.date) -> FlowsSnap:
    snap = FlowsSnap(north_money=None, south_money=None, hsgt_date=None,
                      margin_total=None, margin_change=None, margin_date=None)
    end = on_date.strftime("%Y%m%d")
    start = (on_date - dt.timedelta(days=10)).strftime("%Y%m%d")
    try:
        df = client.call("moneyflow_hsgt", start_date=start, end_date=end)
        if df is not None and not df.empty:
            df = df.sort_values("trade_date").tail(1)
            row = df.iloc[-1]
            nm = row.get("north_money"); sm = row.get("south_money")
            snap.north_money = _f(nm) / 10000 if nm is not None and pd.notna(nm) else None
            snap.south_money = _f(sm) / 10000 if sm is not None and pd.notna(sm) else None
            snap.hsgt_date = _d(str(row["trade_date"]))
    except Exception:
        pass
    try:
        df = client.call("margin", start_date=start, end_date=end)
        if df is not None and not df.empty:
            df = df.sort_values("trade_date")
            grouped = df.groupby("trade_date")["rzye"].sum()
            if len(grouped) >= 1:
                snap.margin_total = float(grouped.iloc[-1]) / 1e12
                snap.margin_date = _d(str(grouped.index[-1]))
                if len(grouped) >= 2:
                    prev = float(grouped.iloc[-2]) / 1e12
                    snap.margin_change = snap.margin_total - prev
    except Exception:
        pass
    return snap


# ─── Stock metadata enrichment (names + close prices) ─────────────────────

def enrich_stocks(client: TuShareClient, *, on_date: dt.date,
                   stocks: list[StockSnap]) -> None:
    """Fill missing names, close, pct_change, amount via daily(trade_date=)."""
    if not stocks:
        return
    end = on_date.strftime("%Y%m%d")
    try:
        df = client.call("daily", trade_date=end)
    except Exception:
        return
    if df is None or df.empty:
        return
    by_code = {r.ts_code: r for r in df.itertuples()}
    # Stock names lookup (one-shot)
    try:
        nm_df = client.call("stock_basic", exchange="", list_status="L")
        names = {r.ts_code: r.name for r in nm_df.itertuples()}
    except Exception:
        names = {}
    for s in stocks:
        if not s.name:
            s.name = names.get(s.ts_code, "")
        d = by_code.get(s.ts_code)
        if d:
            if s.pct_change is None:
                s.pct_change = _f(getattr(d, "pct_chg", None))
            if s.amount is None:
                s.amount = _f(getattr(d, "amount", None))


# ─── Three-aux summary (read latest macro/asset/tech sections from DB) ───

@dataclass
class AuxReportSummary:
    family: str            # 'macro' | 'asset' | 'tech'
    headline: str | None
    tone_or_state: str | None
    summary: str | None
    bullets: list[dict[str, Any]] = field(default_factory=list)
    template_version: str | None = None


def fetch_three_aux_summaries(engine: Engine, *, report_date: dt.date,
                               report_type: str = "morning_long") -> dict[str, AuxReportSummary]:
    """Read the latest succeeded morning-report's tone/headline section per family."""
    sql = text("""
        SELECT r.report_family, r.template_version, s.section_key, s.content_json
          FROM report_sections s
          JOIN report_runs r ON r.report_run_id = s.report_run_id
         WHERE r.report_family IN ('macro', 'asset', 'tech')
           AND r.report_type = :rt
           AND r.report_date = :rd
           AND r.status = 'succeeded'
           AND (s.section_key LIKE '%.s1_tone' OR s.section_key LIKE '%.s1_headline')
         ORDER BY r.completed_at DESC
    """)
    out: dict[str, AuxReportSummary] = {}
    seen: set[str] = set()
    with engine.connect() as conn:
        for r in conn.execute(sql, {"rt": report_type, "rd": report_date}).all():
            family = r.report_family
            if family in seen:
                continue
            seen.add(family)
            cj = r.content_json or {}
            if isinstance(cj, str):
                try:
                    cj = json.loads(cj)
                except Exception:
                    cj = {}
            tone = cj.get("tone") or cj.get("tech_state") or cj.get("label") or None
            headline = cj.get("headline") or cj.get("label") or ""
            summary = cj.get("summary") or cj.get("text") or cj.get("review_summary") or ""
            out[family] = AuxReportSummary(
                family=family,
                headline=headline,
                tone_or_state=tone,
                summary=summary,
                bullets=cj.get("bullets") or cj.get("validation_points") or [],
                template_version=r.template_version,
            )
    return out


# ─── News (broad market) ─────────────────────────────────────────────────

_BROAD_MARKET_KEYWORDS: list[str] = [
    "A股", "上证", "深证", "创业板", "沪深300", "北交所",
    "成交额", "市场风险偏好", "板块轮动", "主线",
    "央行", "证监会", "国务院", "财政部", "降准", "降息", "LPR", "MLF",
    "北向", "南向", "外资", "两融",
    "稳增长", "新质生产力", "AI+", "并购重组", "退市", "国九条",
    "美联储", "鲍威尔", "美元指数", "OPEC",
]


def fetch_market_news(client: TuShareClient, *, end_bjt: dt.datetime,
                       lookback_hours: int = 24, max_keep: int = 30) -> pd.DataFrame:
    end_local = end_bjt.replace(tzinfo=None)
    start_local = end_local - dt.timedelta(hours=lookback_hours)
    s = start_local.strftime("%Y-%m-%d %H:%M:%S")
    e = end_local.strftime("%Y-%m-%d %H:%M:%S")
    pat = "|".join(re.escape(k) for k in _BROAD_MARKET_KEYWORDS)
    sources = [
        ("major_news", "新华网"),
        ("major_news", "财联社"),
        ("major_news", "华尔街见闻"),
        ("news", "cls"),
        ("news", "yicai"),
        ("news", "wallstreetcn"),
    ]
    keep: list[pd.DataFrame] = []
    for api, src in sources:
        try:
            df = client.call(api, src=src, start_date=s, end_date=e)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        title = df["title"].fillna("").astype(str) if "title" in df.columns else pd.Series([""] * len(df))
        content = df["content"].fillna("").astype(str) if "content" in df.columns else pd.Series([""] * len(df))
        blob = title + " " + content
        mask = blob.str.contains(pat, regex=True, na=False, case=False)
        hits = df[mask].copy()
        if hits.empty:
            continue
        hits["api"] = api
        hits["src_label"] = src
        keep.append(hits)
    if not keep:
        return pd.DataFrame()
    out = pd.concat(keep, ignore_index=True)
    if "url" in out.columns:
        out = out.drop_duplicates(subset=["url"], keep="first")
    if "title" in out.columns:
        out = out.drop_duplicates(subset=["title"], keep="first")
    if "datetime" in out.columns:
        out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
        out = out.sort_values("datetime", ascending=False)
    return out.head(max_keep).reset_index(drop=True)


# ─── helpers ──────────────────────────────────────────────────────────────

def _f(v: Any) -> float | None:
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _d(s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(s, "%Y%m%d").date()
    except Exception:
        return None
