"""半年翻倍 — 中线大牛股捕捉.

Three-condition resonance (all must hold today):

    A. 日线均线: MA(5) > MA(24), 站稳 MA24
       AND MA(5) cross above MA(24) within last 60 trading days
       (more leeway than sniper — looking for stable trend, not fresh cross)

    B. 日线量线: VolMA(5) > VolMA(60), VolMA(5) 陡峭向上
       (5日成交量均线远高于60日均线，显示资金持续流入)

    C. 周线: 周MACD 平行向上 + 刚刚上穿0轴 + 周均线金叉
       (本周DIF > DEA AND 上周DIF <= DEA OR 二者非常接近;
        周MA(5) > 周MA(10))

Filter (avoid late chase):
    Stock 60-day cum return < +50%  (避免追高)

Confidence components:
    1. ma_cross_strength    0.20  日线 5/24 站稳生命线天数
    2. vol_cross_strength   0.25  日线 5/60 量线斜率（陡峭=好）
    3. macd_strength        0.20  日线 MACD 上升斜率
    4. weekly_macd_zero     0.20  周线 MACD 刚上穿 0 轴
    5. weekly_ma_golden     0.15  周线 MA 金叉

Half-year-double 是中线策略（持仓数月级），但 5-15 天内的入场点
优劣同样可观察 — 信号触发当天通常是周线突破的初期。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ifa.families.ningbo.strategies._indicators import enrich_indicators

STRATEGY_NAME = "half_year_double"

MAX_DAYS_SINCE_MA_CROSS = 60      # 5/24 cross within ~3 months
MIN_VOL_5_60_RATIO = 1.10         # 5日均量 ≥ 60日均量 × 1.1
MAX_60D_RETURN = 0.50             # 过去 60 个交易日累计涨幅 < 50%
WEEKLY_MIN_LOOKBACK = 26          # need ≥26 weeks for weekly MA + MACD
MIN_LOOKBACK_BARS = 130           # 60-day MA + 60-day return needs 130 bars

# Confidence component weights
W_MA_CROSS = 0.20
W_VOL_CROSS = 0.25
W_MACD = 0.20
W_WEEKLY_ZERO = 0.20
W_WEEKLY_GOLDEN = 0.15


def _compute_weekly_indicators(weekly_df: pd.DataFrame) -> pd.DataFrame:
    """Compute weekly MA + MACD on a single stock's weekly bars."""
    df = weekly_df.copy().sort_values("week_end").reset_index(drop=True)
    df["wma5"] = df["close"].rolling(window=5, min_periods=5).mean()
    df["wma10"] = df["close"].rolling(window=10, min_periods=10).mean()

    ema_fast = df["close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["close"].ewm(span=26, adjust=False).mean()
    df["wmacd_dif"] = ema_fast - ema_slow
    df["wmacd_dea"] = df["wmacd_dif"].ewm(span=9, adjust=False).mean()
    df["wmacd_hist"] = df["wmacd_dif"] - df["wmacd_dea"]
    return df


def _check_one(stock_daily: pd.DataFrame, weekly_df: pd.DataFrame, on_date: dt.date) -> dict | None:
    """Check 半年翻倍 conditions on a single stock."""
    on_date_ts = pd.to_datetime(on_date)

    # ── Daily checks ────────────────────────────────────────────────────
    today_row = stock_daily[stock_daily["trade_date"] == on_date_ts]
    if today_row.empty:
        return None
    today = today_row.iloc[-1]

    if pd.isna(today.get("close_ma24")) or pd.isna(today.get("vol_ma60")):
        return None

    ma5 = float(today["close_ma5"])
    ma24 = float(today["close_ma24"])
    vol_ma5 = float(today["vol_ma5"])
    vol_ma60 = float(today["vol_ma60"])
    today_close = float(today["close"])

    # A. MA5 > MA24, 站稳生命线
    if ma5 <= ma24 or today_close < ma24:
        return None

    # Find days since MA cross (similar to sniper, but allow 60 days)
    sub = stock_daily[stock_daily["trade_date"] <= on_date_ts].dropna(
        subset=["close_ma5", "close_ma24"]
    ).reset_index(drop=True)
    if sub.empty:
        return None
    above = (sub["close_ma5"] > sub["close_ma24"]).values
    if not above[-1]:
        return None

    cross_idx = None
    for i in range(len(sub) - 2, -1, -1):
        if not above[i]:
            cross_idx = i + 1
            break
    if cross_idx is None:
        return None
    cross_date = sub.iloc[cross_idx]["trade_date"]
    days_since_ma_cross = (on_date_ts - cross_date).days
    if days_since_ma_cross > MAX_DAYS_SINCE_MA_CROSS:
        return None

    # B. VolMA5 > VolMA60 * 1.1
    vol_ratio = vol_ma5 / vol_ma60 if vol_ma60 > 0 else 0
    if vol_ratio < MIN_VOL_5_60_RATIO:
        return None

    # B-2: VolMA5 sloping up (last 3 vol_ma5 trending up)
    trailing = sub.tail(3)
    if len(trailing) < 3 or not trailing["vol_ma5"].is_monotonic_increasing:
        return None

    # MACD daily strength: DIF rising + DIF > DEA
    if pd.isna(today.get("macd_dif")) or pd.isna(today.get("macd_dea")):
        return None
    if today["macd_dif"] <= today["macd_dea"]:
        return None
    if not trailing["macd_dif"].is_monotonic_increasing:
        return None

    # Filter: not too far up in last 60 days
    bars_back = stock_daily[stock_daily["trade_date"] <= on_date_ts].tail(61)
    if len(bars_back) < 30:
        return None
    cum_return_60d = (today_close - bars_back.iloc[0]["close"]) / bars_back.iloc[0]["close"]
    if cum_return_60d > MAX_60D_RETURN:
        return None  # already up too much, late chase risk

    # ── Weekly checks ───────────────────────────────────────────────────
    if weekly_df is None or weekly_df.empty or len(weekly_df) < WEEKLY_MIN_LOOKBACK:
        return None
    wk = _compute_weekly_indicators(weekly_df)

    # Find current week (most recent)
    wk_today = wk.iloc[-1]
    wk_prev = wk.iloc[-2] if len(wk) >= 2 else None

    if pd.isna(wk_today["wma5"]) or pd.isna(wk_today["wma10"]):
        return None
    if pd.isna(wk_today["wmacd_dif"]) or pd.isna(wk_today["wmacd_dea"]):
        return None

    # C-1: 周MACD 平行向上
    if wk_today["wmacd_dif"] <= wk_today["wmacd_dea"]:
        return None
    if wk_prev is not None and not pd.isna(wk_prev["wmacd_dif"]):
        if wk_today["wmacd_dif"] <= wk_prev["wmacd_dif"]:
            return None  # DIF must be rising

    # C-2: 周MA 金叉 (wma5 > wma10) — currently above
    if wk_today["wma5"] <= wk_today["wma10"]:
        return None

    # C-3: 周MACD 刚上穿 0 轴 (DIF crossed above 0 within last few weeks)
    # Score this rather than gate it
    weekly_dif_just_crossed_zero = False
    weeks_since_zero_cross = None
    for back in range(1, min(8, len(wk))):
        prev_dif = wk.iloc[-1 - back]["wmacd_dif"]
        if pd.isna(prev_dif):
            continue
        if prev_dif <= 0 and wk.iloc[-back]["wmacd_dif"] > 0:
            weekly_dif_just_crossed_zero = True
            weeks_since_zero_cross = back
            break

    # ── Confidence components ───────────────────────────────────────────

    # 1. MA cross strength: more days = more proven trend (peaks at 30 days)
    ma_cross_strength = min(1.0, days_since_ma_cross / 30.0)

    # 2. Vol cross strength: vol_ratio above threshold + slope
    vol_cross_strength = min(1.0, (vol_ratio - 1.0) / 0.5)  # 1.5 ratio = full score

    # 3. Daily MACD strength: DIF/DEA gap relative to close
    macd_gap = (today["macd_dif"] - today["macd_dea"]) / today_close
    macd_strength = min(1.0, max(0.0, macd_gap * 200))  # rough scaling

    # 4. Weekly MACD zero-cross recency
    if weekly_dif_just_crossed_zero:
        # crossed 1 week ago = 1.0, 7 weeks ago = 0.1
        weekly_zero_score = max(0.1, 1.0 - (weeks_since_zero_cross - 1) / 7.0)
    else:
        # already above 0 for a while (could still be valid but less "fresh")
        if wk_today["wmacd_dif"] > 0:
            weekly_zero_score = 0.5
        else:
            weekly_zero_score = 0.0

    # 5. Weekly MA golden cross strength
    wma_gap = (wk_today["wma5"] - wk_today["wma10"]) / wk_today["wma10"]
    weekly_golden_score = min(1.0, max(0.0, wma_gap * 50))

    confidence = (
        W_MA_CROSS * ma_cross_strength
        + W_VOL_CROSS * vol_cross_strength
        + W_MACD * macd_strength
        + W_WEEKLY_ZERO * weekly_zero_score
        + W_WEEKLY_GOLDEN * weekly_golden_score
    )

    return {
        "ts_code": today["ts_code"],
        "days_since_ma_cross": int(days_since_ma_cross),
        "vol_5_60_ratio": float(vol_ratio),
        "weekly_macd_zero_crossed": bool(weekly_dif_just_crossed_zero),
        "weeks_since_zero_cross": int(weeks_since_zero_cross) if weeks_since_zero_cross else None,
        "cum_return_60d": float(cum_return_60d),
        "confidence_score": float(confidence),
        "components": {
            "ma_cross": float(ma_cross_strength),
            "vol_cross": float(vol_cross_strength),
            "macd": float(macd_strength),
            "weekly_zero": float(weekly_zero_score),
            "weekly_golden": float(weekly_golden_score),
        },
        "signal_meta": {
            "today_close": today_close,
            "ma5": ma5,
            "ma24": ma24,
            "vol_ma5": vol_ma5,
            "vol_ma60": vol_ma60,
            "macd_dif": float(today["macd_dif"]),
            "macd_dea": float(today["macd_dea"]),
            "weekly_dif": float(wk_today["wmacd_dif"]),
            "weekly_dea": float(wk_today["wmacd_dea"]),
            "wma5": float(wk_today["wma5"]),
            "wma10": float(wk_today["wma10"]),
            "days_since_ma_cross": int(days_since_ma_cross),
            "cum_return_60d_pct": float(cum_return_60d * 100),
        },
    }


def detect_signals(
    universe_df: pd.DataFrame,
    weekly_df: pd.DataFrame,
    on_date: dt.date,
) -> pd.DataFrame:
    """Detect 半年翻倍 candidates ending on on_date.

    Args:
        universe_df: from data.load_universe (≥130 day lookback)
        weekly_df: from data.load_weekly_bars (≥30 weeks lookback) for the same stocks
        on_date: trading date being screened
    """
    if universe_df.empty:
        return pd.DataFrame()

    df = universe_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    on_date_ts = pd.to_datetime(on_date)
    counts = df[df["trade_date"] <= on_date_ts].groupby("ts_code").size()
    eligible_codes = counts[counts >= MIN_LOOKBACK_BARS].index
    df = df[df["ts_code"].isin(eligible_codes)]

    # Pre-filter: today's MA-equivalent is bullish
    today_df = df[df["trade_date"] == on_date_ts]
    if today_df.empty:
        return pd.DataFrame()

    # Index weekly_df by ts_code for fast lookup
    if weekly_df is None or weekly_df.empty:
        weekly_groups = {}
    else:
        weekly_groups = {code: g.sort_values("week_end") for code, g in weekly_df.groupby("ts_code")}

    results = []
    for ts_code, group in df.groupby("ts_code"):
        group = group.sort_values("trade_date").reset_index(drop=True)
        try:
            enriched = enrich_indicators(group)
        except Exception:
            continue
        wk = weekly_groups.get(ts_code, pd.DataFrame())
        result = _check_one(enriched, wk, on_date)
        if result is not None:
            results.append(result)

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results).sort_values("confidence_score", ascending=False).reset_index(drop=True)
