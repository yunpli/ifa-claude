"""Tech report data layer.

V2.1: Migrated from THS to SW. Sector-board snapshots use `sw_daily`
against SW L2 indices; sector membership uses `sw_member_monthly`
(point-in-time correct, snapshot_month = first day of trade_date's
month). All `ths_daily` / `ths_member` calls have been removed from
the primary path.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.report.timezones import BJT
from ifa.core.tushare import TuShareClient

from .focus import FocusStock, get_focus_for, tech_only
from .universe import (
    AI_LAYERS,
    all_tech_sector_codes,
    sector_name,
    sector_to_layer,
)


# ─── Layer-level board snapshot ────────────────────────────────────────────

@dataclass
class BoardSnapshot:
    ts_code: str
    name: str
    layer_id: str
    close: float | None
    pct_change: float | None
    volume: float | None
    turnover_rate: float | None
    trade_date: dt.date | None
    history_close: list[float | None] = field(default_factory=list)
    history_dates: list[str] = field(default_factory=list)


def fetch_board_performance(
    client: TuShareClient, *, on_date: dt.date, history_days: int = 10,
    slot: str = "morning", engine=None,
) -> dict[str, list[BoardSnapshot]]:
    """SW L2 layer boards over ~10 days, keyed by layer_id.

    Note: tech family has no noon report — the slot="noon" branch is
    unreachable from production runners. Kept for API parity. The evening
    realtime path covers the ~15:00→17:00 window before TuShare sw_daily
    EOD publishes.

    Slot routing:
      today + (noon|evening) → realtime close/pct from member rt_k aggregation
        (history sparkline still uses sw_daily T-1 EOD series, with realtime
        bar appended to tail).
      morning / historical → sw_daily EOD with staleness gate.

    SW codes don't support rt_min_daily / stk_mins — synthesis from
    constituents (MV-weighted) is the only realtime path.
    """
    from ifa.core.report.timezones import BJT
    is_today = on_date == dt.datetime.now(BJT).date()
    use_realtime = is_today and slot in ("noon", "evening") and engine is not None

    out: dict[str, list[BoardSnapshot]] = {l.layer_id: [] for l in AI_LAYERS}
    layer_lookup = sector_to_layer()
    end = on_date.strftime("%Y%m%d")
    start = (on_date - dt.timedelta(days=history_days * 2 + 5)).strftime("%Y%m%d")

    realtime_agg: dict[str, dict] = {}
    if use_realtime:
        from ifa.families.market._sw_realtime import compute_sw_realtime_snapshot
        codes = [c for c in all_tech_sector_codes() if layer_lookup.get(c)]
        # SW L2 codes — pass level="l2" so member lookup uses l2_code
        realtime_agg = compute_sw_realtime_snapshot(client, engine, on_date=on_date,
                                                      sw_codes=codes, level="l2")

    for code in all_tech_sector_codes():
        layer_id = layer_lookup.get(code)
        if layer_id is None:
            continue
        name = sector_name(code)
        try:
            df = client.call("sw_daily", ts_code=code, start_date=start, end_date=end)
        except Exception:
            df = None
        if df is None or df.empty:
            out[layer_id].append(BoardSnapshot(
                ts_code=code, name=name, layer_id=layer_id,
                close=None, pct_change=None, volume=None, turnover_rate=None,
                trade_date=None,
            ))
            continue
        df = df.sort_values("trade_date").tail(history_days)
        latest = df.iloc[-1]
        latest_td = _d(str(latest.get("trade_date")))
        history_close = [_f(v) for v in df["close"]]
        history_dates = df["trade_date"].astype(str).tolist()

        if use_realtime:
            v = realtime_agg.get(code, {})
            rt_close = v.get("close")
            rt_pct = v.get("pct_change")
            if rt_close is not None and rt_pct is not None:
                history_close.append(rt_close)
                history_dates.append(on_date.strftime("%Y%m%d"))
                snap = BoardSnapshot(
                    ts_code=code, name=name, layer_id=layer_id,
                    close=rt_close, pct_change=rt_pct,
                    volume=None, turnover_rate=None,
                    trade_date=on_date,
                    history_close=history_close, history_dates=history_dates,
                )
            else:
                snap = BoardSnapshot(
                    ts_code=code, name=name, layer_id=layer_id,
                    close=None, pct_change=None, volume=None, turnover_rate=None,
                    trade_date=latest_td,
                    history_close=history_close, history_dates=history_dates,
                )
        else:
            is_current = latest_td == on_date
            snap = BoardSnapshot(
                ts_code=code, name=name, layer_id=layer_id,
                close=_f(latest.get("close")) if is_current else None,
                pct_change=_f(latest.get("pct_change")) if is_current else None,
                volume=_f(latest.get("vol")) if is_current else None,
                turnover_rate=_f(latest.get("turnover_rate")) if is_current else None,
                trade_date=latest_td,
                history_close=history_close, history_dates=history_dates,
            )
        out[layer_id].append(snap)
    return out


# ─── Top movers (limit-up + dragon-tiger + money flow) ─────────────────────

@dataclass
class StockMover:
    ts_code: str
    name: str
    pct_change: float | None
    amount: float | None         # 元
    turnover_rate: float | None
    layer_id: str | None
    board_hits: list[str] = field(default_factory=list)  # which SW L2 sectors it belongs to
    limit_status: str | None = None      # 'U' / 'D' / 'Z' from limit_list_d
    moneyflow_net: float | None = None   # 主力资金净流入 (元)
    role: str | None = None              # 'leader' | 'mid' | 'breakout' | 'laggard' | 'limit_up'


def fetch_limit_up_tech(
    client: TuShareClient, *, on_date: dt.date,
    tech_members: dict[str, set[str]],  # sector_code -> set(stock ts_codes)
) -> list[StockMover]:
    """Fetch limit-up list and tag tech-sector members."""
    end = on_date.strftime("%Y%m%d")
    try:
        df = client.call("limit_list_d", trade_date=end)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    code_to_layers: dict[str, list[str]] = {}
    layer_lookup = sector_to_layer()
    for sector_code, members in tech_members.items():
        layer_id = layer_lookup.get(sector_code, "")
        for ts_code in members:
            code_to_layers.setdefault(ts_code, []).append(layer_id)
    movers: list[StockMover] = []
    for _, r in df.iterrows():
        ts_code = r.get("ts_code")
        if ts_code not in code_to_layers:
            continue
        layers = code_to_layers[ts_code]
        movers.append(StockMover(
            ts_code=ts_code, name=str(r.get("name", "")),
            pct_change=_f(r.get("pct_chg")),
            amount=_f(r.get("amount")),
            turnover_rate=_f(r.get("turnover_rate")),
            layer_id=layers[0] if layers else None,
            board_hits=[],
            limit_status=str(r.get("limit", "")),
            role="limit_up",
        ))
    return movers


def fetch_top_movers_in_tech(
    client: TuShareClient, *, on_date: dt.date,
    tech_members: dict[str, set[str]],
    top_n: int = 30,
) -> list[StockMover]:
    """Filter the day's `daily` table to tech-sector members and return top movers."""
    end = on_date.strftime("%Y%m%d")
    try:
        df = client.call("daily", trade_date=end)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    layer_lookup = sector_to_layer()
    code_to_layer: dict[str, str] = {}
    code_to_boards: dict[str, list[str]] = {}
    for sector_code, members in tech_members.items():
        layer_id = layer_lookup.get(sector_code, "")
        for ts_code in members:
            code_to_layer.setdefault(ts_code, layer_id)
            code_to_boards.setdefault(ts_code, []).append(sector_code)
    df = df[df["ts_code"].isin(code_to_layer.keys())].copy()
    if df.empty:
        return []
    df = df.sort_values("pct_chg", ascending=False).head(top_n)
    movers: list[StockMover] = []
    for _, r in df.iterrows():
        ts_code = r.get("ts_code")
        movers.append(StockMover(
            ts_code=ts_code, name="",
            pct_change=_f(r.get("pct_chg")),
            amount=_f(r.get("amount")),
            turnover_rate=None,
            layer_id=code_to_layer.get(ts_code),
            board_hits=code_to_boards.get(ts_code, []),
            limit_status=None,
            role="breakout" if (r.get("pct_chg") or 0) >= 5 else "mid",
        ))
    return movers


def fetch_money_flow_top(
    client: TuShareClient, *, on_date: dt.date,
    ts_codes: list[str],
) -> dict[str, float]:
    """Returns {ts_code -> net_mf_amount} for the given stocks on that date."""
    if not ts_codes:
        return {}
    end = on_date.strftime("%Y%m%d")
    try:
        df = client.call("moneyflow", trade_date=end)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    df = df[df["ts_code"].isin(ts_codes)]
    return {r.ts_code: _f(r.net_mf_amount) for r in df.itertuples() if r.net_mf_amount is not None}


# ─── Tech-sector membership resolution (PIT via SW monthly snapshot) ───────

def resolve_tech_members(
    client: TuShareClient,
    engine: Engine | None = None,
    *,
    trade_date: dt.date | None = None,
) -> dict[str, set[str]]:
    """For each curated tech SW L2 sector, fetch its members from
    `smartmoney.sw_member_monthly` at the snapshot_month corresponding
    to `trade_date` (defaults to today). Point-in-time correct.

    The `client` argument is unused (kept for backward-compat with old
    THS-based callers); membership is resolved purely from the DB.
    """
    if engine is None:
        # Lazy import so module can still be imported without DB config.
        from ifa.core.db import get_engine
        engine = get_engine()
    if trade_date is None:
        trade_date = dt.date.today()
    snapshot_month = trade_date.replace(day=1)
    codes = all_tech_sector_codes()
    if not codes:
        return {}
    out: dict[str, set[str]] = {c: set() for c in codes}
    sql = text("""
        SELECT l2_code, ts_code
          FROM smartmoney.sw_member_monthly
         WHERE snapshot_month = :sm
           AND l2_code = ANY(:codes)
    """)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, {"sm": snapshot_month, "codes": codes}).all()
    except Exception:
        rows = []
    for l2_code, ts_code in rows:
        if l2_code in out and ts_code:
            out[l2_code].add(str(ts_code))
    # Fallback: if requested month has no rows, take most recent <= snapshot_month
    if not any(out.values()):
        try:
            with engine.connect() as conn:
                latest = conn.execute(text(
                    "SELECT MAX(snapshot_month) FROM smartmoney.sw_member_monthly "
                    "WHERE snapshot_month <= :sm"
                ), {"sm": snapshot_month}).scalar()
                if latest is not None and latest != snapshot_month:
                    rows = conn.execute(sql, {"sm": latest, "codes": codes}).all()
                    for l2_code, ts_code in rows:
                        if l2_code in out and ts_code:
                            out[l2_code].add(str(ts_code))
        except Exception:
            pass
    return out


# ─── US tech overnight ─────────────────────────────────────────────────────

US_TECH_TICKERS: list[tuple[str, str, str]] = [
    # (ts_code, display_name, role)
    ("NVDA",   "NVIDIA",          "AI 算力"),
    ("AVGO",   "Broadcom",        "AI ASIC / 网络"),
    ("AMD",    "AMD",             "GPU / CPU"),
    ("TSM",    "TSMC",            "晶圆代工"),
    ("MU",     "Micron",          "存储 / HBM"),
    ("ASML",   "ASML",            "光刻机"),
    ("META",   "Meta",            "AI 应用 / 模型"),
    ("MSFT",   "Microsoft",       "云 / 模型 / 应用"),
    ("GOOGL",  "Alphabet",        "云 / 模型 / 应用"),
    ("TSLA",   "Tesla",           "机器人 / 智能驾驶"),
]


@dataclass
class USStockSnap:
    ticker: str
    display_name: str
    role: str
    close: float | None
    pct_change: float | None
    trade_date: dt.date | None


def fetch_us_tech_overnight(client: TuShareClient, *, ref_date: dt.date) -> list[USStockSnap]:
    """Fetch latest US tech daily — best effort given account permission."""
    end = ref_date.strftime("%Y%m%d")
    start = (ref_date - dt.timedelta(days=10)).strftime("%Y%m%d")
    out: list[USStockSnap] = []
    for ticker, name, role in US_TECH_TICKERS:
        snap = USStockSnap(ticker=ticker, display_name=name, role=role,
                           close=None, pct_change=None, trade_date=None)
        try:
            df = client.call("us_daily", ts_code=ticker, start_date=start, end_date=end)
        except Exception:
            out.append(snap); continue
        if df is None or df.empty:
            out.append(snap); continue
        df = df.sort_values("trade_date")
        row = df.iloc[-1]
        snap.close = _f(row.get("close"))
        snap.pct_change = _f(row.get("pct_change"))
        if snap.pct_change is None and len(df) >= 2:
            prev = _f(df.iloc[-2].get("close"))
            if prev and snap.close:
                snap.pct_change = (snap.close - prev) / prev * 100
        try:
            snap.trade_date = dt.datetime.strptime(str(row["trade_date"]), "%Y%m%d").date()
        except Exception:
            pass
        out.append(snap)
    return out


# ─── Tech news ─────────────────────────────────────────────────────────────

_TECH_KEYWORDS = [
    # AI / 模型
    "OpenAI", "Anthropic", "ChatGPT", "GPT", "Gemini", "Claude", "Llama",
    "大模型", "AI 应用", "AI模型", "智能体", "Agent", "AGI", "AI",
    "人工智能", "机器学习", "生成式",
    # 芯片 / 半导体
    "NVIDIA", "英伟达", "AMD", "Broadcom", "博通", "TSMC", "台积电", "Micron", "美光",
    "ASML", "光刻机", "GPU", "ASIC", "FPGA", "HBM", "半导体", "芯片", "晶圆",
    "存储芯片", "EDA", "封测", "国产替代", "出口管制", "半导体设备",
    "存储", "DRAM", "NAND",
    # 算力基建
    "光模块", "CPO", "PCB", "服务器", "数据中心", "IDC", "液冷",
    "算力", "云计算", "AI 服务器", "网络设备",
    # 应用 / 机器人
    "机器人", "Optimus", "智能驾驶", "FSD", "端侧 AI", "AR", "VR", "MR",
    "无人驾驶", "智能汽车", "智能座舱",
    # 能源 / 电力
    "数据中心电力", "AI 电力", "储能", "特高压", "电网", "电力设备",
    "新能源", "锂电", "光伏",
    # 通信
    "5G", "6G", "通信", "卫星互联网", "低空经济",
    # 政策
    "新质生产力", "人工智能+", "AI+", "科技自立",
    # 港股科技 (asset evening news shows these)
    "恒生科技", "互联网", "平台经济",
]


def fetch_tech_news(
    client: TuShareClient, *, end_bjt: dt.datetime,
    lookback_hours: int = 24, max_keep: int = 30,
) -> pd.DataFrame:
    end_local = end_bjt.replace(tzinfo=None)
    start_local = end_local - dt.timedelta(hours=lookback_hours)
    s = start_local.strftime("%Y-%m-%d %H:%M:%S")
    e = end_local.strftime("%Y-%m-%d %H:%M:%S")
    pat = "|".join(re.escape(k) for k in _TECH_KEYWORDS)

    sources = [
        ("major_news", "新华网"),
        ("major_news", "财联社"),
        ("major_news", "华尔街见闻"),
        ("major_news", "凤凰财经"),
        ("news", "cls"),
        ("news", "yicai"),
        ("news", "wallstreetcn"),
        ("news", "10jqka"),
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


# ─── User focus enrichment ────────────────────────────────────────────────

@dataclass
class FocusStockSnap:
    spec: FocusStock
    close: float | None
    pct_change: float | None
    turnover_rate: float | None
    pe: float | None
    pb: float | None
    moneyflow_net: float | None
    history_close: list[float | None] = field(default_factory=list)
    trade_date: dt.date | None = None
    data_status: str = "ok"


def enrich_focus(
    client: TuShareClient, *, on_date: dt.date,
    important: list[FocusStock], regular: list[FocusStock],
    history_days: int = 10,
) -> tuple[list[FocusStockSnap], list[FocusStockSnap]]:
    """Pull daily / daily_basic / moneyflow for focus stocks on `on_date`,
    plus N-day close history for sparklines."""
    end = on_date.strftime("%Y%m%d")
    start_history = (on_date - dt.timedelta(days=history_days * 2 + 5)).strftime("%Y%m%d")
    all_codes = list({s.ts_code for s in important + regular})
    if not all_codes:
        return [], []

    try:
        df_d = client.call("daily", trade_date=end)
    except Exception:
        df_d = pd.DataFrame()
    try:
        df_db = client.call("daily_basic", trade_date=end)
    except Exception:
        df_db = pd.DataFrame()
    try:
        df_mf = client.call("moneyflow", trade_date=end)
    except Exception:
        df_mf = pd.DataFrame()

    by_code_d: dict[str, Any] = {r.ts_code: r for r in df_d.itertuples()} if not df_d.empty else {}
    by_code_db: dict[str, Any] = {r.ts_code: r for r in df_db.itertuples()} if not df_db.empty else {}
    by_code_mf: dict[str, float | None] = (
        {r.ts_code: _f(r.net_mf_amount) for r in df_mf.itertuples()} if not df_mf.empty else {}
    )

    def _build(spec: FocusStock) -> FocusStockSnap:
        d = by_code_d.get(spec.ts_code)
        db = by_code_db.get(spec.ts_code)
        snap = FocusStockSnap(spec=spec,
                              close=_f(getattr(d, "close", None)) if d else None,
                              pct_change=_f(getattr(d, "pct_chg", None)) if d else None,
                              turnover_rate=_f(getattr(db, "turnover_rate", None)) if db else None,
                              pe=_f(getattr(db, "pe_ttm", None) or getattr(db, "pe", None)) if db else None,
                              pb=_f(getattr(db, "pb", None)) if db else None,
                              moneyflow_net=by_code_mf.get(spec.ts_code))
        try:
            hd = client.call("daily", ts_code=spec.ts_code,
                             start_date=start_history, end_date=end)
            if hd is not None and not hd.empty:
                hd = hd.sort_values("trade_date").tail(history_days)
                snap.history_close = [_f(v) for v in hd["close"]]
                snap.trade_date = _d(str(hd.iloc[-1]["trade_date"]))
        except Exception:
            pass
        if d is None and not snap.history_close:
            snap.data_status = "no_data"
        return snap

    imp_snaps = [_build(s) for s in important]
    reg_snaps = [_build(s) for s in regular]
    return imp_snaps, reg_snaps


# ─── A-share tech sector daily (SW L1, broad TMT reference) ───────────────

SW_TECH_INDEXES: dict[str, str] = {
    "801080.SI": "电子",
    "801750.SI": "计算机",
    "801770.SI": "通信",
    "801760.SI": "传媒",
    "801730.SI": "电力设备",
    "801710.SI": "建筑材料",
    "801950.SI": "煤炭",  # not tech but useful negative reference
}


@dataclass
class SectorBar:
    code: str
    name: str
    close: float | None
    pct_change: float | None
    trade_date: dt.date | None


def fetch_tech_sw_sectors(client: TuShareClient, *, on_date: dt.date,
                            slot: str = "morning", engine=None) -> list[SectorBar]:
    """SW L1 broad TMT reference indices.

    Note: tech has no noon report — slot="noon" is unreachable. Evening
    realtime path covers the 15:00→17:00 EOD-publish lag.

    Slot routing:
      today + (noon|evening) → MV-weighted realtime via member rt_k
                                 (market._sw_realtime).
      morning / historical → sw_daily EOD with staleness gate.
    """
    from ifa.core.report.timezones import BJT
    is_today = on_date == dt.datetime.now(BJT).date()
    if is_today and slot in ("noon", "evening") and engine is not None:
        from ifa.families.market._sw_realtime import compute_sw_realtime_snapshot
        codes = list(SW_TECH_INDEXES.keys())
        agg = compute_sw_realtime_snapshot(client, engine, on_date=on_date, sw_codes=codes, level="l1")
        out: list[SectorBar] = []
        for code, name in SW_TECH_INDEXES.items():
            v = agg.get(code, {})
            out.append(SectorBar(
                code=code, name=name,
                close=v.get("close"),
                pct_change=v.get("pct_change"),
                trade_date=v.get("trade_date"),
            ))
        return out

    end = on_date.strftime("%Y%m%d")
    start = (on_date - dt.timedelta(days=10)).strftime("%Y%m%d")
    out: list[SectorBar] = []
    for code, name in SW_TECH_INDEXES.items():
        try:
            df = client.call("sw_daily", ts_code=code, start_date=start, end_date=end)
        except Exception:
            out.append(SectorBar(code, name, None, None, None)); continue
        if df is None or df.empty:
            out.append(SectorBar(code, name, None, None, None)); continue
        df = df.sort_values("trade_date")
        row = df.iloc[-1]
        row_td = _d(str(row["trade_date"]))
        is_current = row_td == on_date
        out.append(SectorBar(
            code=code, name=name,
            close=_f(row.get("close")) if is_current else None,
            pct_change=_f(row.get("pct_change")) if is_current else None,  # sw_daily uses pct_change
            trade_date=row_td,
        ))
    return out


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
