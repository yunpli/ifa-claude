"""聚宝盆 — 两阳夹一阴 K 线组合.

Pattern: T-2 / T-1 / T0 form a "盆" shape:
    T-2:  实体阳线 (close > open, 实体 ≥ 1%)
    T-1:  小K线 (实体 < T-2 实体的 50%, 可阴可阳)
    T0:   实体阳线 (close > open) AND close > T-2.close (突破)

Volume confirmation (must hold):
    vol(T-1) < vol(T-2)   — 回调缩量
    vol(T0)  > vol(T-1)   — 上涨放量
    vol(T0)  >= vol(T-2) * 0.8 — 不低于T-2太多

Resonance bonus (not required, but boosts confidence):
    Stock近期 close 接近 MA24 (在生命线附近形成的 basin 最可靠)
    → on_sniper_setup = True

Confidence components:
    1. pattern_quality      0.40  形态完美度（中阴线小、突破幅度）
    2. vol_pattern_match    0.25  量能配合
    3. on_sniper_setup      0.20  与神枪手共振 (在 MA24 附近)
    4. body_strength        0.15  T0 阳线强度（close 接近 high）
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ifa.families.ningbo.strategies._indicators import enrich_indicators

STRATEGY_NAME = "treasure_basin"

MIN_T2_BODY_PCT = 0.01           # T-2 阳线实体 ≥ 1%
MAX_T1_BODY_RATIO = 0.5          # T-1 实体 ≤ T-2 实体的 50%
MIN_T0_BREAKOUT_PCT = 0.0        # T0 close > T-2 close（任何幅度的突破）
VOL_T0_VS_T2_MIN_RATIO = 0.8     # vol(T0) >= 0.8 * vol(T-2)
NEAR_MA24_PCT = 0.05             # close 在 MA24 ±5% 范围内算"接近"
MIN_LOOKBACK_BARS = 70

# Confidence component weights
W_PATTERN_QUALITY = 0.40
W_VOL_PATTERN = 0.25
W_SNIPER_SETUP = 0.20
W_BODY_STRENGTH = 0.15


def _check_one(stock_df: pd.DataFrame, on_date: dt.date) -> dict | None:
    """Check 聚宝盆 pattern ending on on_date."""
    on_date_ts = pd.to_datetime(on_date)

    # Need at least last 3 trading days (T-2, T-1, T0)
    sub = stock_df[stock_df["trade_date"] <= on_date_ts].tail(3).reset_index(drop=True)
    if len(sub) < 3:
        return None
    if sub.iloc[-1]["trade_date"] != on_date_ts:
        return None

    t2, t1, t0 = sub.iloc[0], sub.iloc[1], sub.iloc[2]

    # Validate basic OHLCV completeness
    for d in (t2, t1, t0):
        if pd.isna(d["open"]) or pd.isna(d["close"]) or pd.isna(d["vol"]):
            return None

    # ── T-2: 实体阳线 ────────────────────────────────────────────
    t2_body = t2["close"] - t2["open"]
    t2_body_pct = t2_body / t2["open"]
    if t2_body <= 0 or t2_body_pct < MIN_T2_BODY_PCT:
        return None
    t2_body_abs = abs(t2_body)

    # ── T-1: 小K线（实体 < T-2 实体的 50%） ─────────────────────
    t1_body_abs = abs(t1["close"] - t1["open"])
    if t1_body_abs > t2_body_abs * MAX_T1_BODY_RATIO:
        return None

    # ── T0: 实体阳线 AND close > T-2.close ───────────────────────
    t0_body = t0["close"] - t0["open"]
    if t0_body <= 0:
        return None
    if t0["close"] <= t2["close"] * (1 + MIN_T0_BREAKOUT_PCT):
        return None

    # ── 量能: vol(T-1) < vol(T-2) AND vol(T0) > vol(T-1) ──────────
    if t1["vol"] >= t2["vol"]:
        return None
    if t0["vol"] <= t1["vol"]:
        return None
    vol_t0_vs_t2 = t0["vol"] / t2["vol"]
    if vol_t0_vs_t2 < VOL_T0_VS_T2_MIN_RATIO:
        return None

    # ── 形态质量评分 ─────────────────────────────────────────────
    # T-1 越小越好（≤30%T-2 满分）
    t1_smallness = 1.0 - min(1.0, t1_body_abs / (t2_body_abs * MAX_T1_BODY_RATIO))
    # T0 突破越大越好（>2% 满分）
    t0_breakout_pct = (t0["close"] - t2["close"]) / t2["close"]
    breakout_quality = min(1.0, t0_breakout_pct / 0.02)
    pattern_quality = 0.6 * t1_smallness + 0.4 * breakout_quality

    # 量能配合评分
    # 缩量幅度: vol(T-1)/vol(T-2) 越小越好（0.5 满分）
    vol_contraction = 1.0 - min(1.0, (t1["vol"] / t2["vol"]) / 0.5)
    # 放量幅度: vol(T0)/vol(T-1) 越大越好（>2 满分）
    vol_expansion = min(1.0, (t0["vol"] / t1["vol"]) / 2.0)
    vol_pattern = 0.5 * vol_contraction + 0.5 * vol_expansion

    # T0 阳线强度: close 在当日 H-L 中的位置
    t0_range = t0["high"] - t0["low"]
    if t0_range > 0:
        body_strength = (t0["close"] - t0["low"]) / t0_range
    else:
        body_strength = 0.5

    # 与神枪手共振（在 MA24 附近）
    ma24 = t0.get("close_ma24")
    on_sniper = False
    if ma24 is not None and not pd.isna(ma24):
        dist_to_ma24 = abs(t0["close"] - ma24) / ma24
        if dist_to_ma24 <= NEAR_MA24_PCT:
            on_sniper = True
    sniper_score = 1.0 if on_sniper else 0.3  # 不在 MA24 附近不至于零分

    confidence = (
        W_PATTERN_QUALITY * pattern_quality
        + W_VOL_PATTERN * vol_pattern
        + W_SNIPER_SETUP * sniper_score
        + W_BODY_STRENGTH * body_strength
    )

    return {
        "ts_code": t0["ts_code"],
        "pattern_quality": float(pattern_quality),
        "vol_pattern": float(vol_pattern),
        "body_strength": float(body_strength),
        "on_sniper_setup": bool(on_sniper),
        "confidence_score": float(confidence),
        "components": {
            "pattern_quality": float(pattern_quality),
            "vol_pattern": float(vol_pattern),
            "sniper_setup": float(sniper_score),
            "body_strength": float(body_strength),
        },
        "signal_meta": {
            "t2": {"date": str(t2["trade_date"].date()), "open": float(t2["open"]),
                   "close": float(t2["close"]), "vol": float(t2["vol"])},
            "t1": {"date": str(t1["trade_date"].date()), "open": float(t1["open"]),
                   "close": float(t1["close"]), "vol": float(t1["vol"])},
            "t0": {"date": str(t0["trade_date"].date()), "open": float(t0["open"]),
                   "close": float(t0["close"]), "high": float(t0["high"]),
                   "low": float(t0["low"]), "vol": float(t0["vol"])},
            "t0_breakout_pct": float(t0_breakout_pct),
            "vol_t1_t2_ratio": float(t1["vol"] / t2["vol"]),
            "vol_t0_t1_ratio": float(t0["vol"] / t1["vol"]),
            "ma24": float(ma24) if ma24 is not None and not pd.isna(ma24) else None,
            "near_ma24": on_sniper,
        },
    }


def detect_signals(universe_df: pd.DataFrame, on_date: dt.date) -> pd.DataFrame:
    """Detect 聚宝盆 patterns ending on on_date."""
    if universe_df.empty:
        return pd.DataFrame()

    df = universe_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    on_date_ts = pd.to_datetime(on_date)
    counts = df[df["trade_date"] <= on_date_ts].groupby("ts_code").size()
    eligible_codes = counts[counts >= MIN_LOOKBACK_BARS].index
    df = df[df["ts_code"].isin(eligible_codes)]

    # Pre-filter: today must be 阳线 with >0 body
    today_df = df[df["trade_date"] == on_date_ts]
    today_bullish = today_df[today_df["close"] > today_df["open"]]
    candidate_codes = today_bullish["ts_code"].tolist()
    df = df[df["ts_code"].isin(candidate_codes)]

    results = []
    for ts_code, group in df.groupby("ts_code"):
        group = group.sort_values("trade_date").reset_index(drop=True)
        try:
            enriched = enrich_indicators(group)
        except Exception:
            continue
        result = _check_one(enriched, on_date)
        if result is not None:
            results.append(result)

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results).sort_values("confidence_score", ascending=False).reset_index(drop=True)
