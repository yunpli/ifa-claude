"""Asset report data layer.

Three responsibilities:
  1. Resolve a stable *main contract* per logical symbol (CU → CU2606.SHF) by
     volume rank on the latest available trade date. Walks back up to 10 days
     if the requested date has no rows.
  2. Pull a 10-day price/volume history per main contract for sparklines and
     trend judgement.
  3. Fetch commodity-relevant news from major_news / news with a keyword
     filter built from the universe display names + chain narratives.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ifa.core.tushare import TuShareClient

from .universe import CHINA_ASSET_UNIVERSE, AssetSpec, EXCHANGES_UNAVAILABLE


@dataclass
class CommoditySnapshot:
    spec: AssetSpec
    actual_contract: str | None = None
    close: float | None = None
    settle: float | None = None
    pre_close: float | None = None
    pre_settle: float | None = None
    pct_change: float | None = None
    open_: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    amount: float | None = None
    open_interest: float | None = None
    oi_change: float | None = None
    trade_date: dt.date | None = None
    history_dates: list[str] = field(default_factory=list)
    history_close: list[float | None] = field(default_factory=list)
    history_volume: list[float | None] = field(default_factory=list)
    data_status: str = "ok"   # 'ok' | 'czce_unavailable' | 'no_data'
    mapping_method: str = "highest_volume"


def resolve_main_contracts(
    client: TuShareClient, *, on_date: dt.date, max_lookback: int = 10
) -> tuple[dict[str, CommoditySnapshot], dt.date | None]:
    """Returns (snapshots_by_symbol, actual_trade_date_used)."""
    df: pd.DataFrame | None = None
    used_date: dt.date | None = None
    for back in range(0, max_lookback + 1):
        d = on_date - dt.timedelta(days=back)
        try:
            candidate = client.call("fut_daily", trade_date=d.strftime("%Y%m%d"))
        except Exception:
            continue
        if candidate is not None and not candidate.empty:
            df = candidate
            used_date = d
            break

    snapshots: dict[str, CommoditySnapshot] = {}
    for spec in CHINA_ASSET_UNIVERSE:
        snap = CommoditySnapshot(spec=spec)
        snapshots[spec.logical_symbol] = snap
        if spec.exchange in EXCHANGES_UNAVAILABLE:
            snap.data_status = "czce_unavailable"
            continue
        if df is None or df.empty:
            snap.data_status = "no_data"
            continue
        candidates = df[df["ts_code"].str.match(rf"^{re.escape(spec.logical_symbol)}\d", na=False)]
        if candidates.empty:
            snap.data_status = "no_data"
            continue
        candidates = candidates.sort_values("vol", ascending=False)
        row = candidates.iloc[0]
        snap.actual_contract = row["ts_code"]
        snap.close = _f(row.get("close"))
        snap.settle = _f(row.get("settle"))
        snap.pre_close = _f(row.get("pre_close"))
        snap.pre_settle = _f(row.get("pre_settle"))
        snap.open_ = _f(row.get("open"))
        snap.high = _f(row.get("high"))
        snap.low = _f(row.get("low"))
        snap.volume = _f(row.get("vol"))
        snap.amount = _f(row.get("amount"))
        snap.open_interest = _f(row.get("oi"))
        snap.oi_change = _f(row.get("oi_chg"))
        snap.trade_date = used_date
        if snap.close is not None and snap.pre_close not in (None, 0):
            snap.pct_change = (snap.close / snap.pre_close - 1) * 100
    return snapshots, used_date


def attach_histories(
    client: TuShareClient,
    snapshots: dict[str, CommoditySnapshot],
    *,
    end_date: dt.date,
    days: int = 10,
    sleep_between: float = 0.0,
) -> None:
    """For each contract, pull the last `days` daily bars via fut_daily(ts_code=...)."""
    end = end_date.strftime("%Y%m%d")
    start = (end_date - dt.timedelta(days=days * 2 + 5)).strftime("%Y%m%d")
    for snap in snapshots.values():
        if snap.actual_contract is None or snap.data_status != "ok":
            continue
        try:
            df = client.call("fut_daily", ts_code=snap.actual_contract,
                             start_date=start, end_date=end)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df.sort_values("trade_date").tail(days)
        snap.history_dates = df["trade_date"].astype(str).tolist()
        snap.history_close = [_f(v) for v in df["close"]]
        snap.history_volume = [_f(v) for v in df["vol"]]
        if sleep_between:
            time.sleep(sleep_between)


# ─── Category-level aggregates ─────────────────────────────────────────────

@dataclass
class CategoryStrength:
    category: str
    n_components: int
    n_with_data: int
    avg_pct_change: float | None
    up_share: float | None       # 0..1 fraction of components with pct_change > 0
    leader: str | None           # display name of top mover
    laggard: str | None
    leader_pct: float | None
    laggard_pct: float | None


def category_strengths(snapshots: dict[str, CommoditySnapshot]) -> list[CategoryStrength]:
    out: list[CategoryStrength] = []
    by_cat: dict[str, list[CommoditySnapshot]] = {}
    for s in snapshots.values():
        by_cat.setdefault(s.spec.category, []).append(s)
    for cat, items in by_cat.items():
        with_data = [i for i in items if i.pct_change is not None]
        if not with_data:
            out.append(CategoryStrength(category=cat, n_components=len(items),
                                        n_with_data=0, avg_pct_change=None,
                                        up_share=None, leader=None, laggard=None,
                                        leader_pct=None, laggard_pct=None))
            continue
        avg = sum(i.pct_change or 0 for i in with_data) / len(with_data)
        up = sum(1 for i in with_data if (i.pct_change or 0) > 0) / len(with_data)
        sorted_items = sorted(with_data, key=lambda i: i.pct_change or 0, reverse=True)
        out.append(CategoryStrength(
            category=cat, n_components=len(items), n_with_data=len(with_data),
            avg_pct_change=avg, up_share=up,
            leader=sorted_items[0].spec.display_name,
            leader_pct=sorted_items[0].pct_change,
            laggard=sorted_items[-1].spec.display_name,
            laggard_pct=sorted_items[-1].pct_change,
        ))
    # Sort by avg_pct_change desc (with None last)
    out.sort(key=lambda c: (c.avg_pct_change is None, -(c.avg_pct_change or 0)))
    return out


# ─── Anomaly detection ────────────────────────────────────────────────────

@dataclass
class AnomalyFlag:
    spec: AssetSpec
    snapshot: CommoditySnapshot
    flag_type: str    # 'large_move' | 'volume_surge' | 'oi_jump'
    detail: str


def detect_anomalies(snapshots: dict[str, CommoditySnapshot],
                     *, large_move_pct: float = 1.5,
                     volume_surge_ratio: float = 1.6,
                     oi_jump_pct: float = 2.0) -> list[AnomalyFlag]:
    flags: list[AnomalyFlag] = []
    for snap in snapshots.values():
        if snap.data_status != "ok" or snap.pct_change is None:
            continue
        if abs(snap.pct_change) >= large_move_pct:
            flags.append(AnomalyFlag(
                spec=snap.spec, snapshot=snap, flag_type="large_move",
                detail=f"{snap.pct_change:+.2f}% 异常波动（阈值 ±{large_move_pct}%）",
            ))
        if snap.history_volume and len(snap.history_volume) >= 5 and snap.volume:
            recent = [v for v in snap.history_volume[:-1] if v is not None]
            if recent:
                avg = sum(recent) / len(recent)
                if avg > 0 and snap.volume / avg >= volume_surge_ratio:
                    flags.append(AnomalyFlag(
                        spec=snap.spec, snapshot=snap, flag_type="volume_surge",
                        detail=f"成交 {snap.volume:,.0f}（近期均值 {avg:,.0f}，比值 {snap.volume/avg:.2f}×）",
                    ))
        if snap.oi_change is not None and snap.open_interest:
            ratio = snap.oi_change / snap.open_interest * 100 if snap.open_interest else 0
            if abs(ratio) >= oi_jump_pct:
                flags.append(AnomalyFlag(
                    spec=snap.spec, snapshot=snap, flag_type="oi_jump",
                    detail=f"持仓变化 {snap.oi_change:+,.0f}（对应 {ratio:+.2f}%）",
                ))
    return flags


# ─── Commodity-relevant news ──────────────────────────────────────────────

# Keyword union derived from universe + chain context
_COMMODITY_KEYWORDS: list[str] = [
    "原油", "OPEC", "WTI", "布伦特", "燃油", "汽油", "柴油",
    "黄金", "白银",
    "铜", "铝", "锌", "镍", "锡", "铅", "铜价", "铝价", "镍价",
    "铁矿", "螺纹", "热卷", "焦煤", "焦炭", "钢铁", "钢厂",
    "PTA", "甲醇", "塑料", "PP", "PVC", "纯碱", "玻璃", "橡胶", "沥青",
    "豆粕", "豆油", "玉米", "棉花", "白糖", "苹果", "菜粕", "菜油",
    "OPEC+", "煤炭", "天然气", "硫酸", "锂",
    "限产", "增产", "罢工", "减产", "出口管制", "矿山",
]


def fetch_commodity_news(client: TuShareClient, *, end_bjt: dt.datetime,
                         lookback_hours: int = 36, max_keep: int = 30) -> pd.DataFrame:
    """Fetch from major_news + news for the past lookback_hours and keep rows
    that mention at least one commodity keyword."""
    end_local = end_bjt.replace(tzinfo=None)
    start_local = end_local - dt.timedelta(hours=lookback_hours)
    s = start_local.strftime("%Y-%m-%d %H:%M:%S")
    e = end_local.strftime("%Y-%m-%d %H:%M:%S")

    keep: list[pd.DataFrame] = []
    sources = [
        ("major_news", "新华网"),
        ("major_news", "财联社"),
        ("major_news", "华尔街见闻"),
        ("major_news", "凤凰财经"),
        ("news", "cls"),
        ("news", "yicai"),
        ("news", "wallstreetcn"),
    ]
    pat = "|".join(re.escape(k) for k in _COMMODITY_KEYWORDS)
    for api, src in sources:
        try:
            df = client.call(api, src=src, start_date=s, end_date=e)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        col_t = df["title"].fillna("").astype(str) if "title" in df.columns else pd.Series([""] * len(df))
        col_c = df["content"].fillna("").astype(str) if "content" in df.columns else pd.Series([""] * len(df))
        blob = col_t + " " + col_c
        mask = blob.str.contains(pat, regex=True, na=False)
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


# ─── A-share sector daily snapshot for evening transmission analysis ──────

# Map common SW (申万) industry index codes to canonical sector display names.
# These are the sectors most directly driven by commodity moves.
SW_SECTOR_INDEXES: dict[str, str] = {
    "801010.SI": "农林牧渔",
    "801030.SI": "基础化工",
    "801040.SI": "钢铁",
    "801050.SI": "有色金属",
    "801080.SI": "电子",
    "801110.SI": "家用电器",
    "801120.SI": "食品饮料",
    "801130.SI": "纺织服饰",
    "801140.SI": "轻工制造",
    "801160.SI": "公用事业",
    "801170.SI": "交通运输",
    "801180.SI": "房地产",
    "801710.SI": "建筑材料",
    "801720.SI": "建筑装饰",
    "801730.SI": "电力设备",
    "801740.SI": "国防军工",
    "801950.SI": "煤炭",
    "801960.SI": "石油石化",
}


@dataclass
class SectorBar:
    code: str
    name: str
    close: float | None
    pct_change: float | None
    trade_date: dt.date | None


def fetch_a_share_sectors(client: TuShareClient, *, on_date: dt.date,
                           sleep_between: float = 0.0) -> list[SectorBar]:
    end = on_date.strftime("%Y%m%d")
    start = (on_date - dt.timedelta(days=10)).strftime("%Y%m%d")
    out: list[SectorBar] = []
    for code, name in SW_SECTOR_INDEXES.items():
        try:
            df = client.call("index_daily", ts_code=code, start_date=start, end_date=end)
        except Exception:
            out.append(SectorBar(code, name, None, None, None))
            continue
        if df is None or df.empty:
            out.append(SectorBar(code, name, None, None, None))
            continue
        df = df.sort_values("trade_date")
        row = df.iloc[-1]
        out.append(SectorBar(
            code=code, name=name,
            close=_f(row["close"]),
            pct_change=_f(row.get("pct_chg")),
            trade_date=dt.datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(),
        ))
        if sleep_between:
            time.sleep(sleep_between)
    return out


def _f(v: Any) -> float | None:
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
