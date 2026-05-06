"""All data the Macro morning/evening reports need, in one place.

Two flavours of source:
  - **Structured TuShare endpoints** — cn_gdp / cn_cpi / cn_ppi / cn_pmi /
    cn_m / sf_month / shibor / shibor_lpr / fx_daily / moneyflow_hsgt /
    margin / index_daily / fut_daily.
  - **DB memory tables** — macro_text_derived_indicators (新增贷款/贷款余额)
    and macro_policy_event_memory (curated active policy events).

Every fetcher returns a small typed dict so the section builders don't have to
deal with pandas / SQL details.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.report.timezones import BJT, to_bjt
from ifa.core.tushare import TuShareClient


# ─── Structured macro panel ─────────────────────────────────────────────────

@dataclass
class TimeSeries:
    name: str
    periods: list[str]              # e.g. ["202601", "202602", ...] oldest→newest
    values: list[float | None]
    yoy_values: list[float | None]
    mom_values: list[float | None]
    unit: str
    latest_period: str | None = None
    latest_value: float | None = None
    latest_yoy: float | None = None
    latest_mom: float | None = None


def _df_series(df: pd.DataFrame, period_col: str, val_col: str,
               yoy_col: str | None, mom_col: str | None,
               name: str, unit: str, last_n: int = 12) -> TimeSeries:
    if df is None or df.empty or period_col not in df.columns:
        return TimeSeries(name=name, periods=[], values=[], yoy_values=[], mom_values=[], unit=unit)
    df = df.sort_values(period_col).tail(last_n)
    periods = df[period_col].astype(str).tolist()
    values = [None if pd.isna(v) else float(v) for v in df[val_col]]
    yoy = [None if pd.isna(v) else float(v) for v in df[yoy_col]] if yoy_col and yoy_col in df.columns else [None] * len(periods)
    mom = [None if pd.isna(v) else float(v) for v in df[mom_col]] if mom_col and mom_col in df.columns else [None] * len(periods)
    ts = TimeSeries(name=name, periods=periods, values=values, yoy_values=yoy, mom_values=mom, unit=unit)
    if periods:
        ts.latest_period = periods[-1]
        ts.latest_value = values[-1]
        ts.latest_yoy = yoy[-1]
        ts.latest_mom = mom[-1]
    return ts


def fetch_macro_panel(client: TuShareClient) -> dict[str, TimeSeries]:
    """Pull GDP / CPI / PPI / PMI / M2 / SF / 新增贷款-via-cn_m? + recent series."""
    out: dict[str, TimeSeries] = {}

    try:
        out["GDP"] = _df_series(client.call("cn_gdp", start_q="2023Q1"),
                                "quarter", "gdp_yoy", None, None,
                                "GDP 同比", "%", last_n=10)
    except Exception:
        out["GDP"] = TimeSeries("GDP 同比", [], [], [], [], "%")
    try:
        df = client.call("cn_cpi", start_m="202401")
        out["CPI"] = _df_series(df, "month", "nt_yoy", None, "nt_mom",
                                "CPI 同比", "%", last_n=14)
    except Exception:
        out["CPI"] = TimeSeries("CPI 同比", [], [], [], [], "%")
    try:
        df = client.call("cn_ppi", start_m="202401")
        out["PPI"] = _df_series(df, "month", "ppi_yoy", None, None,
                                "PPI 同比", "%", last_n=14)
    except Exception:
        out["PPI"] = TimeSeries("PPI 同比", [], [], [], [], "%")
    try:
        df = client.call("cn_pmi", start_m="202401")
        # cn_pmi returns wide — 制造业 PMI is column 'pmi010000' or similar; fall back to first numeric.
        col = next((c for c in ("pmi010000", "PMI010000", "PMI010100") if c in df.columns), None)
        if col is None:
            num_cols = [c for c in df.columns if c.lower().startswith("pmi") and df[c].dtype.kind in "fi"]
            col = num_cols[0] if num_cols else None
        if col:
            out["PMI"] = _df_series(df, "month", col, None, None,
                                    "制造业 PMI", "", last_n=14)
        else:
            out["PMI"] = TimeSeries("制造业 PMI", [], [], [], [], "")
    except Exception:
        out["PMI"] = TimeSeries("制造业 PMI", [], [], [], [], "")
    try:
        df = client.call("cn_m", start_m="202401")
        out["M2"] = _df_series(df, "month", "m2", "m2_yoy", "m2_mom",
                               "M2 余额", "亿元", last_n=14)
        out["M1"] = _df_series(df, "month", "m1", "m1_yoy", "m1_mom",
                               "M1 余额", "亿元", last_n=14)
    except Exception:
        out["M2"] = TimeSeries("M2 余额", [], [], [], [], "亿元")
        out["M1"] = TimeSeries("M1 余额", [], [], [], [], "亿元")
    try:
        df = client.call("sf_month", start_m="202401")
        out["社融增量"] = _df_series(df, "month", "inc_month", None, None,
                                     "社融月度增量", "亿元", last_n=14)
        out["社融存量"] = _df_series(df, "month", "stk_endval", None, None,
                                     "社融存量", "万亿元", last_n=14)
    except Exception:
        out["社融增量"] = TimeSeries("社融月度增量", [], [], [], [], "亿元")
        out["社融存量"] = TimeSeries("社融存量", [], [], [], [], "万亿元")

    return out


# ─── Liquidity / FX / flows ────────────────────────────────────────────────

@dataclass
class LiquiditySnapshot:
    shibor_overnight: float | None = None
    shibor_1w: float | None = None
    shibor_3m: float | None = None
    shibor_date: dt.date | None = None
    lpr_1y: float | None = None
    lpr_5y: float | None = None
    lpr_date: dt.date | None = None
    usdcnh_close: float | None = None
    usdcnh_change: float | None = None
    usdcnh_date: dt.date | None = None
    north_money: float | None = None      # 亿元, last available trading day
    south_money: float | None = None
    hsgt_date: dt.date | None = None
    margin_total: float | None = None     # 万亿元, sum SSE+SZSE
    margin_change: float | None = None    # vs. previous available
    margin_date: dt.date | None = None


def fetch_liquidity_snapshot(client: TuShareClient, *, ref_date: dt.date) -> LiquiditySnapshot:
    snap = LiquiditySnapshot()
    end = ref_date.strftime("%Y%m%d")
    start = (ref_date - dt.timedelta(days=20)).strftime("%Y%m%d")

    # Staleness defense: data is only "current" if its publication date matches ref_date.
    # If TuShare returns yesterday's row (today's not published yet), keep snap.*_date
    # for renderer to prefix "T-1" rather than fabricating today.

    try:
        df = client.call("shibor", start_date=start, end_date=end).sort_values("date").tail(1)
        if not df.empty:
            row = df.iloc[-1]
            snap.shibor_overnight = float(row.get("on")) if pd.notna(row.get("on")) else None
            snap.shibor_1w = float(row.get("1w")) if pd.notna(row.get("1w")) else None
            snap.shibor_3m = float(row.get("3m")) if pd.notna(row.get("3m")) else None
            snap.shibor_date = dt.datetime.strptime(str(row["date"]), "%Y%m%d").date()
    except Exception:
        pass

    try:
        df = client.call("shibor_lpr", start_date=start, end_date=end).sort_values("date").tail(1)
        if not df.empty:
            row = df.iloc[-1]
            snap.lpr_1y = float(row.get("1y")) if pd.notna(row.get("1y")) else None
            snap.lpr_5y = float(row.get("5y")) if pd.notna(row.get("5y")) else None
            snap.lpr_date = dt.datetime.strptime(str(row["date"]), "%Y%m%d").date()
    except Exception:
        pass

    try:
        df = client.call("fx_daily", ts_code="USDCNH.FXCM",
                         start_date=start, end_date=end).sort_values("trade_date").tail(2)
        if not df.empty:
            last = df.iloc[-1]
            snap.usdcnh_close = float(last.get("bid_close")) if pd.notna(last.get("bid_close")) else None
            snap.usdcnh_date = dt.datetime.strptime(str(last["trade_date"]), "%Y%m%d").date()
            if len(df) >= 2 and snap.usdcnh_close is not None:
                prev = float(df.iloc[-2].get("bid_close"))
                snap.usdcnh_change = snap.usdcnh_close - prev
    except Exception:
        pass

    try:
        df = client.call("moneyflow_hsgt", start_date=start, end_date=end).sort_values("trade_date").tail(1)
        if not df.empty:
            row = df.iloc[-1]
            nm = row.get("north_money")
            sm = row.get("south_money")
            snap.north_money = float(nm) / 10000 if pd.notna(nm) else None
            snap.south_money = float(sm) / 10000 if pd.notna(sm) else None
            snap.hsgt_date = dt.datetime.strptime(str(row["trade_date"]), "%Y%m%d").date()
    except Exception:
        pass

    try:
        df = client.call("margin", start_date=start, end_date=end)
        if not df.empty:
            df = df.sort_values("trade_date")
            grouped = df.groupby("trade_date")["rzye"].sum()
            if len(grouped) >= 1:
                latest_date = grouped.index[-1]
                snap.margin_total = float(grouped.iloc[-1]) / 1e12  # 元 → 万亿
                snap.margin_date = dt.datetime.strptime(str(latest_date), "%Y%m%d").date()
                if len(grouped) >= 2:
                    prev = float(grouped.iloc[-2]) / 1e12
                    snap.margin_change = snap.margin_total - prev
    except Exception:
        pass

    return snap


# ─── Cross-asset (HK + futures) ─────────────────────────────────────────────

@dataclass
class AssetSnapshot:
    name: str
    code: str
    latest: float | None = None
    pct_change: float | None = None
    period: str | None = None


CROSS_ASSET_TARGETS: list[tuple[str, str, str]] = [
    # (display_name, ts_code, kind)
    ("恒生指数", "HSI.HI", "index"),
    ("恒生科技", "HSTECH.HI", "index"),
    ("沪深300", "000300.SH", "index"),
    ("上证综指", "000001.SH", "index"),
    ("沪金主连", "AU2606.SHF", "fut"),
    ("沪铜主连", "CU2606.SHF", "fut"),
    ("螺纹主连", "RB2610.SHF", "fut"),
    ("原油主连", "SC2606.INE", "fut"),
]


def fetch_cross_asset(client: TuShareClient, *, ref_date: dt.date) -> list[AssetSnapshot]:
    end = ref_date.strftime("%Y%m%d")
    start = (ref_date - dt.timedelta(days=10)).strftime("%Y%m%d")
    out: list[AssetSnapshot] = []
    for name, code, kind in CROSS_ASSET_TARGETS:
        try:
            if kind == "index":
                df = client.call("index_daily", ts_code=code, start_date=start, end_date=end)
            else:
                df = client.call("fut_daily", ts_code=code, start_date=start, end_date=end)
            if df is None or df.empty:
                out.append(AssetSnapshot(name=name, code=code))
                continue
            df = df.sort_values("trade_date")
            row = df.iloc[-1]
            row_td = dt.datetime.strptime(str(row["trade_date"]), "%Y%m%d").date()
            # Staleness defense: only treat as "current" if matches on_date.
            # When stale, still report latest but mark period as the real date so
            # renderer can prefix "截至 YYYY-MM-DD" rather than implying today.
            close = float(row["close"]) if pd.notna(row.get("close")) else None
            pct = float(row.get("pct_chg")) if pd.notna(row.get("pct_chg")) else None
            if pct is None and close is not None and len(df) >= 2:
                prev = float(df.iloc[-2]["close"])
                pct = (close - prev) / prev * 100 if prev else None
            out.append(AssetSnapshot(
                name=name, code=code, latest=close, pct_change=pct,
                period=row_td.strftime("%Y-%m-%d"),
            ))
        except Exception:
            out.append(AssetSnapshot(name=name, code=code))
    return out


# ─── DB memory tables ───────────────────────────────────────────────────────

@dataclass
class TextDerivedRow:
    indicator_name: str
    indicator_display: str
    reported_period: str | None
    value: float | None
    unit: str | None
    yoy: float | None
    release_type: str | None
    publisher_or_origin: str | None
    source_name: str | None
    source_publish_time: dt.datetime | None
    evidence_sentence: str | None
    confidence: str | None


def fetch_text_derived(engine: Engine, *, since_days: int = 120, limit: int = 30) -> list[TextDerivedRow]:
    sql = text("""
        SELECT indicator_name, reported_period, value, unit, yoy,
               release_type, publisher_or_origin, source_name,
               source_publish_time, evidence_sentence, confidence
          FROM macro_text_derived_indicators
         WHERE source_publish_time >= now() - (:days || ' days')::interval
           AND status IN ('extracted','confirmed','revised')
         ORDER BY source_publish_time DESC NULLS LAST, indicator_name
         LIMIT :lim
    """)
    display = {"new_rmb_loans": "新增人民币贷款", "rmb_loan_balance": "人民币贷款余额"}
    with engine.connect() as conn:
        rows = conn.execute(sql, {"days": since_days, "lim": limit}).all()
    return [
        TextDerivedRow(
            indicator_name=r.indicator_name,
            indicator_display=display.get(r.indicator_name, r.indicator_name),
            reported_period=r.reported_period,
            value=float(r.value) if r.value is not None else None,
            unit=r.unit,
            yoy=float(r.yoy) if r.yoy is not None else None,
            release_type=r.release_type,
            publisher_or_origin=r.publisher_or_origin,
            source_name=r.source_name,
            source_publish_time=r.source_publish_time,
            evidence_sentence=r.evidence_sentence,
            confidence=r.confidence,
        )
        for r in rows
    ]


@dataclass
class PolicyEventRow:
    event_id: str
    event_date: dt.date | None
    publish_time: dt.datetime | None
    policy_dimension: str
    policy_signal: str
    event_title: str | None
    summary: str | None
    market_implication: str | None
    affected_areas: list[str]
    importance: str | None  # not stored but inferred from confidence
    confidence: str | None
    source_name: str | None
    source_url: str | None
    carry_forward_until: dt.date | None


def fetch_active_policy_events(engine: Engine, *, since_days: int = 14, limit: int = 60) -> list[PolicyEventRow]:
    sql = text("""
        SELECT event_id, event_date, publish_time, policy_dimension, policy_signal,
               event_title, summary, market_implication, affected_areas,
               confidence, source_name, source_url, carry_forward_until
          FROM macro_policy_event_memory
         WHERE status = 'active'
           AND (publish_time IS NULL OR publish_time >= now() - (:days || ' days')::interval)
         ORDER BY publish_time DESC NULLS LAST
         LIMIT :lim
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"days": since_days, "lim": limit}).all()
    out: list[PolicyEventRow] = []
    for r in rows:
        areas: list[str]
        try:
            if isinstance(r.affected_areas, list):
                areas = list(r.affected_areas)
            elif isinstance(r.affected_areas, str):
                import json
                areas = json.loads(r.affected_areas)
            else:
                areas = []
        except Exception:
            areas = []
        out.append(PolicyEventRow(
            event_id=r.event_id,
            event_date=r.event_date,
            publish_time=r.publish_time,
            policy_dimension=r.policy_dimension,
            policy_signal=r.policy_signal,
            event_title=r.event_title,
            summary=r.summary,
            market_implication=r.market_implication,
            affected_areas=areas,
            importance=r.confidence,
            confidence=r.confidence,
            source_name=r.source_name,
            source_url=r.source_url,
            carry_forward_until=r.carry_forward_until,
        ))
    return out


# ─── A-share market state for evening report ───────────────────────────────

@dataclass
class MarketDay:
    trade_date: dt.date
    sh_close: float | None
    sh_pct: float | None
    sz_close: float | None
    sz_pct: float | None
    cyb_close: float | None
    cyb_pct: float | None
    hs300_close: float | None
    hs300_pct: float | None
    total_amount: float | None       # 万亿元
    total_amount_prev: float | None
    up_count: int | None             # how many stocks up
    down_count: int | None
    flat_count: int | None


def fetch_market_day(client: TuShareClient, *, on_date: dt.date,
                      slot: str = "morning", engine=None) -> MarketDay:
    """Whole-A snapshot for macro morning/evening.

    Note: macro family currently has no noon report — the slot="noon" branch
    below is unreachable from production runners. Kept for two reasons:
      1. API parity with market family (which DOES have noon)
      2. Future-proof if macro ever adds a noon report
    The evening realtime path (rt_min_daily + rt_k breadth) is the live value:
    it covers the ~15:00→17:00 window when TuShare EOD batch hasn't published
    yet, without which an early-evening run would surface T-1 data.

    Slot routing:
      today + (noon|evening) → rt_min_daily 5MIN bars cut at 11:30 / 15:00,
                                 paired with prior-day index_daily for pre_close
                                 to compute pct_change.
      historical noon         → stk_mins 09:30→11:30 last bar, prior-day pre_close.
      morning / historical evening → index_daily EOD on or before on_date.

    Whole-A breadth (up/down/flat + total_amount): today/noon|evening uses
    rt_k aggregation (delegated to market._fetch_realtime_breadth_snapshot);
    morning + historical use EOD `daily`.
    """
    from ifa.families.market.data import _fetch_realtime_breadth_snapshot
    md = MarketDay(trade_date=on_date,
                   sh_close=None, sh_pct=None,
                   sz_close=None, sz_pct=None,
                   cyb_close=None, cyb_pct=None,
                   hs300_close=None, hs300_pct=None,
                   total_amount=None, total_amount_prev=None,
                   up_count=None, down_count=None, flat_count=None)
    end = on_date.strftime("%Y%m%d")
    start = (on_date - dt.timedelta(days=8)).strftime("%Y%m%d")
    is_today = on_date == dt.datetime.now(BJT).date()
    use_realtime = is_today and slot in ("noon", "evening")

    indices = [
        ("000001.SH", "sh_close", "sh_pct"),
        ("399001.SZ", "sz_close", "sz_pct"),
        ("399006.SZ", "cyb_close", "cyb_pct"),
        ("000300.SH", "hs300_close", "hs300_pct"),
    ]

    if use_realtime:
        import tushare as ts_lib
        cutoff_hhmm = "11:30" if slot == "noon" else "15:00"
        cutoff_str = f"{on_date.strftime('%Y-%m-%d')} {cutoff_hhmm}:00"
        for code, attr_close, attr_pct in indices:
            picked_close = None
            # Path 1: rt_min_daily 5MIN bars (works for stocks; some indices return 0 rows)
            try:
                df_min = client.call("rt_min_daily", ts_code=code, freq="5MIN")
                if df_min is not None and not df_min.empty:
                    time_col = "time" if "time" in df_min.columns else ("trade_time" if "trade_time" in df_min.columns else None)
                    if time_col is not None:
                        df_min = df_min.sort_values(time_col).reset_index(drop=True)
                        df_min = df_min[df_min[time_col] <= cutoff_str]
                        if not df_min.empty:
                            picked_close = float(df_min.iloc[-1]["close"]) if pd.notna(df_min.iloc[-1].get("close")) else None
            except Exception:
                pass
            pre_close = None
            try:
                prev_end = (on_date - dt.timedelta(days=1)).strftime("%Y%m%d")
                prev_start = (on_date - dt.timedelta(days=10)).strftime("%Y%m%d")
                df_pre = client.call("index_daily", ts_code=code, start_date=prev_start, end_date=prev_end)
                if df_pre is not None and not df_pre.empty:
                    pre_close = float(df_pre.sort_values("trade_date").iloc[-1]["close"])
            except Exception:
                pass
            if picked_close is not None and pre_close:
                setattr(md, attr_close, picked_close)
                setattr(md, attr_pct, (picked_close - pre_close) / pre_close * 100)
                md.trade_date = on_date
                continue
            # Path 2: realtime_quote fallback (sina backend; index PRICE supported)
            # Note: PRICE keeps ticking through afternoon — at noon slot this is
            # only acceptable if the report runs ≤11:30; the cutoff cannot be
            # enforced on this snapshot. Caller should prefer slot-correct timing.
            try:
                rt = ts_lib.realtime_quote(ts_code=code, src="sina")
                if rt is not None and not rt.empty:
                    row = rt.iloc[0]
                    rt_date = str(row.get("DATE") or "")
                    rt_price = float(row.get("PRICE")) if row.get("PRICE") is not None else None
                    rt_pre = float(row.get("PRE_CLOSE")) if row.get("PRE_CLOSE") is not None else None
                    expected_date = on_date.strftime("%Y%m%d")
                    if rt_date == expected_date and rt_price and rt_pre:
                        setattr(md, attr_close, rt_price)
                        setattr(md, attr_pct, (rt_price - rt_pre) / rt_pre * 100)
                        md.trade_date = on_date
            except Exception:
                pass
        # Evening fallback to EOD index_daily if minute path failed
        if slot == "evening" and md.sh_close is None:
            try:
                df_eod = client.call("index_daily", ts_code="000001.SH", start_date=end, end_date=end)
                if df_eod is not None and not df_eod.empty:
                    last = df_eod.iloc[-1]
                    if dt.datetime.strptime(str(last["trade_date"]), "%Y%m%d").date() == on_date:
                        md.sh_close = float(last["close"])
                        md.sh_pct = float(last.get("pct_chg")) if pd.notna(last.get("pct_chg")) else None
                        md.trade_date = on_date
            except Exception:
                pass
    else:
        # EOD / historical replay path
        if not is_today and slot == "noon":
            # Historical noon — pull stk_mins 11:30 bar per index for cutoff parity
            for code, attr_close, attr_pct in indices:
                try:
                    df_min = client.call("stk_mins", ts_code=code, freq="5min",
                                          start_date=f"{on_date.strftime('%Y-%m-%d')} 09:30:00",
                                          end_date=f"{on_date.strftime('%Y-%m-%d')} 11:30:00")
                    if df_min is None or df_min.empty:
                        continue
                    time_col = "time" if "time" in df_min.columns else ("trade_time" if "trade_time" in df_min.columns else None)
                    if time_col is None:
                        continue
                    last_bar = df_min.sort_values(time_col).iloc[-1]
                    picked_close = float(last_bar["close"]) if pd.notna(last_bar.get("close")) else None
                    prev_end = (on_date - dt.timedelta(days=1)).strftime("%Y%m%d")
                    prev_start = (on_date - dt.timedelta(days=10)).strftime("%Y%m%d")
                    df_pre = client.call("index_daily", ts_code=code, start_date=prev_start, end_date=prev_end)
                    pre_close = None
                    if df_pre is not None and not df_pre.empty:
                        pre_close = float(df_pre.sort_values("trade_date").iloc[-1]["close"])
                    if picked_close and pre_close:
                        setattr(md, attr_close, picked_close)
                        setattr(md, attr_pct, (picked_close - pre_close) / pre_close * 100)
                        md.trade_date = on_date
                except Exception:
                    continue
        else:
            # Morning, or historical evening — index_daily EOD with strict staleness gate
            last_actual_td: dt.date | None = None
            for code, attr_close, attr_pct in indices:
                try:
                    df = client.call("index_daily", ts_code=code, start_date=start, end_date=end)
                    if df is None or df.empty:
                        continue
                    df = df.sort_values("trade_date")
                    row = df.iloc[-1]
                    row_td = dt.datetime.strptime(str(row["trade_date"]), "%Y%m%d").date()
                    if row_td != on_date:
                        last_actual_td = row_td
                        continue
                    setattr(md, attr_close, float(row["close"]) if pd.notna(row.get("close")) else None)
                    setattr(md, attr_pct, float(row.get("pct_chg")) if pd.notna(row.get("pct_chg")) else None)
                    md.trade_date = row_td
                    last_actual_td = row_td
                except Exception:
                    continue
            if md.sh_close is None and md.sz_close is None and last_actual_td and last_actual_td != on_date:
                md.trade_date = last_actual_td

    # Whole-A breadth — slot-aware (rt_k for today/noon|evening, EOD daily otherwise)
    if use_realtime:
        agg = _fetch_realtime_breadth_snapshot(client, on_date=on_date)
        if agg is not None:
            md.total_amount = agg["total_amount"]
            md.up_count = agg["up_count"]
            md.down_count = agg["down_count"]
            md.flat_count = agg["flat_count"]
    else:
        try:
            df = client.call("daily", trade_date=end)
            if df is not None and not df.empty:
                md.total_amount = float(df["amount"].sum()) / 1e5 / 1e4
                md.up_count = int((df["pct_chg"] > 0).sum())
                md.down_count = int((df["pct_chg"] < 0).sum())
                md.flat_count = int((df["pct_chg"] == 0).sum())
        except Exception:
            pass
    # Previous TRADING day's amount — calendar-T-1 fails on Mon / post-holiday.
    prev_td: dt.date | None = None
    if engine is not None:
        try:
            from ifa.core.calendar import prev_trading_day
            prev_td = prev_trading_day(engine, on_date)
        except Exception:
            prev_td = None
    if prev_td is None:
        # Fallback: walk back up to 14 calendar days for trade_cal-less envs
        for back in range(1, 14):
            try:
                df_prev = client.call("daily", trade_date=(on_date - dt.timedelta(days=back)).strftime("%Y%m%d"))
                if df_prev is not None and not df_prev.empty:
                    md.total_amount_prev = float(df_prev["amount"].sum()) / 1e5 / 1e4
                    break
            except Exception:
                continue
    else:
        try:
            df_prev = client.call("daily", trade_date=prev_td.strftime("%Y%m%d"))
            if df_prev is not None and not df_prev.empty:
                md.total_amount_prev = float(df_prev["amount"].sum()) / 1e5 / 1e4
        except Exception:
            pass

    return md
