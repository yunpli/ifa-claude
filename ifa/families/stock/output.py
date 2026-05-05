"""Stock Edge output path helpers.

All generated artifacts must live under the configured iFA environment root,
normally `/Users/neoclaw/claude/ifaenv/out`. The repo should contain source,
tests, and docs only.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from ifa.config import Settings
REPORT_FAMILY = "stock_edge"
_NESTED_RUN_MODES = {"production", "manual"}


def output_dir_for_stock_edge(settings: Settings, report_date: dt.date, *, run_mode: str | None = None) -> Path:
    """Return the report output directory for Stock Edge.

    Manual/production:
        <settings.output_root>/<run_mode>/<YYYYMMDD>/stock_edge/

    Test:
        <settings.output_root>/test/
    """
    mode = run_mode or settings.run_mode.value
    base = Path(settings.output_root) / mode
    if mode in _NESTED_RUN_MODES:
        out = base / report_date.strftime("%Y%m%d") / REPORT_FAMILY
    else:
        out = base
    out.mkdir(parents=True, exist_ok=True)
    return out
