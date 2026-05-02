"""半年翻倍 — 中线大牛股捕捉策略.

Three simultaneous conditions on weekly + daily bars:

    1. 日线: MA(5) crossed above MA(24), price firmly above MA(24)
    2. 日线: VolMA(5) crossed above VolMA(60), VolMA(5) sloping up sharply
    3. 周线: MACD parallel up, just crossed 0-axis
       AND 周MA + 周MACD both 金叉

Filter: avoid stocks already up > 50% in last 3 months (chasing high).

Phase 1.6 — to be implemented (Opus).
"""
from __future__ import annotations

import pandas as pd

STRATEGY_NAME = "half_year_double"


def detect_signals(universe_df: pd.DataFrame, weekly_df: pd.DataFrame, on_date) -> pd.DataFrame:
    """Detect 半年翻倍 candidates on the given date.

    Returns DataFrame:
        ts_code,
        ma_cross_strength (days since 5/24 cross),
        vol_cross_strength (5/60 vol slope),
        macd_strength (DIF rising rate),
        weekly_macd_just_crossed_zero (bool),
        signal_meta (dict)
    """
    raise NotImplementedError("Phase 1.6 — Opus")
