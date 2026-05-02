"""ningbo evening report — main pipeline.

Daily flow (16:30 BJT, after smartmoney ETL):

    1. load_universe (data.py)
    2. Run 4 strategies → candidate pools
    3. Apply scorer (heuristic; ml in Phase 3+)
    4. Top-5 selection per scoring_mode
    5. Multi-strategy resonance merge
    6. Write recommendations_daily
    7. Run tracking batch (update existing in-progress recs)
    8. Detect alerts (stop-loss / take-profit triggered today)
    9. LLM narrative for new recs
    10. Render HTML/PDF
    11. (Optional) Generate ML recommendations alongside (Phase 3+)

Phase 1.14 — to be implemented (Sonnet).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import Engine

SLOT = "evening"
REPORT_FAMILY = "ningbo"


def run_ningbo_evening(
    *,
    report_date: dt.date,
    data_cutoff_at: dt.datetime,
    user: str = "default",
    triggered_by: str = "manual",
    scoring_modes: tuple[str, ...] = ("heuristic",),  # Phase 3+ adds 'ml'
    on_log=lambda m: None,
) -> Path:
    """Run the ningbo evening report end-to-end.

    Returns the absolute path to the rendered HTML.
    """
    raise NotImplementedError("Phase 1.14")
