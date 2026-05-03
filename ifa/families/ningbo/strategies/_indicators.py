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

    K = EWM(RSV, alpha=1/m1), D = EWM(K, alpha=1/m2), J = 3K - 2D
    RSV = (close - lowest_low_n) / (highest_high_n - lowest_low_n) * 100

    Chinese-convention SMA(x, m) ≡ EWM(x, alpha=1/m, adjust=False),
    so we replace the original Python row-loop with vectorized pandas EWM.

    New columns: 'kdj_k', 'kdj_d', 'kdj_j'.
    """
    low_n = df["low"].rolling(window=n, min_periods=n).min()
    high_n = df["high"].rolling(window=n, min_periods=n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n) * 100
    rsv = rsv.replace([np.inf, -np.inf], np.nan).fillna(50)
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
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


# Sentinel columns: if ALL present, indicators are already computed
_INDICATOR_SENTINEL_COLS = frozenset({"close_ma5", "close_ma24", "macd_dif", "kdj_k", "rsi6", "wr5"})


# ─── Convenience: enrich all (per-stock, used by Phase 1 evening report) ─────

def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all 5 indicator categories needed by 选股六步曲.

    Input must be single-stock OHLCV DataFrame ordered by trade_date asc.
    If indicator columns already present (pre-computed via
    compute_all_indicators_bulk), returns df unchanged — no-op.
    """
    if _INDICATOR_SENTINEL_COLS.issubset(df.columns):
        return df  # already enriched by bulk pre-computation
    df = df.copy()
    add_ma(df, col="close", windows=(5, 10, 20, 24, 60))
    add_ma(df, col="vol", windows=(5, 10, 20, 60))
    add_macd(df)
    add_kdj(df)
    add_rsi(df, windows=(6, 14))
    add_wr(df, windows=(5, 14, 55))
    return df


# ─── Bulk vectorized: compute all indicators for ALL stocks at once ───────────

def compute_all_indicators_bulk(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators for ALL stocks and ALL dates in one vectorized pass.

    Uses groupby.transform() so each indicator is computed across all stocks
    simultaneously — far faster than calling enrich_indicators() per stock.
    The result has the same shape as the input with indicator columns appended.

    Key optimizations vs. per-stock loop:
      - No Python-level for loop over 5,500 stock groups
      - KDJ uses vectorized EWM (add_kdj already vectorized)
      - All rolling/EWM ops dispatched to numpy/Cython via pandas internals
    """
    df = df.sort_values(["ts_code", "trade_date"]).copy()
    g = df.groupby("ts_code", group_keys=False)

    # ── Price MAs ─────────────────────────────────────────────────────────────
    for w in (5, 10, 20, 24, 60):
        df[f"close_ma{w}"] = g["close"].transform(
            lambda x, w=w: x.rolling(w, min_periods=w).mean()
        )

    # ── Volume MAs ────────────────────────────────────────────────────────────
    for w in (5, 10, 20, 60):
        df[f"vol_ma{w}"] = g["vol"].transform(
            lambda x, w=w: x.rolling(w, min_periods=w).mean()
        )

    # ── MACD ──────────────────────────────────────────────────────────────────
    df["macd_dif"] = g["close"].transform(
        lambda x: x.ewm(span=12, adjust=False).mean() - x.ewm(span=26, adjust=False).mean()
    )
    df["macd_dea"] = g["macd_dif"].transform(
        lambda x: x.ewm(span=9, adjust=False).mean()
    )
    df["macd_hist"] = df["macd_dif"] - df["macd_dea"]

    # ── KDJ (vectorized EWM per group) ────────────────────────────────────────
    low9  = g["low"].transform(lambda x: x.rolling(9, min_periods=9).min())
    high9 = g["high"].transform(lambda x: x.rolling(9, min_periods=9).max())
    rsv = ((df["close"] - low9) / (high9 - low9) * 100).replace([np.inf, -np.inf], np.nan).fillna(50)
    df["_rsv_tmp"] = rsv
    df["kdj_k"] = g["_rsv_tmp"].transform(lambda x: x.ewm(alpha=1 / 3, adjust=False).mean())
    df["kdj_d"] = g["kdj_k"].transform(lambda x: x.ewm(alpha=1 / 3, adjust=False).mean())
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]
    df.drop(columns=["_rsv_tmp"], inplace=True)

    # ── RSI ───────────────────────────────────────────────────────────────────
    def _rsi_transform(x: pd.Series, w: int) -> pd.Series:
        delta = x.diff()
        avg_gain = delta.clip(lower=0).ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
        avg_loss = (-delta).clip(lower=0).ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - 100 / (1 + rs)

    for w in (6, 14):
        df[f"rsi{w}"] = g["close"].transform(lambda x, w=w: _rsi_transform(x, w))

    # ── Williams %R ───────────────────────────────────────────────────────────
    for w in (5, 14, 55):
        low_w  = g["low"].transform(lambda x, w=w: x.rolling(w, min_periods=w).min())
        high_w = g["high"].transform(lambda x, w=w: x.rolling(w, min_periods=w).max())
        wr = (df["close"] - low_w) / (high_w - low_w) * 100
        df[f"wr{w}"] = wr.replace([np.inf, -np.inf], np.nan)

    return df
