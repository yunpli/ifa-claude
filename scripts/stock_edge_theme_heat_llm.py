#!/usr/bin/env python3
"""Run one Stock Edge daily/weekly LLM theme heat scan.

The command is batch-oriented by design: one daily or weekly LLM call produces a
bounded JSON object, then the result is cached locally.  It never makes
per-stock or per-news LLM calls.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.families.stock.theme_heat import (
    WeeklyThemeHeat,
    build_daily_theme_heat_with_llm,
    build_weekly_theme_heat_with_llm,
    daily_theme_heat_artifact_from_llm_response,
    upsert_weekly_theme_heat,
    week_start,
    weekly_theme_heat_rows_from_llm_response,
)


DEFAULT_ARTIFACT_ROOT = Path("/Users/neoclaw/claude/ifaenv/data/stock/theme_heat/llm")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Observation date YYYY-MM-DD.")
    parser.add_argument("--window", default="7d", help="Daily scan lookback window, e.g. 3d or 7d.")
    parser.add_argument("--cadence", choices=["daily", "weekly"], default="daily", help="Scan cadence.")
    parser.add_argument("--persist", action="store_true", help="Persist/cache parsed output. Weekly writes stock.theme_heat_weekly; daily writes a JSON artifact.")
    parser.add_argument("--dry-run", action="store_true", help="Preview prompt/schema only; no external LLM call and no DB/artifact write.")
    parser.add_argument("--from-json", type=Path, help="Ingest a previously reviewed LLM JSON response/artifact; no external LLM call.")
    parser.add_argument("--allow-llm-prior", action="store_true", help="Allow weak-evidence outputs flagged llm_prior_only/needs_local_evidence.")
    parser.add_argument("--source", choices=["local-cache", "tushare-cache", "all-cache"], default="all-cache", help="Local cached source bundle. Never performs online Tushare calls.")
    parser.add_argument("--min-source-rows", type=int, default=3, help="Minimum local evidence rows before strong cache quality is allowed.")
    parser.add_argument("--source-row-limit", type=int, default=300, help="Maximum cached source rows to include in the batch prompt.")
    parser.add_argument("--max-themes", type=int, default=None, help="Maximum themes in the JSON response. Defaults: daily=8, weekly=5.")
    parser.add_argument("--run-mode", default=None, help="manual | production | test; defaults to settings.run_mode.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ARTIFACT_ROOT, help="Directory for daily JSON artifacts and dry-run previews.")
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    args = parser.parse_args()

    settings = get_settings()
    engine = get_engine(settings)
    as_of = dt.date.fromisoformat(args.date)
    run_mode = args.run_mode or settings.run_mode.value
    max_themes = args.max_themes or (5 if args.cadence == "weekly" else 8)

    if args.from_json:
        payload = _ingest_from_json(
            args.from_json,
            engine=engine,
            as_of=as_of,
            cadence=args.cadence,
            window_days=_parse_window_days(args.window),
            run_mode=run_mode,
            max_themes=max_themes,
            persist=args.persist,
            output_dir=args.output_dir,
        )
    elif args.cadence == "weekly":
        payload = _run_weekly(
            engine,
            as_of=as_of,
            source=args.source,
            min_source_rows=args.min_source_rows,
            max_themes=max_themes,
            source_row_limit=args.source_row_limit,
            run_mode=run_mode,
            allow_llm_prior=args.allow_llm_prior,
            dry_run=args.dry_run,
            persist=args.persist,
        )
    else:
        payload = _run_daily(
            engine,
            as_of=as_of,
            window_days=_parse_window_days(args.window),
            source=args.source,
            min_source_rows=args.min_source_rows,
            max_themes=max_themes,
            source_row_limit=args.source_row_limit,
            run_mode=run_mode,
            allow_llm_prior=args.allow_llm_prior,
            dry_run=args.dry_run,
            persist=args.persist,
            output_dir=args.output_dir,
        )

    if args.json:
        print(json.dumps(_json_payload(payload), ensure_ascii=False, default=str, indent=2))
    else:
        _print_summary(payload)
    return 0


def _run_weekly(
    engine,
    *,
    as_of: dt.date,
    source: str,
    min_source_rows: int,
    max_themes: int,
    source_row_limit: int | None,
    run_mode: str,
    allow_llm_prior: bool,
    dry_run: bool,
    persist: bool,
) -> dict[str, Any]:
    built = build_weekly_theme_heat_with_llm(
        engine,
        as_of,
        source=source,  # type: ignore[arg-type]
        min_source_rows=min_source_rows,
        max_themes=max_themes,
        source_row_limit=source_row_limit,
        run_mode=run_mode,
        allow_llm_prior=allow_llm_prior,
        no_external=dry_run,
    )
    if built.get("status") == "ready":
        rows = built["rows"]
        written = upsert_weekly_theme_heat(engine, rows) if persist else 0
        built = dict(built)
        built["scan_type"] = "weekly"
        built["persisted_rows"] = written
        built["status"] = "written" if persist else "ready"
    return built


def _run_daily(
    engine,
    *,
    as_of: dt.date,
    window_days: int,
    source: str,
    min_source_rows: int,
    max_themes: int,
    source_row_limit: int | None,
    run_mode: str,
    allow_llm_prior: bool,
    dry_run: bool,
    persist: bool,
    output_dir: Path,
) -> dict[str, Any]:
    built = build_daily_theme_heat_with_llm(
        engine,
        as_of,
        window_days=window_days,
        source=source,  # type: ignore[arg-type]
        min_source_rows=min_source_rows,
        max_themes=max_themes,
        source_row_limit=source_row_limit,
        run_mode=run_mode,
        allow_llm_prior=allow_llm_prior,
        no_external=dry_run,
    )
    if built.get("status") in {"ready", "llm_dry_run"} and persist and not dry_run:
        path = _write_daily_artifact(built, output_dir=output_dir)
        built = dict(built)
        built["artifact_path"] = str(path)
        built["status"] = "written"
    return built


def _ingest_from_json(
    path: Path,
    *,
    engine,
    as_of: dt.date,
    cadence: str,
    window_days: int,
    run_mode: str,
    max_themes: int,
    persist: bool,
    output_dir: Path,
) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if cadence == "weekly":
        raw = data.get("themes") if isinstance(data, dict) else None
        if raw is None:
            raise ValueError("--from-json weekly payload must contain themes")
        rows = weekly_theme_heat_rows_from_llm_response(
            {"themes": raw},
            week=week_start(as_of),
            run_mode=run_mode,
            model_name=str(data.get("model_name") or "operator-reviewed-json") if isinstance(data, dict) else "operator-reviewed-json",
            source_rows=[],
            evidence_quality=str(data.get("evidence_quality") or "needs_local_evidence") if isinstance(data, dict) else "needs_local_evidence",
            max_themes=max_themes,
        )
        written = upsert_weekly_theme_heat(engine, rows) if persist else 0
        return {
            "status": "written" if persist else "ready",
            "scan_type": "weekly",
            "valid_week": week_start(as_of).isoformat(),
            "rows": len(rows),
            "persisted_rows": written,
            "themes": rows,
        }
    artifact = data if isinstance(data, dict) and data.get("scan_type") == "daily" else daily_theme_heat_artifact_from_llm_response(
        data,
        as_of=as_of,
        window_days=window_days,
        run_mode=run_mode,
        model_name=str(data.get("model_name") or "operator-reviewed-json") if isinstance(data, dict) else "operator-reviewed-json",
        endpoint=str(data.get("endpoint") or "from_json") if isinstance(data, dict) else "from_json",
        source=str(data.get("source") or "all-cache") if isinstance(data, dict) else "all-cache",  # type: ignore[arg-type]
        source_rows=[],
        evidence_quality=str(data.get("evidence_quality") or "needs_local_evidence") if isinstance(data, dict) else "needs_local_evidence",
        max_themes=max_themes,
    )
    if persist:
        artifact = dict(artifact)
        artifact["artifact_path"] = str(_write_daily_artifact(artifact, output_dir=output_dir))
        artifact["status"] = "written"
    return artifact


def _write_daily_artifact(payload: dict[str, Any], *, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    as_of = str(payload.get("as_of") or "unknown").replace("-", "")
    window = str(payload.get("window_days") or "x")
    path = output_dir / f"stock_theme_heat_daily_{as_of}_{window}d.json"
    path.write_text(json.dumps(_json_payload(payload), ensure_ascii=False, default=str, indent=2) + "\n", encoding="utf-8")
    return path


def _parse_window_days(raw: str) -> int:
    value = raw.strip().lower()
    if value.endswith("d"):
        value = value[:-1]
    days = int(value)
    if days <= 0:
        raise ValueError("--window must be positive, e.g. 7d")
    return days


def _json_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    if "rows" in out and isinstance(out["rows"], list):
        out["rows"] = [asdict(row) if is_dataclass(row) else row for row in out["rows"]]
    if "themes" in out and isinstance(out["themes"], list):
        out["themes"] = [asdict(row) if is_dataclass(row) else row for row in out["themes"]]
    return out


def _print_summary(payload: dict[str, Any]) -> None:
    status = payload.get("status")
    scan_type = payload.get("scan_type") or payload.get("status")
    date_value = payload.get("as_of") or payload.get("valid_week")
    count = len(payload.get("themes") or payload.get("rows") or [])
    print(f"{status} scan={scan_type} date={date_value} themes={count}")
    if payload.get("artifact_path"):
        print(f"  artifact={payload['artifact_path']}")
    if payload.get("persisted_rows"):
        print(f"  persisted_rows={payload['persisted_rows']}")
    if status == "blocked":
        print(f"  blocker={payload.get('reason')} source_rows={payload.get('source_rows')}/{payload.get('required_source_rows')}")
    for row in (payload.get("themes") or payload.get("rows") or [])[:10]:
        if isinstance(row, WeeklyThemeHeat):
            print(f"  {row.theme_rank}. {row.theme_label} heat={row.heat_score} quality={row.quality_flag}")
        elif isinstance(row, dict):
            print(f"  {row.get('theme_rank')}. {row.get('theme_label')} heat={row.get('heat_score')} quality={row.get('quality_flag')}")


if __name__ == "__main__":
    raise SystemExit(main())
