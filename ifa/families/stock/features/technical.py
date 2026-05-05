"""Daily technical features for the Stock Edge rule baseline."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TechnicalSummary:
    close: float
    prev_close: float | None
    return_5d_pct: float | None
    ma5: float | None
    ma20: float | None
    ma60: float | None
    atr14: float | None
    avg_amount_7d_yuan: float | None
    trend_label: str


def compute_technical_summary(daily_bars: pd.DataFrame) -> TechnicalSummary:
    if daily_bars.empty:
        raise ValueError("daily_bars is empty.")
    df = daily_bars.sort_values("trade_date").reset_index(drop=True).copy()
    close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else None

    ma5 = _ma(df, 5)
    ma20 = _ma(df, 20)
    ma60 = _ma(df, 60)
    atr14 = _atr(df, 14)
    avg_amount = _avg_amount_yuan(df, 7)
    return_5d = None
    if len(df) >= 6 and float(df["close"].iloc[-6]) != 0:
        return_5d = (close / float(df["close"].iloc[-6]) - 1.0) * 100.0

    if ma20 is not None and ma60 is not None and close > ma20 > ma60:
        trend = "uptrend"
    elif ma20 is not None and close > ma20:
        trend = "recovery"
    elif ma20 is not None and close < ma20:
        trend = "weak"
    else:
        trend = "insufficient_history"

    return TechnicalSummary(
        close=close,
        prev_close=prev_close,
        return_5d_pct=return_5d,
        ma5=ma5,
        ma20=ma20,
        ma60=ma60,
        atr14=atr14,
        avg_amount_7d_yuan=avg_amount,
        trend_label=trend,
    )


def _ma(df: pd.DataFrame, window: int) -> float | None:
    if len(df) < window:
        return None
    return float(df["close"].tail(window).mean())


def _avg_amount_yuan(df: pd.DataFrame, window: int) -> float | None:
    if "amount" not in df.columns or df.empty:
        return None
    # TuShare daily.amount is normally in thousand yuan.
    return float(df["amount"].tail(min(window, len(df))).mean()) * 1000.0


def _atr(df: pd.DataFrame, window: int) -> float | None:
    if len(df) < window or not {"high", "low", "close"}.issubset(df.columns):
        return None
    tail = df.tail(window).copy()
    prev_close = df["close"].shift(1).tail(window)
    tr = pd.concat(
        [
            tail["high"] - tail["low"],
            (tail["high"] - prev_close).abs(),
            (tail["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(tr.mean())
