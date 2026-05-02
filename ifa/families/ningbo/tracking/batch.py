"""Daily tracking batch — update tracking + outcomes for in-progress recs.

Runs as part of the evening report pipeline (or as standalone backfill).

Logic:
    1. Find all recommendations in `recommendation_outcomes` with status='in_progress'
       AND track_day_count < 15
    2. For each, fetch today's close + MA24 from raw_daily
    3. Compute cum_return, write a new row in `recommendation_tracking`
    4. Update `recommendation_outcomes`:
       - If close < MA24 → status='stop_loss', terminal
       - If cum_return >= 0.20 → status='take_profit', terminal
       - If track_day == 15 → status='expired', terminal
       - Else → status='in_progress', update peak/trough cum_return

Phase 1.10 — to be implemented (Sonnet).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Engine

TRACKING_WINDOW_DAYS = 15
TAKE_PROFIT = 0.20


def run_tracking_batch(engine: Engine, on_date: dt.date) -> dict:
    """Update tracking + outcomes for all in-progress recommendations.

    Returns summary:
        {
            'tracked_recs': int,
            'newly_stop_loss': int,
            'newly_take_profit': int,
            'newly_expired': int,
            'still_in_progress': int,
        }
    """
    raise NotImplementedError("Phase 1.10")
