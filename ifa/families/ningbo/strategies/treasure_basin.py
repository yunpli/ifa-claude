"""聚宝盆 — 两阳夹一阴 K 线组合买入.

Pattern: 3-day K-line sequence (T-2, T-1, T0):
    T-2: 阳线 (close > open)
    T-1: 小阴线 or 小阳线 (|close - open| < threshold)
    T0:  阳线 (close > open) AND close > T-2.close

Volume confirmation:
    T-1 vol < T-2 vol  (回调缩量)
    T0  vol > T-1 vol  (上涨放量)

Best on top of 神枪手 setup (price near MA24 support after cross).

Phase 1.5 — to be implemented (Opus).
"""
from __future__ import annotations

import pandas as pd

STRATEGY_NAME = "treasure_basin"


def detect_signals(universe_df: pd.DataFrame, on_date) -> pd.DataFrame:
    """Detect 聚宝盆 K-line patterns ending on on_date.

    Returns DataFrame:
        ts_code, pattern_quality (0-1),
        vol_pattern_match (bool),
        on_sniper_setup (bool, whether this overlaps with sniper signal),
        signal_meta (dict with 3-day OHLCV snapshot)
    """
    raise NotImplementedError("Phase 1.5 — Opus")
