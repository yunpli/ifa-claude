"""板块资金流因子计算 → factor_daily (P0 rule-based).

4 explainable factors (per sector × date):
  heat_score        资金热度  — how much capital is flowing in today
  trend_score       趋势确认  — is the sector in a consistent price uptrend
  persistence_score 资金持续  — has capital flowed in for multiple consecutive days
  crowding_score    拥挤风险  — is the sector overbought / capital trapped

Input raw tables:
  smartmoney.raw_moneyflow_ind_dc  — DC 板块级资金流（主力）
  smartmoney.raw_sw_daily          — SW 行业日线（收盘、成交、涨幅）
  smartmoney.raw_moneyflow_ind_ths — THS 板块资金流（net_amount）
  smartmoney.raw_kpl_concept       — KPL 今日热概念（z_t_num）

Output:
  factor_daily rows; upserted via write_factor_daily().

Design notes:
  - All factors are cross-sectionally normalized to [0, 1] within each source
    and date so they can be compared across sectors.
  - When historical data is missing (early backfill dates), factors fall back
    to NaN rather than crashing.
  - M1-safe: we load only n_history_days of data per query, never the full table.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

import math

from .common import (
    consecutive_positive,
    cross_sectional_rank,
    minmax_normalize,
    percentile_rank,
    positive_ratio,
    rolling_mean,
    winsorize,
)

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"


# ── Output row ────────────────────────────────────────────────────────────────

@dataclass
class FactorRow:
    trade_date: dt.date
    sector_code: str
    sector_source: str           # sw / dc / ths / kpl
    sector_name: str | None
    heat_score: float | None
    trend_score: float | None
    persistence_score: float | None
    crowding_score: float | None
    derived: dict[str, Any] = field(default_factory=dict)


# ── DC moneyflow factors (most data-rich source) ──────────────────────────────

def _load_dc_history(
    engine: Engine,
    trade_date: dt.date,
    n_days: int,
) -> pd.DataFrame:
    """Load last n_days of raw_moneyflow_ind_dc for ALL sectors.

    Returns a DataFrame with columns:
      trade_date, ts_code, name, content_type, pct_change,
      net_amount, net_amount_rate, buy_elg_amount, buy_elg_amount_rate
    """
    sql = f"""
        SELECT
            trade_date, ts_code, name, content_type,
            pct_change, net_amount, net_amount_rate,
            buy_elg_amount, buy_elg_amount_rate, rank
        FROM {SCHEMA}.raw_moneyflow_ind_dc
        WHERE trade_date <= :d
          AND trade_date >= :start
        ORDER BY trade_date ASC, ts_code
    """
    start = trade_date - dt.timedelta(days=n_days * 2)  # calendar days ≈ 2× trading days
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "start": start}).fetchall()
    _COLS = ["trade_date", "ts_code", "name", "content_type",
             "pct_change", "net_amount", "net_amount_rate",
             "buy_elg_amount", "buy_elg_amount_rate", "rank"]
    if not rows:
        return pd.DataFrame(columns=_COLS)
    df = pd.DataFrame(rows, columns=_COLS)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for col in ["pct_change", "net_amount", "net_amount_rate", "buy_elg_amount", "buy_elg_amount_rate"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _compute_dc_factors(
    df: pd.DataFrame,
    trade_date: dt.date,
    params: dict[str, Any],
) -> list[FactorRow]:
    """Compute 4 factors for DC sectors using moneyflow data.

    Only 概念 and 行业 sectors (not 地域) are processed.
    """
    fp = params.get("factors", {})
    short_win = int(fp.get("short_window", 10))
    persist_win = int(fp.get("persistence_window", 5))
    consist_win = int(fp.get("trend", {}).get("consistency_window", 5))

    heat_w_net = float(fp.get("heat", {}).get("net_amount_weight", 0.50))
    heat_w_elg = float(fp.get("heat", {}).get("elg_rate_weight", 0.30))
    heat_w_amt = float(fp.get("heat", {}).get("amount_vs_avg_weight", 0.20))
    trend_w_pct = float(fp.get("trend", {}).get("pct_change_weight", 0.60))
    trend_w_con = float(fp.get("trend", {}).get("consistency_weight", 0.40))

    # Exclude region sectors (地域 adds noise, no cycle semantics)
    df = df[df["content_type"].isin(["概念", "行业"])].copy()
    if df.empty:
        return []

    today_df = df[df["trade_date"] == trade_date].copy()
    if today_df.empty:
        return []

    hist_df = df[df["trade_date"] < trade_date]

    # ── Cross-sectional normalization for today ───────────────────────────────
    # heat component 1: net_amount cross-section rank
    today_df["net_amount_rank"] = cross_sectional_rank(today_df["net_amount"].fillna(0))
    # heat component 2: buy_elg_amount_rate (超大单占比)
    today_df["elg_rate_norm"] = minmax_normalize(today_df["buy_elg_amount_rate"].fillna(0))
    # trend component 1: pct_change rank
    today_df["pct_rank"] = cross_sectional_rank(today_df["pct_change"].fillna(0))

    rows: list[FactorRow] = []
    for _, row in today_df.iterrows():
        code = row["ts_code"]
        name = row.get("name")

        # Historical series for this sector
        hist = hist_df[hist_df["ts_code"] == code].sort_values("trade_date")
        net_hist = hist["net_amount"].tolist()

        # ── heat_score ───────────────────────────────────────────────────────
        net_avg = rolling_mean(net_hist, short_win) if net_hist else float("nan")
        if net_avg and abs(net_avg) > 1e-6:
            amount_vs_avg = (float(row["net_amount"] or 0) - net_avg) / abs(net_avg)
            amount_vs_avg_norm = min(max((amount_vs_avg + 2) / 4, 0), 1)  # map [-2,+2] → [0,1]
        else:
            amount_vs_avg_norm = 0.5

        heat_score = (
            float(row["net_amount_rank"]) * heat_w_net
            + float(row["elg_rate_norm"]) * heat_w_elg
            + amount_vs_avg_norm * heat_w_amt
        )

        # ── trend_score ───────────────────────────────────────────────────────
        pct_hist = hist["pct_change"].tolist()
        consistency = positive_ratio(pct_hist, consist_win) if pct_hist else 0.5

        trend_score = (
            float(row["pct_rank"]) * trend_w_pct
            + (consistency if not (isinstance(consistency, float) and np.isnan(consistency)) else 0.5) * trend_w_con
        )

        # ── persistence_score ─────────────────────────────────────────────────
        persist_ratio = positive_ratio(net_hist, persist_win)
        if isinstance(persist_ratio, float) and np.isnan(persist_ratio):
            persistence_score = None
        else:
            persistence_score = float(persist_ratio)

        # ── crowding_score ────────────────────────────────────────────────────
        # High net_amount_rank + low pct_rank → crowded (money in but price not moving)
        # Also: if net_amount has been positive for many days + pct not moving = crowded
        pct_rank_val = float(row["pct_rank"])
        crowding_score = float(row["net_amount_rank"]) * (1.0 - pct_rank_val)

        derived = {
            "net_amount": round(float(row["net_amount"] or 0), 2),
            "net_amount_rate": round(float(row["net_amount_rate"] or 0), 4),
            "buy_elg_amount_rate": round(float(row["buy_elg_amount_rate"] or 0), 4),
            "pct_change": round(float(row["pct_change"] or 0), 4),
            "dc_rank": int(row["rank"]) if row["rank"] == row["rank"] and row["rank"] is not None else 0,
            "consec_positive_days": consecutive_positive(net_hist),
            "content_type": row["content_type"],
        }

        rows.append(FactorRow(
            trade_date=trade_date,
            sector_code=code,
            sector_source="dc",
            sector_name=name,
            heat_score=round(heat_score, 4),
            trend_score=round(trend_score, 4),
            persistence_score=round(persistence_score, 4) if persistence_score is not None else None,
            crowding_score=round(crowding_score, 4),
            derived=derived,
        ))

    return rows


# ── SW daily factors (行业骨架 — no direct net_amount; use price + volume) ──────

def _load_sw_history(
    engine: Engine,
    trade_date: dt.date,
    n_days: int,
) -> pd.DataFrame:
    sql = f"""
        SELECT trade_date, ts_code, name, pct_change, amount, vol
        FROM {SCHEMA}.raw_sw_daily
        WHERE trade_date <= :d
          AND trade_date >= :start
        ORDER BY trade_date ASC, ts_code
    """
    start = trade_date - dt.timedelta(days=n_days * 2)
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "start": start}).fetchall()
    _COLS = ["trade_date", "ts_code", "name", "pct_change", "amount", "vol"]
    if not rows:
        return pd.DataFrame(columns=_COLS)
    df = pd.DataFrame(rows, columns=_COLS)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for col in ["pct_change", "amount", "vol"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _compute_sw_factors(
    df: pd.DataFrame,
    trade_date: dt.date,
    params: dict[str, Any],
) -> list[FactorRow]:
    """Compute simplified 4 factors for SW L1 sectors.

    SW lacks direct net_amount data; we proxy:
      heat_score        ← amount cross-section rank (vs historical avg)
      trend_score       ← pct_change rank + rolling pct consistency
      persistence_score ← rolling amount-above-avg streak
      crowding_score    ← high amount + poor pct (price not following)
    """
    fp = params.get("factors", {})
    short_win = int(fp.get("short_window", 10))
    persist_win = int(fp.get("persistence_window", 5))
    consist_win = int(fp.get("trend", {}).get("consistency_window", 5))

    today_df = df[df["trade_date"] == trade_date].copy()
    if today_df.empty:
        return []
    hist_df = df[df["trade_date"] < trade_date]

    today_df["amount_rank"] = cross_sectional_rank(today_df["amount"].fillna(0))
    today_df["pct_rank"] = cross_sectional_rank(today_df["pct_change"].fillna(0))

    rows: list[FactorRow] = []
    for _, row in today_df.iterrows():
        code = row["ts_code"]
        name = row.get("name")
        hist = hist_df[hist_df["ts_code"] == code].sort_values("trade_date")
        amount_hist = hist["amount"].tolist()
        pct_hist = hist["pct_change"].tolist()

        # heat: amount vs its own 10d mean
        avg_amount = rolling_mean(amount_hist, short_win)
        today_amount = float(row["amount"] or 0)
        if avg_amount and avg_amount > 1e-6:
            amt_ratio_norm = min(max((today_amount / avg_amount - 0.5) / 1.5, 0), 1)
        else:
            amt_ratio_norm = 0.5

        heat_score = float(row["amount_rank"]) * 0.6 + amt_ratio_norm * 0.4

        # trend
        consistency = positive_ratio(pct_hist, consist_win)
        if isinstance(consistency, float) and np.isnan(consistency):
            consistency = 0.5
        trend_score = float(row["pct_rank"]) * 0.7 + consistency * 0.3

        # persistence: amount above its rolling mean for N days
        if len(amount_hist) >= persist_win:
            recent_amts = amount_hist[-persist_win:]
            hist_avg = rolling_mean(amount_hist[:-persist_win], short_win) if len(amount_hist) > persist_win else rolling_mean(amount_hist, short_win)
            pos_days = sum(1 for a in recent_amts if a is not None and a > (hist_avg or 0))
            persistence_score = pos_days / persist_win
        else:
            persistence_score = None

        # crowding: high amount + low pct → trapped
        crowding_score = float(row["amount_rank"]) * (1.0 - float(row["pct_rank"]))

        derived = {
            "amount": round(today_amount, 2),
            "amount_rank_pct": round(float(row["amount_rank"]), 4),
            "pct_change": round(float(row["pct_change"] or 0), 4),
            "rolling_pct_consistency": round(consistency, 4),
        }

        rows.append(FactorRow(
            trade_date=trade_date,
            sector_code=code,
            sector_source="sw",
            sector_name=name,
            heat_score=round(heat_score, 4),
            trend_score=round(trend_score, 4),
            persistence_score=round(persistence_score, 4) if persistence_score is not None else None,
            crowding_score=round(crowding_score, 4),
            derived=derived,
        ))
    return rows


# ── THS moneyflow factors ─────────────────────────────────────────────────────

def _load_ths_history(
    engine: Engine,
    trade_date: dt.date,
    n_days: int,
) -> pd.DataFrame:
    sql = f"""
        SELECT trade_date, ts_code, industry, pct_change, net_amount
        FROM {SCHEMA}.raw_moneyflow_ind_ths
        WHERE trade_date <= :d AND trade_date >= :start
        ORDER BY trade_date ASC, ts_code
    """
    start = trade_date - dt.timedelta(days=n_days * 2)
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "start": start}).fetchall()
    _COLS = ["trade_date", "ts_code", "industry", "pct_change", "net_amount"]
    if not rows:
        return pd.DataFrame(columns=_COLS)
    df = pd.DataFrame(rows, columns=_COLS)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for col in ["pct_change", "net_amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _compute_ths_factors(
    df: pd.DataFrame,
    trade_date: dt.date,
    params: dict[str, Any],
) -> list[FactorRow]:
    """Simplified DC-style factors for THS sectors (cross-check source)."""
    fp = params.get("factors", {})
    persist_win = int(fp.get("persistence_window", 5))
    consist_win = int(fp.get("trend", {}).get("consistency_window", 5))

    today_df = df[df["trade_date"] == trade_date].copy()
    if today_df.empty:
        return []
    hist_df = df[df["trade_date"] < trade_date]

    today_df["net_rank"] = cross_sectional_rank(today_df["net_amount"].fillna(0))
    today_df["pct_rank"] = cross_sectional_rank(today_df["pct_change"].fillna(0))

    rows: list[FactorRow] = []
    for _, row in today_df.iterrows():
        code = row["ts_code"]
        hist = hist_df[hist_df["ts_code"] == code].sort_values("trade_date")
        net_hist = hist["net_amount"].tolist()
        pct_hist = hist["pct_change"].tolist()

        consistency = positive_ratio(pct_hist, consist_win)
        if isinstance(consistency, float) and np.isnan(consistency):
            consistency = 0.5
        persist_ratio = positive_ratio(net_hist, persist_win)

        heat_score = float(row["net_rank"])
        trend_score = float(row["pct_rank"]) * 0.7 + consistency * 0.3
        persistence_score = float(persist_ratio) if not (isinstance(persist_ratio, float) and np.isnan(persist_ratio)) else None
        crowding_score = float(row["net_rank"]) * (1 - float(row["pct_rank"]))

        rows.append(FactorRow(
            trade_date=trade_date,
            sector_code=code,
            sector_source="ths",
            sector_name=row.get("industry"),
            heat_score=round(heat_score, 4),
            trend_score=round(trend_score, 4),
            persistence_score=round(persistence_score, 4) if persistence_score is not None else None,
            crowding_score=round(crowding_score, 4),
            derived={
                "net_amount": round(float(row["net_amount"] or 0), 2),
                "pct_change": round(float(row["pct_change"] or 0), 4),
            },
        ))
    return rows


# ── KPL concept factors (short-term mainline signals only) ────────────────────

def _load_kpl_history(
    engine: Engine,
    trade_date: dt.date,
    n_days: int,
) -> pd.DataFrame:
    sql = f"""
        SELECT trade_date, ts_code, name, z_t_num, up_num
        FROM {SCHEMA}.raw_kpl_concept
        WHERE trade_date <= :d AND trade_date >= :start
        ORDER BY trade_date ASC, ts_code
    """
    start = trade_date - dt.timedelta(days=n_days * 2)
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "start": start}).fetchall()
    _COLS = ["trade_date", "ts_code", "name", "z_t_num", "up_num"]
    if not rows:
        return pd.DataFrame(columns=_COLS)
    df = pd.DataFrame(rows, columns=_COLS)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for col in ["z_t_num", "up_num"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def _compute_kpl_factors(
    df: pd.DataFrame,
    trade_date: dt.date,
    params: dict[str, Any],
) -> list[FactorRow]:
    """KPL factors based on limit-up count (z_t_num) per concept.

    KPL has no price series or net_amount; factors are simpler:
      heat_score       = z_t_num cross-section rank
      trend_score      = up_num / (z_t_num + up_num) if available, else z_t_rank
      persistence_score = z_t_num has been >= min for N days
      crowding_score   = high z_t_num with declining day-over-day (概念热度顶点)
    """
    kpl_params = params.get("kpl", {})
    z_t_min = int(kpl_params.get("z_t_min", 2))
    persist_win = int(params.get("factors", {}).get("persistence_window", 5))

    today_df = df[df["trade_date"] == trade_date].copy()
    today_df = today_df[today_df["z_t_num"] >= z_t_min]
    if today_df.empty:
        return []
    hist_df = df[df["trade_date"] < trade_date]

    today_df["zt_rank"] = cross_sectional_rank(today_df["z_t_num"])

    rows: list[FactorRow] = []
    for _, row in today_df.iterrows():
        code = row["ts_code"]
        hist = hist_df[hist_df["ts_code"] == code].sort_values("trade_date")
        zt_hist = hist["z_t_num"].tolist()

        heat_score = float(row["zt_rank"])

        total_active = float(row["z_t_num"]) + float(row["up_num"])
        trend_score = float(row["z_t_num"]) / total_active if total_active > 0 else 0.5

        # persistence: concept appeared with z_t_num >= z_t_min in recent N days
        if len(zt_hist) >= persist_win:
            hot_days = sum(1 for v in zt_hist[-persist_win:] if v >= z_t_min)
            persistence_score = hot_days / persist_win
        else:
            persistence_score = None

        # crowding: high rank today but declining vs yesterday
        prev_zt = float(zt_hist[-1]) if zt_hist else float(row["z_t_num"])
        today_zt = float(row["z_t_num"])
        declining = today_zt < prev_zt
        crowding_score = float(row["zt_rank"]) * (0.8 if declining else 0.2)

        rows.append(FactorRow(
            trade_date=trade_date,
            sector_code=code,
            sector_source="kpl",
            sector_name=row.get("name"),
            heat_score=round(heat_score, 4),
            trend_score=round(trend_score, 4),
            persistence_score=round(persistence_score, 4) if persistence_score is not None else None,
            crowding_score=round(crowding_score, 4),
            derived={
                "z_t_num": int(row["z_t_num"]),
                "up_num": int(row["up_num"]),
                "declining": declining,
            },
        ))
    return rows


# ── SW L2 moneyflow factors (actual net_amount from sector_moneyflow_sw_daily) ──

def _load_sw_l2_history(
    engine: Engine,
    trade_date: dt.date,
    n_days: int,
) -> pd.DataFrame:
    """Load sector_moneyflow_sw_daily + L1 pct_change proxy for n_days window.

    Returns columns: trade_date, ts_code (=l2_code), name (=l2_name),
    net_amount, buy_elg_amount, sell_elg_amount, stock_count,
    pct_change (L1 proxy via raw_sw_daily), elg_net, buy_elg_rate.
    """
    sql = f"""
        SELECT
            sf.trade_date,
            sf.l2_code     AS ts_code,
            sf.l2_name     AS name,
            sf.net_amount,
            sf.buy_elg_amount,
            sf.sell_elg_amount,
            sf.stock_count,
            sw.pct_change
        FROM {SCHEMA}.sector_moneyflow_sw_daily sf
        LEFT JOIN {SCHEMA}.raw_sw_daily sw
               ON sw.ts_code = sf.l1_code
              AND sw.trade_date = sf.trade_date
        WHERE sf.trade_date <= :d
          AND sf.trade_date >= :start
        ORDER BY sf.trade_date ASC, sf.l2_code
    """
    start = trade_date - dt.timedelta(days=n_days * 2)
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "start": start}).fetchall()
    _COLS = ["trade_date", "ts_code", "name",
             "net_amount", "buy_elg_amount", "sell_elg_amount",
             "stock_count", "pct_change"]
    if not rows:
        return pd.DataFrame(columns=_COLS)
    df = pd.DataFrame(rows, columns=_COLS)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for col in ["net_amount", "buy_elg_amount", "sell_elg_amount", "stock_count", "pct_change"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["elg_net"] = df["buy_elg_amount"].fillna(0) - df["sell_elg_amount"].fillna(0)
    total_elg = df["buy_elg_amount"].fillna(0) + df["sell_elg_amount"].fillna(0)
    df["buy_elg_rate"] = (df["buy_elg_amount"].fillna(0) / total_elg.replace(0, np.nan)).fillna(0.5)
    return df


def _compute_sw_l2_factors(
    df: pd.DataFrame,
    trade_date: dt.date,
    params: dict[str, Any],
) -> list[FactorRow]:
    """Compute 4 factors for SW L2 sectors from sector_moneyflow_sw_daily.

    Uses actual net_amount data (unlike the existing 'sw' source which uses
    price/volume proxies from raw_sw_daily).  pct_change is L1-level proxy.
    """
    if df.empty:
        return []

    fp = params.get("factors", {})
    short_win = int(fp.get("short_window", 10))
    persist_win = int(fp.get("persistence_window", 5))
    consist_win = int(fp.get("trend", {}).get("consistency_window", 5))

    today_df = df[df["trade_date"] == trade_date].copy()
    if today_df.empty:
        return []
    hist_df = df[df["trade_date"] < trade_date]

    today_df["net_amount_rank"] = cross_sectional_rank(today_df["net_amount"].fillna(0))
    today_df["elg_rate_norm"] = minmax_normalize(today_df["buy_elg_rate"].fillna(0))
    today_df["pct_rank"] = cross_sectional_rank(today_df["pct_change"].fillna(0))

    rows: list[FactorRow] = []
    for _, row in today_df.iterrows():
        code = row["ts_code"]
        name = row.get("name")

        hist = hist_df[hist_df["ts_code"] == code].sort_values("trade_date")
        net_hist = hist["net_amount"].tolist()
        pct_hist = hist["pct_change"].tolist()

        # ── heat_score ───────────────────────────────────────────────────────
        net_avg = rolling_mean(net_hist, short_win) if net_hist else float("nan")
        if net_avg and abs(net_avg) > 1e-6:
            amount_vs_avg = (float(row["net_amount"] or 0) - net_avg) / abs(net_avg)
            amount_vs_avg_norm = min(max((amount_vs_avg + 2) / 4, 0), 1)
        else:
            amount_vs_avg_norm = 0.5

        heat_score = (
            float(row["net_amount_rank"]) * 0.55
            + float(row["elg_rate_norm"]) * 0.30
            + amount_vs_avg_norm * 0.15
        )

        # ── trend_score ───────────────────────────────────────────────────────
        pct_consistency = positive_ratio(pct_hist, consist_win) if pct_hist else 0.5
        if isinstance(pct_consistency, float) and np.isnan(pct_consistency):
            pct_consistency = 0.5
        net_consistency = positive_ratio(net_hist, consist_win) if net_hist else 0.5
        if isinstance(net_consistency, float) and np.isnan(net_consistency):
            net_consistency = 0.5

        trend_score = (
            float(row["pct_rank"]) * 0.45
            + pct_consistency * 0.30
            + net_consistency * 0.25
        )

        # ── persistence_score ─────────────────────────────────────────────────
        persist_ratio = positive_ratio(net_hist, persist_win)
        if isinstance(persist_ratio, float) and np.isnan(persist_ratio):
            persistence_score = None
        else:
            persistence_score = float(persist_ratio)

        # ── crowding_score ────────────────────────────────────────────────────
        crowding_score = float(row["net_amount_rank"]) * (1.0 - float(row["pct_rank"]))

        derived = {
            "net_amount": round(float(row["net_amount"] or 0), 2),
            "buy_elg_rate": round(float(row["buy_elg_rate"] or 0), 4),
            "elg_net": round(float(row["elg_net"] or 0), 2),
            "pct_change": round(float(row["pct_change"] or 0), 4) if row["pct_change"] == row["pct_change"] else None,
            "stock_count": int(row["stock_count"] or 0),
            "consec_positive_days": consecutive_positive(net_hist),
        }

        rows.append(FactorRow(
            trade_date=trade_date,
            sector_code=code,
            sector_source="sw_l2",
            sector_name=name,
            heat_score=round(heat_score, 4),
            trend_score=round(trend_score, 4),
            persistence_score=round(persistence_score, 4) if persistence_score is not None else None,
            crowding_score=round(crowding_score, 4),
            derived=derived,
        ))
    return rows


# ── DB write ──────────────────────────────────────────────────────────────────

def write_factor_daily(engine: Engine, rows: list[FactorRow]) -> int:
    """Batch-upsert FactorRow objects into smartmoney.factor_daily.

    Returns number of rows written.
    """
    if not rows:
        return 0

    sql = text(f"""
        INSERT INTO {SCHEMA}.factor_daily (
            trade_date, sector_code, sector_source, sector_name,
            heat_score, trend_score, persistence_score, crowding_score,
            derived_json, computed_at
        ) VALUES (
            :trade_date, :sector_code, :sector_source, :sector_name,
            :heat_score, :trend_score, :persistence_score, :crowding_score,
            :derived_json, now()
        )
        ON CONFLICT (trade_date, sector_code, sector_source) DO UPDATE SET
            sector_name        = EXCLUDED.sector_name,
            heat_score         = EXCLUDED.heat_score,
            trend_score        = EXCLUDED.trend_score,
            persistence_score  = EXCLUDED.persistence_score,
            crowding_score     = EXCLUDED.crowding_score,
            derived_json       = EXCLUDED.derived_json,
            computed_at        = now()
    """)

    def _clean(v: object) -> object:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        if isinstance(v, dict):
            return {k: _clean(w) for k, w in v.items()}
        return v

    params_list = [
        {
            "trade_date": r.trade_date,
            "sector_code": r.sector_code,
            "sector_source": r.sector_source,
            "sector_name": r.sector_name,
            "heat_score": r.heat_score,
            "trend_score": r.trend_score,
            "persistence_score": r.persistence_score,
            "crowding_score": r.crowding_score,
            "derived_json": json.dumps(_clean(r.derived), ensure_ascii=False),
        }
        for r in rows
    ]
    with engine.begin() as conn:
        conn.execute(sql, params_list)
    return len(rows)


# ── Orchestrator ──────────────────────────────────────────────────────────────

def compute_factors_for_date(
    engine: Engine,
    trade_date: dt.date,
    *,
    params: dict[str, Any],
    sources: list[str] | None = None,
) -> dict[str, int]:
    """Compute and persist factor_daily rows for all sector sources.

    Args:
        engine:     SQLAlchemy engine (correct DB for run mode).
        trade_date: The trading date to compute factors for.
        params:     Full params dict (from params/store.py or default.yaml).
        sources:    Subset of ['dc', 'sw', 'ths', 'kpl']; None = all.

    Returns:
        dict mapping source → number of rows written.
    """
    if sources is None:
        sources = ["dc", "sw", "ths", "kpl", "sw_l2"]

    history_days = int(params.get("factors", {}).get("history_days", 60))
    written: dict[str, int] = {}

    if "dc" in sources:
        df = _load_dc_history(engine, trade_date, history_days)
        rows = _compute_dc_factors(df, trade_date, params)
        n = write_factor_daily(engine, rows)
        written["dc"] = n
        log.info("[flow] dc factors: %d rows for %s", n, trade_date)

    if "sw" in sources:
        df = _load_sw_history(engine, trade_date, history_days)
        rows = _compute_sw_factors(df, trade_date, params)
        n = write_factor_daily(engine, rows)
        written["sw"] = n
        log.info("[flow] sw factors: %d rows for %s", n, trade_date)

    if "ths" in sources:
        df = _load_ths_history(engine, trade_date, history_days)
        rows = _compute_ths_factors(df, trade_date, params)
        n = write_factor_daily(engine, rows)
        written["ths"] = n
        log.info("[flow] ths factors: %d rows for %s", n, trade_date)

    if "kpl" in sources:
        df = _load_kpl_history(engine, trade_date, history_days)
        rows = _compute_kpl_factors(df, trade_date, params)
        n = write_factor_daily(engine, rows)
        written["kpl"] = n
        log.info("[flow] kpl factors: %d rows for %s", n, trade_date)

    if "sw_l2" in sources:
        df = _load_sw_l2_history(engine, trade_date, history_days)
        rows = _compute_sw_l2_factors(df, trade_date, params)
        n = write_factor_daily(engine, rows)
        written["sw_l2"] = n
        log.info("[flow] sw_l2 factors: %d rows for %s", n, trade_date)

    return written
