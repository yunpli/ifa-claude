"""神枪手 — 5/24 MA 回调买入策略.

Setup (must all be true today):
    A. 5MA crossed above 24MA within last N days (默认 N=20)
    B. Since cross, no daily close has been below 24MA
       (i.e., the uptrend has held the 生命线 as support throughout)
    C. Today's low touched 24MA (within 0.5% margin)
    D. Today's close >= 24MA (生命线 support held)
    E. Today's vol < 5-day avg vol (回调缩量)
    F. Today's MA5 still > MA24 (uptrend still intact)

Trigger types by touch count (since cross_date, including today):
    - strike_1   (神枪手出击): 1st touch — early signal
    - strike_2   (神枪手买入): 2nd touch — highest confidence per spec
    - strike_3p  (3rd+ touch): support showing fatigue, lower confidence

Confidence factors (each 0-1, weighted-averaged):
    1. trigger weight       0.30 (strike_2 > strike_1 > strike_3p)
    2. touch_precision      0.20 (how close low got to MA24)
    3. rebound_strength     0.20 (where close lies in today's H-L range)
    4. vol_contraction      0.15 (how much vol dropped vs 5-day avg)
    5. cross_freshness      0.15 (how recently the cross happened)

A touch is "distinct" only after a clean recovery: low must rise ≥2%
above MA24 before the next dip can be counted as a new touch.  This
prevents a multi-day stall at MA24 from being miscounted as multiple
touches.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ifa.families.ningbo.strategies._indicators import enrich_indicators

STRATEGY_NAME = "sniper"

MAX_DAYS_SINCE_CROSS = 20       # cross must be within this many calendar days
TOUCH_NEAR_MARGIN = 0.005       # low within +0.5% above MA24 still counts as touch
RECOVERY_MARGIN = 0.02          # low must rise ≥2% above MA24 to "recover"
CLOSE_HOLD_MARGIN = 0.01        # close ≥ MA24 * (1 - 1%) — small slop for pullbacks
MAX_BREAKDOWN_DAYS = 2          # allow up to 2 non-today days with close < MA24
MIN_LOOKBACK_BARS = 70          # need 60-day MA + buffer

# Trigger weights for confidence
TRIGGER_WEIGHTS = {
    "strike_1": 0.65,   # first touch — confident but unproven
    "strike_2": 1.00,   # second touch — spec's gold standard
    "strike_3p": 0.40,  # 3rd+ — support weakening
}

# Component weights for final confidence (sum = 1.0)
W_TRIGGER = 0.30
W_TOUCH_PRECISION = 0.20
W_REBOUND_STRENGTH = 0.20
W_VOL_CONTRACTION = 0.15
W_CROSS_FRESHNESS = 0.15


def _find_recent_cross(df: pd.DataFrame, on_date_ts: pd.Timestamp) -> pd.Timestamp | None:
    """Find the start of the current uninterrupted "MA5 > MA24" period.

    Walks backwards from on_date.  The cross_date is the first day in the
    current consecutive streak where MA5 stayed above MA24.  This means:
      - Today MA5 must be > MA24 (otherwise no current uptrend exists)
      - The streak ends at the most recent day where MA5 <= MA24

    Returns None if today MA5 <= MA24, or if the streak is older than
    MAX_DAYS_SINCE_CROSS, or if the streak has no valid start (data too short).
    """
    sub = df[df["trade_date"] <= on_date_ts].copy()
    if len(sub) < 2:
        return None

    sub = sub.dropna(subset=["close_ma5", "close_ma24"]).reset_index(drop=True)
    if sub.empty:
        return None

    above = (sub["close_ma5"] > sub["close_ma24"]).values
    if not above[-1]:
        return None  # not above today, no current uptrend

    # Walk backwards from today; find the most recent index where MA5 <= MA24
    cross_idx = None
    for i in range(len(sub) - 2, -1, -1):
        if not above[i]:
            cross_idx = i + 1  # the day after the last "below" day
            break

    if cross_idx is None:
        # MA5 has been above MA24 for the entire visible history — too old to call
        return None

    cross_date = sub.iloc[cross_idx]["trade_date"]
    days_since = (on_date_ts - cross_date).days
    if days_since > MAX_DAYS_SINCE_CROSS:
        return None
    return cross_date


def _count_touches_in_period(period_df: pd.DataFrame) -> int:
    """Count distinct touches of MA24 in the period.

    A touch starts when low <= MA24 * (1 + TOUCH_NEAR_MARGIN).
    A touch ends (and the next one becomes countable) when low rises
    above MA24 * (1 + RECOVERY_MARGIN) — clean recovery required.
    """
    if period_df.empty:
        return 0

    touch_thresh = period_df["close_ma24"] * (1 + TOUCH_NEAR_MARGIN)
    recovery_thresh = period_df["close_ma24"] * (1 + RECOVERY_MARGIN)

    n_touches = 0
    in_touch = False
    for i in range(len(period_df)):
        low = period_df["low"].iloc[i]
        if pd.isna(low):
            continue
        t_thresh = touch_thresh.iloc[i]
        r_thresh = recovery_thresh.iloc[i]

        if not in_touch and low <= t_thresh:
            n_touches += 1
            in_touch = True
        elif in_touch and low > r_thresh:
            in_touch = False
    return n_touches


def _check_one(stock_df: pd.DataFrame, on_date: dt.date) -> dict | None:
    """Run sniper check on a single enriched stock DataFrame."""
    on_date_ts = pd.to_datetime(on_date)
    df_today = stock_df[stock_df["trade_date"] == on_date_ts]
    if df_today.empty:
        return None
    today = df_today.iloc[-1]

    # Need MA24, MA5, vol_ma5
    if pd.isna(today.get("close_ma24")) or pd.isna(today.get("close_ma5")) or pd.isna(today.get("vol_ma5")):
        return None

    ma24 = float(today["close_ma24"])
    ma5 = float(today["close_ma5"])
    today_close = float(today["close"])
    today_low = float(today["low"])
    today_high = float(today["high"])
    today_vol = float(today["vol"])
    vol_ma5 = float(today["vol_ma5"])

    # ── F. Today MA5 still above MA24 ──────────────────────────────────
    if ma5 <= ma24:
        return None

    # ── A. Find recent cross of MA5 above MA24 ─────────────────────────
    cross_date = _find_recent_cross(stock_df, on_date_ts)
    if cross_date is None:
        return None

    # ── B. Since cross, uptrend held生命线 ─────────────────────────────
    # Allow up to MAX_BREAKDOWN_DAYS where close < MA24 * (1 - CLOSE_HOLD_MARGIN)
    # Pullbacks intra-period are normal; we only reject sustained breakdowns.
    period = stock_df[
        (stock_df["trade_date"] >= cross_date) & (stock_df["trade_date"] <= on_date_ts)
    ].copy()
    if period["close_ma24"].isna().any():
        return None
    non_today = period[period["trade_date"] != on_date_ts]
    breakdown_count = int(
        (non_today["close"] < non_today["close_ma24"] * (1 - CLOSE_HOLD_MARGIN)).sum()
    )
    if breakdown_count > MAX_BREAKDOWN_DAYS:
        return None

    # ── C. Today's low touched MA24 ────────────────────────────────────
    if today_low > ma24 * (1 + TOUCH_NEAR_MARGIN):
        return None

    # ── D. Today's close >= MA24 (support held) ────────────────────────
    if today_close < ma24 * (1 - CLOSE_HOLD_MARGIN):
        return None

    # ── E. Today's vol < 5-day avg vol (缩量回调) ──────────────────────
    if today_vol >= vol_ma5:
        return None

    # ── Count touches since cross (including today) ────────────────────
    n_total_touches = _count_touches_in_period(period)
    if n_total_touches < 1:
        return None  # shouldn't happen since today is a touch

    if n_total_touches == 1:
        trigger_type = "strike_1"
    elif n_total_touches == 2:
        trigger_type = "strike_2"
    else:
        trigger_type = "strike_3p"

    # ── Confidence components ──────────────────────────────────────────

    # 1. Trigger weight
    trigger_w = TRIGGER_WEIGHTS[trigger_type]

    # 2. Touch precision: how close did low come to MA24?
    # Perfect = low exactly at MA24. Score drops as low gets further (above OR below).
    touch_distance_pct = abs(today_low - ma24) / ma24
    touch_precision = max(0.0, 1.0 - touch_distance_pct / TOUCH_NEAR_MARGIN)

    # 3. Rebound strength: where close lies in today's range
    # Perfect = close at high (full recovery). Bad = close at low.
    today_range = today_high - today_low
    if today_range > 0:
        rebound_strength = (today_close - today_low) / today_range
    else:
        rebound_strength = 0.5  # doji-like

    # 4. Vol contraction: 1 - (today_vol / vol_ma5)
    # Capped at [0, 1]. 0.5 means today vol = half of 5-day avg.
    vol_contraction = max(0.0, min(1.0, 1.0 - today_vol / vol_ma5))

    # 5. Cross freshness: more recent cross = stronger setup
    days_since_cross = (on_date_ts - cross_date).days
    cross_freshness = max(0.0, 1.0 - days_since_cross / MAX_DAYS_SINCE_CROSS)

    confidence = (
        W_TRIGGER * trigger_w
        + W_TOUCH_PRECISION * touch_precision
        + W_REBOUND_STRENGTH * rebound_strength
        + W_VOL_CONTRACTION * vol_contraction
        + W_CROSS_FRESHNESS * cross_freshness
    )

    return {
        "ts_code": today["ts_code"],
        "trigger_type": trigger_type,
        "cross_date": cross_date.date() if hasattr(cross_date, "date") else cross_date,
        "touch_count": n_total_touches,
        "confidence_score": float(confidence),
        "components": {
            "trigger_w": float(trigger_w),
            "touch_precision": float(touch_precision),
            "rebound_strength": float(rebound_strength),
            "vol_contraction": float(vol_contraction),
            "cross_freshness": float(cross_freshness),
        },
        "signal_meta": {
            "today_close": today_close,
            "today_low": today_low,
            "today_high": today_high,
            "today_open": float(today["open"]),
            "today_vol": today_vol,
            "vol_ma5": vol_ma5,
            "ma5": ma5,
            "ma24": ma24,
            "cross_date": str(cross_date.date() if hasattr(cross_date, "date") else cross_date),
            "days_since_cross": int(days_since_cross),
            "touch_count": int(n_total_touches),
        },
    }


def detect_signals(universe_df: pd.DataFrame, on_date: dt.date) -> pd.DataFrame:
    """Detect 神枪手 signals across the universe on on_date.

    Args:
        universe_df: output of `data.load_universe`, must cover ≥ MIN_LOOKBACK_BARS
                     trading days before on_date (we recommend lookback_days=120 cal days)
        on_date: trading date being screened

    Returns:
        DataFrame ordered by confidence_score desc, columns:
            ts_code, trigger_type, cross_date, touch_count,
            confidence_score, components (dict), signal_meta (dict)
        Empty if no candidates.
    """
    if universe_df.empty:
        return pd.DataFrame()

    df = universe_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    on_date_ts = pd.to_datetime(on_date)
    counts = df[df["trade_date"] <= on_date_ts].groupby("ts_code").size()
    eligible_codes = counts[counts >= MIN_LOOKBACK_BARS].index
    df = df[df["ts_code"].isin(eligible_codes)]

    # ── Vectorized pre-filter to cut work ──────────────────────────────
    # Only keep stocks where today's close > today's recent average
    # (cheap proxy for "MA5 > MA24 today")
    today_df = df[df["trade_date"] == on_date_ts]
    if today_df.empty:
        return pd.DataFrame()

    # Quick filter: today's close in upper half of last 30 days
    recent = df[df["trade_date"] > on_date_ts - pd.Timedelta(days=45)]
    quick_stats = recent.groupby("ts_code")["close"].agg(["min", "max", "median"])
    today_idx = today_df.set_index("ts_code")["close"]
    common = today_idx.index.intersection(quick_stats.index)
    upper_half = today_idx[common] > quick_stats.loc[common, "median"]
    candidate_codes = upper_half[upper_half].index.tolist()
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

    # Cast cross_date to date for consistent typing
    out = pd.DataFrame(results)
    return out.sort_values("confidence_score", ascending=False).reset_index(drop=True)
