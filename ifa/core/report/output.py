"""Output-path resolution for rendered reports (V2.1.3+).

Layout depends on run_mode:

    production / manual:
        <output_root>/<run_mode>/<YYYYMMDD>/<family_folder>/CN_<family>_<slot>_<...>.html
        — nested by report-date and family so a coordinated multi-family
        run for a given trading day collects in one place.

    test:
        <output_root>/test/CN_<family>_<slot>_<...>.html
        — flat layout preserved; test runs are ad-hoc development noise
        and don't need the per-day grouping ceremony.

Where:
    output_root      = settings.output_root  (typically ~/claude/ifaenv/out)
    run_mode         = test | manual | production
    YYYYMMDD         = report_date (Beijing date the report covers)
    family_folder    = market | macro | asset | tech | smartmoney
                       (note: internal report_family='main' is rendered as
                       'market' to match user-facing CLI naming)
"""
from __future__ import annotations

from pathlib import Path

from .run import ReportRun

# Map internal report_family → user-facing folder name. Anything not listed
# uses report_family unchanged.
_FAMILY_FOLDER_OVERRIDE: dict[str, str] = {
    "main": "market",
}

# Run modes that get the nested <YYYYMMDD>/<family>/ layout.
_NESTED_RUN_MODES: set[str] = {"production", "manual"}


def _family_folder(report_family: str) -> str:
    return _FAMILY_FOLDER_OVERRIDE.get(report_family, report_family)


def output_dir_for_run(settings, run: ReportRun) -> Path:
    """Compute (and create) the output directory for a render.

    For production/manual: <root>/<mode>/<YYYYMMDD>/<family>/
    For test:              <root>/test/  (flat — no per-day/family nesting)
    """
    base = Path(settings.output_root) / run.run_mode.value
    if run.run_mode.value in _NESTED_RUN_MODES:
        out = (
            base
            / run.report_date.strftime("%Y%m%d")
            / _family_folder(run.report_family)
        )
    else:
        out = base
    out.mkdir(parents=True, exist_ok=True)
    return out
