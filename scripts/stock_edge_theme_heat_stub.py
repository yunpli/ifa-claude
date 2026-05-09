#!/usr/bin/env python3
"""Create, inspect, or build Stock Edge weekly theme heat cache rows.

This is a cache/backfill interface, not an online LLM extractor.  It lets the
sector-cycle strategy join a stable weekly feature table while avoiding per-row
LLM calls during proxy/replay validation.  Approved JSON ingestion remains the
fallback when local structured sources are insufficient.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

from ifa.core.db import get_engine
from pathlib import Path

from ifa.families.stock.theme_heat import (
    WeeklyThemeHeat,
    build_weekly_theme_heat_from_local_sources,
    default_stub_themes,
    load_weekly_theme_heat,
    upsert_weekly_theme_heat,
    week_start,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True, help="Any date in the target week, YYYY-MM-DD.")
    parser.add_argument("--write", action="store_true", help="Upsert five explicit stub rows into stock.theme_heat_weekly.")
    parser.add_argument("--from-json", "--input-json", dest="input_json", type=Path, help="Upsert operator/LLM-batch theme rows from one JSON file; no per-row LLM calls.")
    parser.add_argument("--build-local", action="store_true", help="Build non-stub rows from existing local event/report memory tables only.")
    parser.add_argument("--dry-run", action="store_true", help="For --build-local/--from-json, validate and print rows without DB writes.")
    parser.add_argument("--min-source-rows", type=int, default=3, help="Minimum local source rows required for --build-local.")
    parser.add_argument("--run-mode", default="manual", help="Run mode to store for generated/cache rows.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a short text summary.")
    args = parser.parse_args()

    week = week_start(dt.date.fromisoformat(args.week))
    engine = get_engine()
    if args.input_json:
        rows = _load_theme_rows(args.input_json, week, args.run_mode)
        n = 0 if args.dry_run else upsert_weekly_theme_heat(engine, rows)
        payload = {"status": "dry_run" if args.dry_run else "written", "valid_week": week.isoformat(), "rows": len(rows) if args.dry_run else n, "quality_flag": "cache", "themes": rows}
    elif args.build_local:
        built = build_weekly_theme_heat_from_local_sources(
            engine,
            week,
            min_source_rows=args.min_source_rows,
            run_mode=args.run_mode,
        )
        if built["status"] != "ready":
            payload = built
        else:
            rows = built["rows"]
            n = 0 if args.dry_run else upsert_weekly_theme_heat(engine, rows)
            payload = {
                "status": "dry_run" if args.dry_run else "written",
                "valid_week": week.isoformat(),
                "rows": len(rows) if args.dry_run else n,
                "quality_flag": "local_source_cache",
                "source_policy": built["source_policy"],
                "source_rows": built["source_rows"],
                "themes": rows,
            }
    elif args.write:
        rows = default_stub_themes(week)
        n = upsert_weekly_theme_heat(engine, rows)
        payload = {"status": "written", "valid_week": week.isoformat(), "rows": n, "quality_flag": "stub"}
    else:
        rows = load_weekly_theme_heat(engine, week)
        payload = {"status": "read", "valid_week": week.isoformat(), "rows": len(rows), "themes": rows}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    else:
        print(f"{payload['status']} week={payload['valid_week']} rows={payload.get('rows', 0)}")
        if payload.get("status") == "blocked":
            print(f"  blocker={payload.get('reason')} source_rows={payload.get('source_rows')}/{payload.get('required_source_rows')}")
            print(f"  {payload.get('message') or 'Use --from-json with approved cached/manual rows.'}")
        if not args.write:
            for row in payload.get("themes", []):
                if isinstance(row, WeeklyThemeHeat):
                    print(f"  {row.theme_rank}. {row.theme_label} [{row.category}] heat={row.heat_score}")
                else:
                    print(f"  {row['theme_rank']}. {row['theme_label']} [{row['category']}] heat={row['heat_score']}")
    return 0


def _load_theme_rows(path: Path, week: dt.date, run_mode: str) -> list[WeeklyThemeHeat]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_rows = data.get("themes") if isinstance(data, dict) else data
    if not isinstance(raw_rows, list):
        raise ValueError("--input-json must be a list or an object with a themes list")
    rows: list[WeeklyThemeHeat] = []
    for idx, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"theme row #{idx} is not an object")
        rank = int(raw.get("theme_rank") or raw.get("rank") or idx)
        rows.append(
            WeeklyThemeHeat(
                valid_week=week,
                theme_rank=rank,
                theme_label=str(raw["theme_label"]),
                category=str(raw.get("category") or raw.get("theme_label")),
                heat_score=float(raw.get("heat_score", 0.0)),
                confidence=float(raw["confidence"]) if raw.get("confidence") is not None else None,
                affected_sectors=list(raw.get("affected_sectors") or raw.get("affected_sectors_json") or []),
                representative_stocks=list(raw.get("representative_stocks") or raw.get("representative_stocks_json") or []),
                source_urls=list(raw.get("source_urls") or raw.get("source_urls_json") or []),
                evidence=dict(raw.get("evidence") or raw.get("evidence_json") or {}),
                model_name=raw.get("model_name"),
                prompt_version=str(raw.get("prompt_version") or "stock_theme_heat_v1"),
                run_mode=run_mode,
                quality_flag=str(raw.get("quality_flag") or "cache"),
            )
        )
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
