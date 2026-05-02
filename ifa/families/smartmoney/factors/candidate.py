"""候选股池筛选 → stock_signals_daily (role ∈ {补涨, 趋势}).

Goal: identify stocks that *haven't* fully moved yet but are positioned to
benefit from the active narrative.

Two complementary roles:

  补涨 (follower/filler) — members of active sectors that haven't rallied
                          yet today; theme aligned with the hot story.
                          Heuristic: "tomorrow's possible 龙头 candidate."

  趋势 (trending)        — multi-day persistent uptrend with healthy volume,
                          irrespective of today's catalyst. Heuristic:
                          "stock is already trending; ride it."

Filtering rules (P0 conservative):
  - Stock must be a member of at least one *active* sector
    (sector role ∈ {主线, 中军, 轮动, 催化}).
  - Must not already be tagged as 龙头/中军/情绪先锋 by leader.py (those have
    already moved; we want the next layer down).
  - Liquidity gate: amount >= candidate.min_amount (default 5e7 = 5000万).
  - Market cap gate: circ_mv >= candidate.min_circ_mv (default 2e9 = 20亿).

补涨 score components (today only):
  - room_score   ── 1 − (pct_chg / 9.9), clipped to [0, 1]: how much room left
  - elg_score    ── elg_net cross-section rank within sector
  - amount_score ── amount cross-section rank within sector
  - theme_match  ── bonus if stock's kpl_list.theme intersects the sector name
  - penalty      ── pct_chg < −2% strong penalty (broken stock)

趋势 score components (rolling 5d window):
  - up_days       ── days with positive pct_chg in last 5 trading days
  - flow_consistency ── days with positive elg_net in last 5
  - vol_above_avg ── today_amount / 5d_avg_amount

A stock can appear in both 补涨 and 趋势 (different (ts_code, role) PK
combinations).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .leader import StockSignal, _load_active_sectors, _load_dc_members, _load_kpl_members, _load_sw_members

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"


# ── Data helpers ──────────────────────────────────────────────────────────────

def _load_already_tagged(
    engine: Engine,
    trade_date: dt.date,
) -> set[str]:
    """ts_codes already classified by leader.py (龙头/中军/情绪先锋)."""
    sql = f"""
        SELECT DISTINCT ts_code
        FROM {SCHEMA}.stock_signals_daily
        WHERE trade_date = :d
          AND role IN ('龙头', '中军', '情绪先锋')
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date}).fetchall()
    return {r[0] for r in rows}


def _load_member_stock_data(
    engine: Engine,
    trade_date: dt.date,
    ts_codes: list[str],
) -> pd.DataFrame:
    """Today's price/flow/turnover/cap for candidate stocks."""
    _COLS = ["ts_code", "pct_chg", "amount", "close", "turnover_rate", "circ_mv",
             "total_mv", "buy_elg_amount", "sell_elg_amount", "net_mf_amount", "limit_state"]
    if not ts_codes:
        return pd.DataFrame(columns=_COLS + ["elg_net"])
    sql = f"""
        SELECT
            d.ts_code, d.pct_chg, d.amount, d.close,
            db.turnover_rate, db.circ_mv, db.total_mv,
            mf.buy_elg_amount, mf.sell_elg_amount,
            mf.net_mf_amount,
            l.limit_ AS limit_state
        FROM {SCHEMA}.raw_daily d
        LEFT JOIN {SCHEMA}.raw_daily_basic db
               ON db.ts_code = d.ts_code AND db.trade_date = d.trade_date
        LEFT JOIN {SCHEMA}.raw_moneyflow mf
               ON mf.ts_code = d.ts_code AND mf.trade_date = d.trade_date
        LEFT JOIN {SCHEMA}.raw_limit_list_d l
               ON l.ts_code = d.ts_code AND l.trade_date = d.trade_date
        WHERE d.trade_date = :d AND d.ts_code = ANY(:codes)
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "codes": ts_codes}).fetchall()
    if not rows:
        return pd.DataFrame(columns=_COLS + ["elg_net"])
    df = pd.DataFrame(rows, columns=_COLS)
    for c in df.columns:
        if c in ("ts_code", "limit_state"):
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["elg_net"] = df["buy_elg_amount"].fillna(0) - df["sell_elg_amount"].fillna(0)
    return df


def _load_member_history(
    engine: Engine,
    trade_date: dt.date,
    ts_codes: list[str],
    n_days: int = 5,
) -> pd.DataFrame:
    """Last n_days of pct_chg / amount / elg_net per stock."""
    _COLS = ["trade_date", "ts_code", "pct_chg", "amount", "elg_net"]
    if not ts_codes:
        return pd.DataFrame(columns=_COLS)
    start = trade_date - dt.timedelta(days=n_days * 2 + 5)
    sql = f"""
        SELECT
            d.trade_date, d.ts_code, d.pct_chg, d.amount,
            (COALESCE(mf.buy_elg_amount,0) - COALESCE(mf.sell_elg_amount,0)) AS elg_net
        FROM {SCHEMA}.raw_daily d
        LEFT JOIN {SCHEMA}.raw_moneyflow mf
               ON mf.ts_code = d.ts_code AND mf.trade_date = d.trade_date
        WHERE d.ts_code = ANY(:codes)
          AND d.trade_date >= :start
          AND d.trade_date < :d
        ORDER BY d.ts_code, d.trade_date
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "start": start, "codes": ts_codes}).fetchall()
    if not rows:
        return pd.DataFrame(columns=_COLS)
    df = pd.DataFrame(rows, columns=_COLS)
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df["elg_net"] = pd.to_numeric(df["elg_net"], errors="coerce")
    return df


def _load_kpl_themes(
    engine: Engine,
    trade_date: dt.date,
    ts_codes: list[str],
) -> dict[str, str]:
    """Map ts_code → theme string (from raw_kpl_list)."""
    if not ts_codes:
        return {}
    sql = f"""
        SELECT ts_code, theme
        FROM {SCHEMA}.raw_kpl_list
        WHERE trade_date = :d AND ts_code = ANY(:codes) AND theme IS NOT NULL
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "codes": ts_codes}).fetchall()
    return {r[0]: r[1] for r in rows}


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_filler(
    df: pd.DataFrame,
    sector_name: str | None,
    theme_map: dict[str, str],
) -> pd.DataFrame:
    """补涨 score per stock within a sector subset."""
    df = df.copy()
    if df.empty:
        return df

    # room_score: how far from limit-up (max gain ~ 10% for normal stocks)
    df["room_score"] = (1.0 - (df["pct_chg"].fillna(0) / 9.9)).clip(0.0, 1.0)
    # Penalty for broken stocks
    df.loc[df["pct_chg"] < -2.0, "room_score"] *= 0.4

    df["elg_rank"] = df["elg_net"].rank(pct=True, na_option="keep").fillna(0.5)
    df["amount_rank"] = df["amount"].rank(pct=True, na_option="keep").fillna(0.5)

    # Theme match bonus
    def _theme_match(ts_code: str) -> float:
        theme = theme_map.get(ts_code, "")
        if not theme or not sector_name:
            return 0.0
        # Substring match either direction
        if sector_name in theme or any(t.strip() and t.strip() in sector_name for t in theme.split("、")):
            return 1.0
        return 0.0

    df["theme_match"] = df["ts_code"].apply(_theme_match)

    df["filler_score"] = (
        df["room_score"] * 0.30
        + df["elg_rank"] * 0.25
        + df["amount_rank"] * 0.20
        + df["theme_match"] * 0.25
    )

    # Hard exclusions
    df = df[df["limit_state"] != "U"]                 # exclude already-涨停
    df = df[df["pct_chg"].between(-3.0, 6.0)]        # not crashed, not too high
    df = df.sort_values("filler_score", ascending=False)
    return df


def _score_trending(
    today_df: pd.DataFrame,
    history_df: pd.DataFrame,
    n_days: int = 5,
) -> pd.DataFrame:
    """趋势 score using rolling history.

    Only stocks with at least n_days of price history qualify.
    """
    if today_df.empty or history_df.empty:
        return pd.DataFrame()

    grouped = history_df.groupby("ts_code")
    rows = []
    for ts_code, grp in grouped:
        grp = grp.sort_values("trade_date").tail(n_days)
        if len(grp) < n_days:
            continue
        up_days = int((grp["pct_chg"] > 0).sum())
        flow_consistency = int((grp["elg_net"] > 0).sum())
        avg_amount = grp["amount"].mean()
        rows.append({
            "ts_code": ts_code,
            "up_days": up_days,
            "flow_consistency": flow_consistency,
            "avg_amount_5d": avg_amount,
        })

    if not rows:
        return pd.DataFrame()
    rolling = pd.DataFrame(rows)

    df = today_df.merge(rolling, on="ts_code", how="inner")
    if df.empty:
        return df

    # Trend criteria: at least 3/5 up days AND today still positive AND not 涨停
    df = df[(df["up_days"] >= 3) & (df["pct_chg"] > 0) & (df["limit_state"] != "U")]
    if df.empty:
        return df

    df["vol_ratio"] = df["amount"] / df["avg_amount_5d"].replace(0, pd.NA)
    df["vol_ratio"] = df["vol_ratio"].fillna(1.0)

    # Composite score: normalize each component then weight
    df["up_score"] = df["up_days"] / float(n_days)
    df["flow_score"] = df["flow_consistency"] / float(n_days)
    df["vol_score"] = df["vol_ratio"].clip(0.5, 2.5).apply(lambda v: (v - 0.5) / 2.0)

    df["trending_score"] = (
        df["up_score"] * 0.40
        + df["flow_score"] * 0.30
        + df["vol_score"] * 0.30
    )
    return df.sort_values("trending_score", ascending=False)


# ── Orchestrator ──────────────────────────────────────────────────────────────

def compute_candidates_for_date(
    engine: Engine,
    trade_date: dt.date,
    *,
    params: dict[str, Any],
) -> list[StockSignal]:
    """Identify 补涨 + 趋势 stocks across active sectors.

    Returns a list of StockSignal (which can be persisted via
    write_stock_signals from leader.py — same target table).
    """
    cand_params = params.get("candidate", {})
    min_amount = float(cand_params.get("min_amount", 5e7))
    min_circ_mv = float(cand_params.get("min_circ_mv", 2e9))
    fillers_per_sector = int(cand_params.get("fillers_per_sector", 3))
    trending_global_top = int(cand_params.get("trending_top_n", 15))

    sectors = _load_active_sectors(engine, trade_date)
    if sectors.empty:
        log.info("[candidate] no active sectors on %s", trade_date)
        return []

    dc_codes = sectors[sectors["sector_source"] == "dc"]["sector_code"].tolist()
    kpl_codes = sectors[sectors["sector_source"] == "kpl"]["sector_code"].tolist()
    sw_codes = sectors[sectors["sector_source"] == "sw_l2"]["sector_code"].tolist()
    dc_members = _load_dc_members(engine, trade_date, dc_codes)
    kpl_members = _load_kpl_members(engine, trade_date, kpl_codes)
    sw_members = _load_sw_members(engine, trade_date, sw_codes)

    all_ts_codes = sorted(set(
        (dc_members["ts_code"].tolist() if not dc_members.empty else [])
        + (kpl_members["ts_code"].tolist() if not kpl_members.empty else [])
        + (sw_members["ts_code"].tolist() if not sw_members.empty else [])
    ))
    if not all_ts_codes:
        return []

    stock_today = _load_member_stock_data(engine, trade_date, all_ts_codes)
    if stock_today.empty:
        return []

    # Apply liquidity / market-cap gates
    stock_today = stock_today[
        (stock_today["amount"].fillna(0) >= min_amount)
        & (stock_today["circ_mv"].fillna(0) >= min_circ_mv)
    ]
    if stock_today.empty:
        log.info("[candidate] no stocks pass liquidity/cap gates on %s", trade_date)
        return []

    already_tagged = _load_already_tagged(engine, trade_date)
    stock_today = stock_today[~stock_today["ts_code"].isin(already_tagged)]
    if stock_today.empty:
        return []

    theme_map = _load_kpl_themes(engine, trade_date, stock_today["ts_code"].tolist())

    output: list[StockSignal] = []
    seen_pairs: set[tuple[str, str]] = set()  # (ts_code, role) — table PK

    # ── 补涨: per-sector top N ────────────────────────────────────────────────
    sectors_sorted = sectors.sort_values(
        by="role_confidence",
        key=lambda c: c.map({"high": 0, "medium": 1, "low": 2}).fillna(2),
    )
    for _, sec in sectors_sorted.iterrows():
        sec_code = sec["sector_code"]
        sec_src = sec["sector_source"]
        sec_name = sec["sector_name"]
        members = (
            dc_members if sec_src == "dc"
            else kpl_members if sec_src == "kpl"
            else sw_members
        )
        sec_member_codes = members[members["sector_code"] == sec_code]["ts_code"].tolist()
        if not sec_member_codes:
            continue

        sub = stock_today[stock_today["ts_code"].isin(sec_member_codes)]
        if sub.empty:
            continue

        scored = _score_filler(sub, sec_name, theme_map)
        if scored.empty:
            continue

        for _, r in scored.head(fillers_per_sector).iterrows():
            key = (r["ts_code"], "补涨")
            if key in seen_pairs:
                continue
            seen_pairs.add(key)

            evidence = {
                "filler_score": round(float(r["filler_score"]), 4),
                "room_score": round(float(r["room_score"]), 4),
                "elg_rank": round(float(r["elg_rank"]), 4),
                "amount_rank": round(float(r["amount_rank"]), 4),
                "theme_match": float(r["theme_match"]),
                "pct_chg": round(float(r["pct_chg"]), 4),
                "amount": round(float(r["amount"] or 0), 2),
                "circ_mv": round(float(r["circ_mv"] or 0), 2),
                "sector_code": sec_code,
                "sector_source": sec_src,
                "sector_role": sec["role"],
            }
            output.append(StockSignal(
                trade_date=trade_date,
                ts_code=r["ts_code"],
                name=None,  # filled below if available
                primary_sector_code=sec_code,
                primary_sector_source=sec_src,
                role="补涨",
                score=float(r["filler_score"]),
                theme=theme_map.get(r["ts_code"]),
                lu_desc=None,
                evidence=evidence,
            ))

    # Look up names for 补涨 stocks
    names = pd.concat([
        dc_members[["ts_code", "name"]] if not dc_members.empty else pd.DataFrame(columns=["ts_code", "name"]),
        kpl_members[["ts_code", "name"]] if not kpl_members.empty else pd.DataFrame(columns=["ts_code", "name"]),
    ]).drop_duplicates(subset=["ts_code"]).set_index("ts_code")["name"].to_dict()
    for sig in output:
        if sig.name is None:
            sig.name = names.get(sig.ts_code)

    # ── 趋势: global top N (all members of active sectors) ────────────────────
    history = _load_member_history(engine, trade_date, all_ts_codes, n_days=5)
    trending = _score_trending(stock_today, history, n_days=5)

    if not trending.empty:
        for _, r in trending.head(trending_global_top).iterrows():
            key = (r["ts_code"], "趋势")
            if key in seen_pairs:
                continue
            seen_pairs.add(key)

            # Find the most-confident sector this stock belongs to
            primary_sec = None
            for src, mem_df in (("dc", dc_members), ("kpl", kpl_members), ("sw_l2", sw_members)):
                if mem_df.empty:
                    continue
                hits = mem_df[mem_df["ts_code"] == r["ts_code"]]
                if not hits.empty:
                    primary_sec = (hits.iloc[0]["sector_code"], src)
                    break
            if not primary_sec:
                continue

            evidence = {
                "trending_score": round(float(r["trending_score"]), 4),
                "up_days_5d": int(r["up_days"]),
                "flow_consistency_5d": int(r["flow_consistency"]),
                "vol_ratio_today": round(float(r["vol_ratio"]), 4),
                "pct_chg": round(float(r["pct_chg"]), 4),
                "amount": round(float(r["amount"] or 0), 2),
                "circ_mv": round(float(r["circ_mv"] or 0), 2),
                "sector_code": primary_sec[0],
                "sector_source": primary_sec[1],
            }
            output.append(StockSignal(
                trade_date=trade_date,
                ts_code=r["ts_code"],
                name=names.get(r["ts_code"]),
                primary_sector_code=primary_sec[0],
                primary_sector_source=primary_sec[1],
                role="趋势",
                score=float(r["trending_score"]),
                theme=theme_map.get(r["ts_code"]),
                lu_desc=None,
                evidence=evidence,
            ))

    log.info("[candidate] %s: %d candidates (补涨 + 趋势)", trade_date, len(output))
    return output
