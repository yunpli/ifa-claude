"""Position alerts: stop-loss (跌破生命线) and take-profit (≥+20%).

Run daily after tracking batch updates outcomes.

Two alert types:
    - stop_loss:    today's close < MA24 (生命线), within 15-day tracking window
    - take_profit:  cum_return reached +20% within tracking window

Both alerts are surfaced in the daily report's "持仓警报" section.

Phase 1.9 — to be implemented (Sonnet).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Engine

TAKE_PROFIT_THRESHOLD = 0.20  # +20%
STOP_LOSS_RULE = "below_ma24"


def detect_today_alerts(engine: Engine, on_date: dt.date) -> dict:
    """Find recommendations that newly triggered stop-loss or take-profit today.

    Returns dict:
        {
            'stop_loss': [{rec_date, ts_code, strategy, scoring_mode, ...}],
            'take_profit': [{rec_date, ts_code, strategy, scoring_mode, ...}],
        }
    """
    raise NotImplementedError("Phase 1.9")
