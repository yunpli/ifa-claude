"""Technical indicators implemented in pure pandas.

All functions take a single-stock DataFrame ordered by trade_date asc
and return a DataFrame with new indicator columns appended.

We implement these directly (vs. pandas-ta or ta libs) because:
  - pandas-ta is unmaintained (numpy<2 conflict)
  - These 6 indicators are textbook, no edge cases worth abstracting
  - Direct control over edge handling (NaN propagation, lookback)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ─── 1. Moving Averages (price + volume) ─────────────────────────────────────

def add_ma(df: pd.DataFrame, col: str = "close", windows=(5, 10, 20, 24, 60)) -> pd.DataFrame:
    """Add simple moving averages of `col` for each window.

    New columns: '{col}_ma{w}' for each w in windows.
    """
    for w in windows:
        df[f"{col}_ma{w}"] = df[col].rolling(window=w, min_periods=w).mean()
    return df


# ─── 2. MACD ─────────────────────────────────────────────────────────────────

def add_macd(df: pd.DataFrame, col: str = "close",
             fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Add MACD: DIF (fast EMA - slow EMA), DEA (signal EMA of DIF), HIST (DIF - DEA).

    New columns: 'macd_dif', 'macd_dea', 'macd_hist'.
    """
    ema_fast = df[col].ewm(span=fast, adjust=False).mean()
    ema_slow = df[col].ewm(span=slow, adjust=False).mean()
    df["macd_dif"] = ema_fast - ema_slow
    df["macd_dea"] = df["macd_dif"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd_dif"] - df["macd_dea"]
    return df


# ─── 3. KDJ ──────────────────────────────────────────────────────────────────

def add_kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
    """Add KDJ stochastic indicator.

    K = SMA(RSV, m1), D = SMA(K, m2), J = 3K - 2D
    RSV = (close - lowest_low_n) / (highest_high_n - lowest_low_n) * 100

    New columns: 'kdj_k', 'kdj_d', 'kdj_j'.
    """
    low_n = df["low"].rolling(window=n, min_periods=n).min()
    high_n = df["high"].rolling(window=n, min_periods=n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n) * 100
    rsv = rsv.replace([np.inf, -np.inf], np.nan).fillna(50)

    # Chinese-convention KDJ: SMA with weight 1/m of new + (m-1)/m of prev
    k = pd.Series(index=df.index, dtype=float)
    d = pd.Series(index=df.index, dtype=float)
    k_prev = 50.0
    d_prev = 50.0
    for i, r in enumerate(rsv):
        k_cur = (1 / m1) * r + ((m1 - 1) / m1) * k_prev if not np.isnan(r) else np.nan
        d_cur = (1 / m2) * k_cur + ((m2 - 1) / m2) * d_prev if not np.isnan(k_cur) else np.nan
        k.iloc[i] = k_cur
        d.iloc[i] = d_cur
        if not np.isnan(k_cur):
            k_prev = k_cur
        if not np.isnan(d_cur):
            d_prev = d_cur
    df["kdj_k"] = k
    df["kdj_d"] = d
    df["kdj_j"] = 3 * k - 2 * d
    return df


# ─── 4. RSI ──────────────────────────────────────────────────────────────────

def add_rsi(df: pd.DataFrame, col: str = "close", windows=(6, 14)) -> pd.DataFrame:
    """Add RSI for each window.

    RSI = 100 - 100 / (1 + RS), RS = avg_gain / avg_loss
    Uses Wilder's smoothing (EMA with alpha = 1/n).

    New columns: 'rsi{w}' for each w in windows.
    """
    delta = df[col].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    for w in windows:
        # Wilder's smoothing
        avg_gain = gain.ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
        avg_loss = loss.ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df[f"rsi{w}"] = 100 - 100 / (1 + rs)
    return df


# ─── 5. Williams %R (normalized 0-100) ───────────────────────────────────────

def add_wr(df: pd.DataFrame, windows=(5, 14, 55)) -> pd.DataFrame:
    """Add Williams %R, normalized to 0-100 (high = strong, low = weak).

    WR_norm(n) = 100 * (close - lowest_low_n) / (highest_high_n - lowest_low_n)

    Range: 0 (price at n-day low) to 100 (price at n-day high).
    > 80 = strongly overbought, < 20 = strongly oversold.

    New columns: 'wr{w}' for each w in windows.
    """
    for w in windows:
        low_w = df["low"].rolling(window=w, min_periods=w).min()
        high_w = df["high"].rolling(window=w, min_periods=w).max()
        wr = (df["close"] - low_w) / (high_w - low_w) * 100
        df[f"wr{w}"] = wr.replace([np.inf, -np.inf], np.nan)
    return df


# ─── Convenience: enrich all ─────────────────────────────────────────────────

def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all 5 indicator categories needed by 选股六步曲.

    Input must be single-stock OHLCV DataFrame ordered by trade_date asc.
    """
    df = df.copy()
    add_ma(df, col="close", windows=(5, 10, 20, 24, 60))
    add_ma(df, col="vol", windows=(5, 10, 20, 60))
    add_macd(df)
    add_kdj(df)
    add_rsi(df, windows=(6, 14))
    add_wr(df, windows=(5, 14, 55))
    return df
