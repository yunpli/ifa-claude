"""选股六步曲 — 基础筛选漏斗.

Six sequential filters (each step requires prev to pass):
    1. 均线多头         MA(5) > MA(10) > MA(20) > MA(60)
    2. 量线多头         VolMA(5) > VolMA(10) > VolMA(20)
    3. MACD 金叉且平行向上  DIF > DEA, both rising, DIF > 0 preferred
    4. KDJ 金叉或多头    K > D, J rising
    5. RSI 多头向上     6-day RSI > 50, rising
    6. WR 进入强势区     5-day WR > 50, 55-day WR > 50

Output columns:
    ts_code, step1_ma, step2_vol, step3_macd, step4_kdj, step5_rsi, step6_wr,
    steps_passed (0-6), strength_score (0-1), signal_meta (dict)

This is a *baseline filter* — its output feeds into the more specific
strategies (sniper / treasure_basin / half_year_double).  Stocks failing
≥4 of 6 steps are unlikely to be good candidates for any 宁波 strategy.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ifa.families.ningbo.strategies._indicators import enrich_indicators

STRATEGY_NAME = "six_step"

# Minimum lookback to compute all 6 indicators reliably (60-day MA needs 60 bars)
MIN_LOOKBACK_BARS = 70


def _check_one(stock_df: pd.DataFrame, on_date: dt.date) -> dict | None:
    """Run six-step check on a single stock's enriched DataFrame.

    Returns dict with per-step flags + strength_score, or None if data insufficient.
    """
    # Locate the row for on_date
    on_date_ts = pd.to_datetime(on_date)
    df_today = stock_df[stock_df["trade_date"] == on_date_ts]
    if df_today.empty:
        return None
    today = df_today.iloc[-1]

    if pd.isna(today.get("close_ma60")):
        return None  # insufficient history

    # ── Step 1: 均线多头 ──────────────────────────────────────────────────
    ma5, ma10, ma20, ma60 = today["close_ma5"], today["close_ma10"], today["close_ma20"], today["close_ma60"]
    step1 = bool(ma5 > ma10 > ma20 > ma60)

    # ── Step 2: 量线多头 ──────────────────────────────────────────────────
    v5, v10, v20 = today["vol_ma5"], today["vol_ma10"], today["vol_ma20"]
    if pd.isna(v20):
        return None
    step2 = bool(v5 > v10 > v20)

    # ── Step 3: MACD 金叉且平行向上 ───────────────────────────────────────
    dif, dea, hist = today["macd_dif"], today["macd_dea"], today["macd_hist"]
    if pd.isna(dif) or pd.isna(dea):
        return None
    # Need at least 3 trailing rows to check "rising" trend
    trailing = stock_df[stock_df["trade_date"] <= on_date_ts].tail(3)
    macd_rising = (
        len(trailing) >= 3
        and trailing["macd_dif"].is_monotonic_increasing
        and trailing["macd_dea"].is_monotonic_increasing
    )
    step3 = bool(dif > dea and macd_rising)

    # ── Step 4: KDJ 金叉或多头向上 ────────────────────────────────────────
    k, d, j = today["kdj_k"], today["kdj_d"], today["kdj_j"]
    if pd.isna(k) or pd.isna(d):
        return None
    j_rising = len(trailing) >= 2 and trailing["kdj_j"].iloc[-1] > trailing["kdj_j"].iloc[-2]
    step4 = bool(k > d and j_rising)

    # ── Step 5: RSI 多头向上 ──────────────────────────────────────────────
    rsi6 = today["rsi6"]
    if pd.isna(rsi6):
        return None
    rsi_rising = (
        len(trailing) >= 2 and trailing["rsi6"].iloc[-1] > trailing["rsi6"].iloc[-2]
    )
    step5 = bool(rsi6 > 50 and rsi_rising)

    # ── Step 6: WR 强势区 ─────────────────────────────────────────────────
    wr5, wr55 = today["wr5"], today["wr55"]
    if pd.isna(wr5) or pd.isna(wr55):
        return None
    step6 = bool(wr5 > 50 and wr55 > 50)

    steps = [step1, step2, step3, step4, step5, step6]
    n_passed = sum(steps)

    # Strength score: weighted by step (later steps are stricter, weight more)
    # 0-1 normalized
    weights = [1.5, 1.5, 2.0, 1.5, 1.0, 1.0]  # sum=8.5
    raw_score = sum(w for s, w in zip(steps, weights) if s)
    strength_score = raw_score / sum(weights)

    return {
        "ts_code": today["ts_code"],
        "step1_ma": step1,
        "step2_vol": step2,
        "step3_macd": step3,
        "step4_kdj": step4,
        "step5_rsi": step5,
        "step6_wr": step6,
        "steps_passed": n_passed,
        "strength_score": float(strength_score),
        "signal_meta": {
            "close": float(today["close"]),
            "ma5": float(ma5), "ma10": float(ma10), "ma20": float(ma20), "ma60": float(ma60),
            "vol_ma5": float(v5), "vol_ma10": float(v10), "vol_ma20": float(v20),
            "macd_dif": float(dif), "macd_dea": float(dea),
            "kdj_k": float(k), "kdj_d": float(d), "kdj_j": float(j),
            "rsi6": float(rsi6),
            "wr5": float(wr5), "wr55": float(wr55),
        },
    }


def screen(universe_df: pd.DataFrame, on_date: dt.date, *, min_steps_passed: int = 4) -> pd.DataFrame:
    """Run six-step screening across the universe.

    Args:
        universe_df: output of `data.load_universe`, must contain ≥ MIN_LOOKBACK_BARS days
        on_date: trading date being screened
        min_steps_passed: minimum number of 6 steps that must pass (default 4)

    Returns:
        DataFrame ordered by strength_score desc.
        Empty if no stock qualifies.
    """
    if universe_df.empty:
        return pd.DataFrame()

    df = universe_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    # Filter to stocks with enough history before on_date
    on_date_ts = pd.to_datetime(on_date)
    counts = df[df["trade_date"] <= on_date_ts].groupby("ts_code").size()
    eligible_codes = counts[counts >= MIN_LOOKBACK_BARS].index
    df = df[df["ts_code"].isin(eligible_codes)]

    results = []
    for ts_code, group in df.groupby("ts_code"):
        group = group.sort_values("trade_date").reset_index(drop=True)
        try:
            enriched = enrich_indicators(group)
        except Exception:
            continue
        result = _check_one(enriched, on_date)
        if result is not None and result["steps_passed"] >= min_steps_passed:
            results.append(result)

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results)
    return out.sort_values("strength_score", ascending=False).reset_index(drop=True)
