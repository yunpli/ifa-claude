"""神枪手 — 5/24 MA 回调买入策略.

Setup conditions (sequential):
    1. MA(5) crossed above MA(24) within last N days (设 N=20)
       AND price has stayed above MA(24) since then
    2. Today's low touched MA(24) (回调到生命线)
    3. Today's close >= MA(24) (站回生命线)
    4. Volume on the touch day < 5-day avg vol (缩量回调)

Two trigger types:
    - 神枪手出击 (first touch): N-th time touching MA(24) since cross, where N=1
    - 神枪手买入 (second touch): N=2, higher confidence

Phase 1.4 — to be implemented (Opus).
"""
from __future__ import annotations

import pandas as pd

STRATEGY_NAME = "sniper"


def detect_signals(universe_df: pd.DataFrame, on_date) -> pd.DataFrame:
    """Detect sniper signals on the given date.

    Returns DataFrame with one row per qualifying stock:
        ts_code, trigger_type ('strike_1' | 'strike_2'),
        cross_date (when MA5 crossed MA24),
        touch_count (how many times touched MA24 since cross),
        rebound_strength (0-1),
        signal_meta (dict)
    """
    raise NotImplementedError("Phase 1.4 — Opus")
