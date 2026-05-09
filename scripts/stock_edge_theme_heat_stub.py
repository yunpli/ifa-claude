#!/usr/bin/env python3
"""Create or inspect Stock Edge weekly theme heat stub rows.

This is a cache/backfill interface, not the final LLM extractor.  It lets the
sector-cycle strategy join a stable weekly feature table while avoiding per-row
LLM calls during proxy/replay validation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

from ifa.core.db import get_engine
from ifa.families.stock.theme_heat import default_stub_themes, load_weekly_theme_heat, upsert_weekly_theme_heat, week_start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True, help="Any date in the target week, YYYY-MM-DD.")
    parser.add_argument("--write", action="store_true", help="Upsert five explicit stub rows into stock.theme_heat_weekly.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a short text summary.")
    args = parser.parse_args()

    week = week_start(dt.date.fromisoformat(args.week))
    engine = get_engine()
    if args.write:
        rows = default_stub_themes(week)
        n = upsert_weekly_theme_heat(engine, rows)
        payload = {"status": "written", "valid_week": week.isoformat(), "rows": n, "quality_flag": "stub"}
    else:
        rows = load_weekly_theme_heat(engine, week)
        payload = {"status": "read", "valid_week": week.isoformat(), "rows": len(rows), "themes": rows}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    else:
        print(f"{payload['status']} week={payload['valid_week']} rows={payload['rows']}")
        if not args.write:
            for row in payload.get("themes", []):
                print(f"  {row['theme_rank']}. {row['theme_label']} [{row['category']}] heat={row['heat_score']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
