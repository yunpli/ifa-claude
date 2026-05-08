#!/usr/bin/env python
"""End-to-end Stock Edge panel-based coarse tuning.

This is the production-aligned tuner: it builds a PIT replay panel by running the
real `compute_strategy_matrix` over (universe × dates), caches signals to parquet,
then runs random search over `decision_layer.horizons.*` weights/thresholds —
the actually load-bearing params for 5/10/20 decisions.

Usage:
    uv run python scripts/stock_edge_panel_tune.py \
        --as-of 2026-03-31 --top 50 --pit-samples 8 \
        --max-candidates 256 --workers -1 [--dry-run]

The legacy `scripts/stock_edge_global_preset.py` runs a SURROGATE optimizer
(unrelated to compute_strategy_matrix) — do not use it for production tuning.
"""
from __future__ import annotations

import argparse
import builtins
import datetime as dt
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.families.stock.backtest.optimizer import (
    fit_global_preset_successive_halving,
    fit_global_preset_via_panel,
)
from ifa.families.stock.backtest.panel_evaluator import (
    bootstrap_rank_ic_lift,
    evaluate_overlay_on_panel,
    k_fold_rolling_walk_forward,
    kfold_aggregate_ci,
    panel_matrix_from_rows,
    regime_bucketed_rank_ic_lift,
    walk_forward_split,
)
from ifa.families.stock.backtest.promotion import auto_promote_if_passing, evaluate_promotion_gates
from ifa.families.stock.backtest.replay_panel import build_replay_panel
from ifa.families.stock.backtest.tuning_artifact import write_tuning_artifact
from ifa.families.stock.params import load_params


def print(*args, **kwargs) -> None:
    """Flush status lines promptly for ACP/background runs."""
    kwargs.setdefault("flush", True)
    builtins.print(*args, **kwargs)


def _select_universe(
    engine,
    *,
    top_n: int,
    lookback_days: int = 20,
    as_of: dt.date,
    liquidity_offset: int = 0,
) -> list[str]:
    """Top N by 20-day average daily turnover, ending at as_of.

    `liquidity_offset` makes out-of-cohort validation explicit: offset 0 is the
    production top-liquidity train cohort, offset 100 gives the next 100 names.
    """
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT ts_code, AVG(amount) AS avg_amount
            FROM smartmoney.raw_daily
            WHERE trade_date <= :as_of AND trade_date >= :start
            GROUP BY ts_code
            HAVING COUNT(*) >= :min_days
            ORDER BY avg_amount DESC
            LIMIT :n
            OFFSET :offset
        """), {
            "as_of": as_of,
            "start": as_of - dt.timedelta(days=lookback_days * 2),
            "min_days": int(lookback_days * 0.7),
            "n": top_n,
            "offset": liquidity_offset,
        }).all()
    return [r[0] for r in rows]


def _select_stratified_pit_universe(
    engine,
    *,
    top_n: int,
    as_of: dt.date,
    lookback_days: int = 60,
    pool_multiple: int = 8,
) -> tuple[list[str], dict[str, Any]]:
    """PIT-safe stratified universe using only rows <= as_of.

    Minimum viable stratification dimensions:
      1. liquidity bucket: 60d average amount
      2. market-cap bucket: latest total_mv
      3. SW L1 industry bucket

    Volatility is computed and recorded as a diagnostic dimension; it is not
    part of the hard partition in MVP1 because industry x liquidity x size x
    volatility can over-fragment small panels.
    """
    pool_n = max(top_n, top_n * max(2, pool_multiple))
    start = as_of - dt.timedelta(days=lookback_days * 2)
    snapshot_month = as_of.replace(day=1)
    with engine.connect() as c:
        rows = c.execute(text("""
            WITH daily_window AS (
                SELECT ts_code,
                       AVG(amount) AS avg_amount,
                       STDDEV_SAMP(pct_chg) AS vol_pct,
                       COUNT(*) AS n_days
                FROM smartmoney.raw_daily
                WHERE trade_date <= :as_of AND trade_date >= :start
                GROUP BY ts_code
                HAVING COUNT(*) >= :min_days
            ),
            latest_basic AS (
                SELECT DISTINCT ON (ts_code)
                       ts_code, total_mv, circ_mv
                FROM smartmoney.raw_daily_basic
                WHERE trade_date <= :as_of
                ORDER BY ts_code, trade_date DESC
            ),
            members AS (
                SELECT DISTINCT ON (ts_code)
                       ts_code, l1_code, l1_name, l2_code, l2_name
                FROM smartmoney.sw_member_monthly
                WHERE snapshot_month <= :snapshot_month
                ORDER BY ts_code, snapshot_month DESC
            ),
            ranked_pool AS (
                SELECT d.ts_code,
                       d.avg_amount,
                       d.vol_pct,
                       COALESCE(b.total_mv, b.circ_mv, 0) AS mv,
                       COALESCE(m.l1_code, 'UNKNOWN') AS l1_code,
                       COALESCE(m.l1_name, 'UNKNOWN') AS l1_name
                FROM daily_window d
                LEFT JOIN latest_basic b USING (ts_code)
                LEFT JOIN members m USING (ts_code)
                ORDER BY d.avg_amount DESC NULLS LAST
                LIMIT :pool_n
            ),
            bucketed AS (
                SELECT *,
                       NTILE(3) OVER (ORDER BY avg_amount NULLS FIRST) AS liquidity_bucket,
                       NTILE(3) OVER (ORDER BY mv NULLS FIRST) AS size_bucket,
                       NTILE(3) OVER (ORDER BY COALESCE(vol_pct, 0) NULLS FIRST) AS volatility_bucket
                FROM ranked_pool
            )
            SELECT ts_code, avg_amount, mv, vol_pct, l1_code, l1_name,
                   liquidity_bucket, size_bucket, volatility_bucket
            FROM bucketed
            ORDER BY l1_code, liquidity_bucket, size_bucket, md5(ts_code || :seed)
        """), {
            "as_of": as_of,
            "start": start,
            "snapshot_month": snapshot_month,
            "min_days": int(lookback_days * 0.55),
            "pool_n": pool_n,
            "seed": as_of.isoformat(),
        }).mappings().all()
    buckets: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row["l1_code"] or "UNKNOWN"), int(row["liquidity_bucket"] or 0), int(row["size_bucket"] or 0))
        buckets[key].append(dict(row))
    selected: list[dict[str, Any]] = []
    for level in range(max((len(v) for v in buckets.values()), default=0)):
        for key in sorted(buckets):
            items = buckets[key]
            if level < len(items):
                selected.append(items[level])
                if len(selected) >= top_n:
                    break
        if len(selected) >= top_n:
            break
    codes = [str(row["ts_code"]) for row in selected]
    meta = {
        "selection_as_of": as_of.isoformat(),
        "lookback_start": start.isoformat(),
        "lookback_days": lookback_days,
        "candidate_pool_rows": len(rows),
        "pool_multiple": pool_multiple,
        "selected_count": len(codes),
        "membership_hash": _digest_codes(codes),
        "dimensions": ["sw_l1_industry", "liquidity_bucket", "size_bucket", "volatility_bucket_diagnostic"],
        "sample_head": codes[:10],
        "strata_counts": _strata_counts(selected),
    }
    return codes, meta


def _strata_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "l1_code": dict(Counter(str(r.get("l1_code") or "UNKNOWN") for r in rows).most_common()),
        "liquidity_bucket": dict(Counter(str(r.get("liquidity_bucket") or "unknown") for r in rows).most_common()),
        "size_bucket": dict(Counter(str(r.get("size_bucket") or "unknown") for r in rows).most_common()),
        "volatility_bucket": dict(Counter(str(r.get("volatility_bucket") or "unknown") for r in rows).most_common()),
    }


def _digest_codes(codes: list[str]) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(codes, ensure_ascii=False).encode()).hexdigest()[:16]


def _select_pit_dates(
    engine,
    *,
    n_samples: int,
    latest_as_of: dt.date,
    forward_min_days: int = 25,
    lookback_days: int = 18 * 30,
) -> list[dt.date]:
    """Pick N trading days that have at least `forward_min_days` of future bars in DB.

    Strategy: take all SSE trading days between (latest - lookback_days) and (latest - forward_min_days * 1.5/business),
    sort descending, evenly sample.
    """
    horizon_days = max(35, int(forward_min_days * 1.5))
    end_max = latest_as_of - dt.timedelta(days=horizon_days)
    start = latest_as_of - dt.timedelta(days=max(30, lookback_days))
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT cal_date FROM smartmoney.trade_cal
            WHERE exchange = 'SSE' AND is_open = true
              AND cal_date >= :start AND cal_date <= :end
            ORDER BY cal_date DESC
        """), {"start": start, "end": end_max}).all()
    days = [r[0] for r in rows]
    if not days or len(days) < n_samples:
        return days
    # Even spacing across the window for regime diversity
    step = max(1, len(days) // n_samples)
    sampled = days[::step][:n_samples]
    return sorted(sampled)


def _cheap_proxy_rows(rows: list, *, max_rows: int, seed: str) -> list:
    """Deterministic diverse subset for cheap proxy search."""
    if max_rows <= 0 or len(rows) <= max_rows:
        return rows
    grouped: dict[tuple[str, object], list] = defaultdict(list)
    for row in rows:
        grouped[(row.regime or "unknown", row.as_of_date)].append(row)
    rng = random.Random(seed)
    for items in grouped.values():
        rng.shuffle(items)
    out = []
    level = 0
    keys = sorted(grouped, key=lambda item: (str(item[0]), str(item[1])))
    while len(out) < max_rows:
        added = False
        for key in keys:
            items = grouped[key]
            if level < len(items):
                out.append(items[level])
                added = True
                if len(out) >= max_rows:
                    break
        if not added:
            break
        level += 1
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Stock Edge panel-based coarse tuning")
    parser.add_argument("--as-of", default=None, help="Latest as_of trade date (default: auto)")
    parser.add_argument("--top", type=int, default=50, help="Top N by liquidity (default 50)")
    parser.add_argument("--liquidity-offset", type=int, default=0, help="Skip the top K liquidity names before selecting --top. Use for OOC cohorts, e.g. 100 = ranks 101..")
    parser.add_argument("--pit-samples", type=int, default=8, help="PIT trading days to sample (default 8)")
    parser.add_argument("--pit-lookback-days", type=int, default=18 * 30, help="Calendar lookback for PIT date sampling before forward-label cutoff (default ~18 months)")
    parser.add_argument("--max-candidates", type=int, default=256, help="Search candidates (default 256)")
    parser.add_argument("--workers", type=int, default=-1, help="Parallel workers (-1 = auto, default -1)")
    parser.add_argument("--panel-chunk-size", type=int, default=25, help="Max stocks per replay worker chunk (default 25)")
    parser.add_argument("--universe-id", default="top_liquidity", help="Cache key prefix")
    parser.add_argument(
        "--universe-mode",
        choices=("latest", "pit-local", "stratified-pit"),
        default="latest",
        help="Universe selection mode: latest = one top-liquidity cohort; pit-local = reselect top N; stratified-pit = PIT-safe liquidity/size/industry stratified sampling",
    )
    parser.add_argument("--stratified-pool-multiple", type=int, default=8, help="Candidate pool multiple for stratified-pit (default 8)")
    parser.add_argument("--diagnose-panel", action=argparse.BooleanOptionalAction, default=True, help="Write panel diagnosis artifact after panel build")
    parser.add_argument("--diagnosis-output-dir", default="/Users/neoclaw/claude/ifaenv/manifests/stock_edge_panel_diagnostics", help="Directory for panel diagnosis JSON artifacts")
    parser.add_argument("--two-stage", action="store_true", help="Use cheap proxy prefilter before expensive replay search")
    parser.add_argument("--proxy-candidates", type=int, default=128, help="Cheap proxy candidate budget in --two-stage mode")
    parser.add_argument("--proxy-max-rows", type=int, default=600, help="Max rows for cheap proxy subset in --two-stage mode")
    parser.add_argument("--include-llm", action="store_true", help="Include LLM signals (slower)")
    parser.add_argument("--dry-run", action="store_true", help="Build panel + tune but don't write artifact")
    parser.add_argument("--n-iterations", type=int, default=3, help="Search iterations (default 3)")
    parser.add_argument("--no-warmstart", action="store_true", help="Disable IC-derived warmstart")
    parser.add_argument("--no-negative-weights", action="store_true", help="Disable negative weights for inverted signals")
    parser.add_argument("--search-algo", choices=("random", "tpe"), default="random", help="Search algorithm (default 'random'; 'tpe' uses Optuna TPE sampler)")
    parser.add_argument("--successive-halving", action="store_true", help="Use 3-stage successive halving (broad → narrow → fine); ignores --n-iterations")
    parser.add_argument("--auto-promote", action="store_true", help="Apply gates; if passed, write YAML variant")
    parser.add_argument("--variant-output", default=None, help="Where to write YAML variant (default: side-by-side .variant.yaml; ignored if --apply-to-baseline)")
    parser.add_argument("--apply-to-baseline", action="store_true", help="Overwrite the base YAML directly (with .bak_<ts> backup); ideal for weekly cron")
    parser.add_argument("--base-yaml", default="ifa/families/stock/params/stock_edge_v2.2.yaml")
    parser.add_argument("--oos", action="store_true", help="Walk-forward OOS: tune on older half, gate on newer half")
    parser.add_argument("--train-fraction", type=float, default=0.5, help="OOS train fraction (default 0.5)")
    parser.add_argument("--embargo-days", type=int, default=10, help="OOS embargo days between train end and val start (default 10)")
    parser.add_argument("--k-fold", type=int, default=0, help="K-fold rolling walk-forward (default 0 = single split). Each fold tunes on growing train window, evaluates on next val_dates_per_fold dates")
    parser.add_argument("--val-dates-per-fold", type=int, default=2, help="Validation dates per fold (default 2)")
    parser.add_argument("--min-train-dates", type=int, default=4, help="Minimum train dates for first fold (default 4)")
    parser.add_argument("--k-fold-min-positive", type=int, default=0, help="G9 gate: minimum number of folds with positive val lift per horizon (default = ceil(0.75 * n_folds))")
    parser.add_argument("--bootstrap-iterations", type=int, default=1000, help="G5 gate: bootstrap iterations for CI (default 1000; 0 disables G5)")
    parser.add_argument("--bootstrap-confidence", type=float, default=0.95, help="G5 gate: confidence level (default 0.95)")
    parser.add_argument("--regime-min-bucket-pct", type=float, default=0.75, help="G4 gate: minimum fraction of regime buckets that must improve (default 0.75)")
    parser.add_argument("--regime-min-samples", type=int, default=30, help="G4 gate: minimum val rows per regime bucket (default 30)")
    args = parser.parse_args()

    engine = get_engine()
    if args.as_of:
        as_of = dt.date.fromisoformat(args.as_of)
    else:
        with engine.connect() as c:
            row = c.execute(text("SELECT MAX(trade_date) FROM smartmoney.raw_daily")).scalar()
        if row is None:
            print("ERROR: no raw_daily data; cannot infer as_of", file=sys.stderr)
            return 2
        as_of = row

    print(f"=== Stock Edge Panel Tune ===")
    print(f"  as_of:           {as_of}")
    print(f"  top N:           {args.top}")
    print(f"  liquidity offset:{args.liquidity_offset}")
    print(f"  universe mode:   {args.universe_mode}")
    print(f"  PIT samples:     {args.pit_samples}")
    print(f"  PIT lookback:    {args.pit_lookback_days} calendar days")
    print(f"  candidates:      {args.max_candidates}")
    print(f"  workers:         {args.workers if args.workers > 0 else os.cpu_count() - 1}")
    print(f"  skip_llm:        {not args.include_llm}")
    print(f"  dry_run:         {args.dry_run}")
    if args.dry_run:
        print("  safety:         DRY RUN - artifact will NOT be written; baseline YAML will NOT be touched")
    liquidity_offset = max(0, args.liquidity_offset)
    universe_label_base = f"{args.universe_id}_top{args.top}" if liquidity_offset == 0 else f"{args.universe_id}_top{args.top}_offset{liquidity_offset}"
    if args.universe_mode == "latest":
        universe_label = universe_label_base
    elif args.universe_mode == "pit-local":
        universe_label = f"{universe_label_base}_pitlocal"
    else:
        universe_label = f"{universe_label_base}_stratifiedpit"

    print(f"\n[1/4] Selecting PIT trading days...")
    t0 = time.monotonic()
    pit_dates = _select_pit_dates(
        engine,
        n_samples=args.pit_samples,
        latest_as_of=as_of,
        lookback_days=args.pit_lookback_days,
    )
    if len(pit_dates) < args.pit_samples:
        print(f"      WARN: only {len(pit_dates)} dates available")
    print(f"      dates: {[d.isoformat() for d in pit_dates]} ({time.monotonic()-t0:.1f}s)")

    print(f"\n[2/4] Selecting universe...")
    t0 = time.monotonic()
    ts_codes_by_date: dict[dt.date, list[str]] | None = None
    universe_selection: dict[str, object] = {
        "mode": args.universe_mode,
        "top_n": args.top,
        "liquidity_offset": liquidity_offset,
        "lookback_days": 20,
        "min_days_fraction": 0.7,
        "leakage_guard": (
            "latest mode selects once using rows <= as_of; pit-local mode selects each "
            "date using rows <= that PIT date; stratified-pit uses rows <= each PIT date "
            "and stratifies by liquidity, size, SW L1 industry, with volatility diagnostics"
        ),
    }
    if args.universe_mode == "latest":
        ts_codes = _select_universe(engine, top_n=args.top, as_of=as_of, liquidity_offset=liquidity_offset)
        universe_selection.update({
            "selection_as_of": as_of.isoformat(),
            "selected_count": len(ts_codes),
            "membership_hash": _digest_codes(ts_codes),
            "sample_head": ts_codes[:10],
        })
        print(f"      {len(ts_codes)} stocks selected as of {as_of} ({time.monotonic()-t0:.1f}s)")
    elif args.universe_mode == "pit-local":
        ts_codes_by_date = {}
        by_date_meta: dict[str, dict[str, object]] = {}
        union_codes: set[str] = set()
        for pit_date in pit_dates:
            codes = _select_universe(engine, top_n=args.top, as_of=pit_date, liquidity_offset=liquidity_offset)
            ts_codes_by_date[pit_date] = codes
            union_codes.update(codes)
            by_date_meta[pit_date.isoformat()] = {
                "selection_as_of": pit_date.isoformat(),
                "lookback_start": (pit_date - dt.timedelta(days=20 * 2)).isoformat(),
                "selected_count": len(codes),
                "membership_hash": _digest_codes(codes),
                "sample_head": codes[:10],
            }
        ts_codes = sorted(union_codes)
        universe_selection.update({
            "unique_stock_count": len(ts_codes),
            "date_count": len(pit_dates),
            "membership_hash": _digest_codes([
                f"{pit_date.isoformat()}:{','.join(ts_codes_by_date.get(pit_date, []))}"
                for pit_date in pit_dates
            ]),
            "by_date": by_date_meta,
        })
        min_n = min((len(v) for v in ts_codes_by_date.values()), default=0)
        max_n = max((len(v) for v in ts_codes_by_date.values()), default=0)
        print(
            f"      {len(ts_codes)} unique stocks across {len(pit_dates)} PIT-local cohorts "
            f"(per-date {min_n}..{max_n}, {time.monotonic()-t0:.1f}s)"
        )
    else:
        ts_codes_by_date = {}
        by_date_meta = {}
        union_codes = set()
        for pit_date in pit_dates:
            codes, meta = _select_stratified_pit_universe(
                engine,
                top_n=args.top,
                as_of=pit_date,
                pool_multiple=args.stratified_pool_multiple,
            )
            ts_codes_by_date[pit_date] = codes
            union_codes.update(codes)
            by_date_meta[pit_date.isoformat()] = meta
        ts_codes = sorted(union_codes)
        universe_selection.update({
            "unique_stock_count": len(ts_codes),
            "date_count": len(pit_dates),
            "membership_hash": _digest_codes([
                f"{pit_date.isoformat()}:{','.join(ts_codes_by_date.get(pit_date, []))}"
                for pit_date in pit_dates
            ]),
            "stratified_pool_multiple": args.stratified_pool_multiple,
            "stratification_dimensions": ["liquidity", "market_cap", "sw_l1_industry", "volatility_diagnostic"],
            "by_date": by_date_meta,
        })
        min_n = min((len(v) for v in ts_codes_by_date.values()), default=0)
        max_n = max((len(v) for v in ts_codes_by_date.values()), default=0)
        print(
            f"      {len(ts_codes)} unique stocks across {len(pit_dates)} stratified PIT cohorts "
            f"(per-date {min_n}..{max_n}, {time.monotonic()-t0:.1f}s)"
        )

    requested_rows = sum(len(ts_codes_by_date.get(d, [])) for d in pit_dates) if ts_codes_by_date else len(ts_codes) * len(pit_dates)
    print(f"\n[3/4] Building replay panel ({requested_rows} requested rows)...")
    t0 = time.monotonic()
    url = engine.url.render_as_string(hide_password=False)
    base = load_params()
    n_workers = args.workers if args.workers > 0 else max(1, (os.cpu_count() or 4) - 1)

    last_progress_at = [time.monotonic()]
    def on_progress(p):
        if p.get("event") == "cache_hit":
            print(f"      [cache] reused panel: {p['rows']} rows from {p['path']}")
            return
        if p.get("event") == "cache_miss":
            print(f"      [cache] miss/rebuild: {p.get('total_pairs', 0)} requested rows -> {p['path']}")
            return
        if p.get("event") == "row_error":
            print(f"      [panel row error] {p.get('error')}")
            return
        now = time.monotonic()
        if now - last_progress_at[0] >= 5.0 or p.get("completed") == p.get("total"):
            last_progress_at[0] = now
            print(f"      progress: {p['completed']}/{p['total']} ok={p.get('ok')} fail={p.get('failed')} "
                  f"rate={p.get('rate_per_min')}/min eta={p.get('eta_sec')}s")

    rows, manifest = build_replay_panel(
        url,
        ts_codes=ts_codes,
        as_of_dates=pit_dates,
        ts_codes_by_date=ts_codes_by_date,
        base_params=base,
        universe_id=universe_label,
        universe_mode=args.universe_mode,
        universe_selection=universe_selection,
        skip_llm=not args.include_llm,
        n_workers=n_workers,
        on_progress=on_progress,
        max_codes_per_chunk=args.panel_chunk_size,
    )
    panel_elapsed = time.monotonic() - t0
    print(f"      panel built: {len(rows)} rows in {panel_elapsed:.1f}s ({len(rows)*60/max(panel_elapsed,1):.1f} rows/min)")
    print(f"      cached at: {manifest.panel_path}")
    expected_rows = manifest.total_pairs or (manifest.universe_size * len(manifest.as_of_dates))
    failed_rows = manifest.failed_rows if manifest.failed_rows is not None else max(0, expected_rows - len(rows))
    print(f"      manifest: {manifest.manifest_path}")
    failure_rate = failed_rows / expected_rows if expected_rows else 0.0
    print(f"      row summary: requested={expected_rows} ok={len(rows)} fail={failed_rows} failure_rate={failure_rate:.2%}")
    if failed_rows:
        details = list(getattr(manifest, "failure_details", []) or [])
        if details:
            reason_counts: dict[str, int] = {}
            for item in details:
                reason = str(item.get("reason") or "unknown")
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            print(f"      WARN: row failures by reason: {reason_counts}")
            for item in details[:10]:
                print(
                    "        "
                    f"{item.get('ts_code')}@{item.get('as_of_date')}: "
                    f"{item.get('reason')}"
                )
            if len(details) > 10:
                print(f"        ... {len(details) - 10} more failures in manifest")
        else:
            print(f"      WARN: row failures detected; legacy manifest has no per-row diagnostics")

    if not rows:
        print("ERROR: panel is empty; cannot tune", file=sys.stderr)
        return 3

    if args.diagnose_panel:
        try:
            from stock_edge_panel_diagnose import diagnose, write_diagnosis_artifact

            report = diagnose(Path(manifest.panel_path), min_slice_rows=max(10, args.regime_min_samples))
            diagnosis_path = write_diagnosis_artifact(report, output_dir=Path(args.diagnosis_output_dir))
            print(f"      diagnosis artifact: {diagnosis_path}")
            flags = report.get("diagnosis_flags") or []
            if flags:
                print(f"      diagnosis flags: {flags[:5]}")
        except Exception as exc:
            print(f"      WARN: panel diagnosis failed: {type(exc).__name__}: {exc}")

    # K-fold rolling walk-forward (Phase 5 v2 — robust OOS)
    k_fold_done = False
    kfold_for_gate: list[dict] | None = None
    if args.k_fold and args.k_fold >= 2:
        folds = k_fold_rolling_walk_forward(
            rows,
            n_folds=args.k_fold,
            val_dates_per_fold=args.val_dates_per_fold,
            min_train_dates=args.min_train_dates,
            embargo_days=args.embargo_days,
        )
        if not folds:
            print("ERROR: not enough dates for k-fold; falling back to single split", file=sys.stderr)
            args.k_fold = 0
        else:
            print(f"\n[K-fold rolling walk-forward] {len(folds)} folds, val_dates={args.val_dates_per_fold} each, min_train={args.min_train_dates}, embargo={args.embargo_days}d")
            for i, (tr, va) in enumerate(folds):
                tr_dates = sorted({r.as_of_date for r in tr})
                va_dates = sorted({r.as_of_date for r in va})
                print(f"  Fold {i+1}: train={len(tr)} rows ({len(tr_dates)} dates: {tr_dates[0]}..{tr_dates[-1]}) | val={len(va)} rows ({va_dates[0]}..{va_dates[-1]})")

            # Run search per fold; aggregate val metrics
            print(f"\n[4/4] Running K-fold search (each fold: {args.max_candidates} candidates × {args.n_iterations} iter)...")
            fold_results = []
            t0 = time.monotonic()
            search_progress_at = [0.0]

            def search_progress(prefix: str):
                def _progress(p):
                    event = p.get("event")
                    if event in {"stage_start", "stage_done"}:
                        label = "start" if event == "stage_start" else "done"
                        print(
                            f"      {prefix} stage {p.get('stage')} {label}: "
                            f"budget={p.get('total', 0)} score={float(p.get('score', 0.0)):.4f} "
                            f"best={float(p.get('best_score', 0.0)):.4f}"
                        )
                        return
                    now = time.monotonic()
                    if now - search_progress_at[0] < 5.0 and p.get("candidate") != p.get("total"):
                        return
                    search_progress_at[0] = now
                    stage = p.get("stage")
                    stage_part = f" stage={stage}" if stage is not None else ""
                    print(
                        f"      {prefix}{stage_part} cand {p.get('candidate', 0)}/{p.get('total', 0)} "
                        f"score={float(p.get('score', 0.0)):.4f} best={float(p.get('best_score', 0.0)):.4f} "
                        f"elapsed={p.get('elapsed_seconds', '?')}s"
                    )
                return _progress

            for i, (train_rows, val_rows) in enumerate(folds):
                print(f"  Fold {i+1}: search start train_rows={len(train_rows)} val_rows={len(val_rows)}")
                initial_overlay = None
                if args.two_stage:
                    proxy_rows = _cheap_proxy_rows(
                        train_rows,
                        max_rows=args.proxy_max_rows,
                        seed=f"{universe_label}:fold{i}:proxy",
                    )
                    print(
                        f"    Fold {i+1} two-stage proxy: rows={len(proxy_rows)}/{len(train_rows)} "
                        f"candidates={args.proxy_candidates}"
                    )
                    proxy_artifact = fit_global_preset_via_panel(
                        proxy_rows,
                        as_of_date=as_of,
                        base_params=base,
                        universe=f"{universe_label}_fold{i}_proxy",
                        max_candidates=args.proxy_candidates,
                        n_iterations=1,
                        use_ic_warmstart=not args.no_warmstart,
                        allow_negative_weights=not args.no_negative_weights,
                        search_algo=args.search_algo,
                        on_progress=search_progress(f"fold {i+1} proxy"),
                    )
                    initial_overlay = proxy_artifact.overlay
                    print(f"    Fold {i+1} proxy best score={proxy_artifact.objective_score:.4f}; expensive replay starts from proxy overlay")
                if args.successive_halving:
                    fold_artifact = fit_global_preset_successive_halving(
                        train_rows, as_of_date=as_of, base_params=base,
                        universe=f"{universe_label}_fold{i}",
                        total_budget=args.max_candidates,
                        use_ic_warmstart=not args.no_warmstart,
                        allow_negative_weights=not args.no_negative_weights,
                        search_algo=args.search_algo,
                        initial_overlay=initial_overlay,
                        on_progress=search_progress(f"fold {i+1}"),
                    )
                else:
                    fold_artifact = fit_global_preset_via_panel(
                        train_rows, as_of_date=as_of, base_params=base,
                        universe=f"{universe_label}_fold{i}",
                        max_candidates=args.max_candidates,
                        n_iterations=args.n_iterations,
                        use_ic_warmstart=not args.no_warmstart,
                        allow_negative_weights=not args.no_negative_weights,
                        search_algo=args.search_algo,
                        initial_overlay=initial_overlay,
                        on_progress=search_progress(f"fold {i+1}"),
                    )
                if args.two_stage:
                    fold_artifact.metrics["two_stage"] = {
                        "enabled": True,
                        "proxy_candidates": args.proxy_candidates,
                        "proxy_max_rows": args.proxy_max_rows,
                        "expensive_candidates": args.max_candidates,
                    }
                val_panel = panel_matrix_from_rows(val_rows)
                val_baseline = evaluate_overlay_on_panel(val_panel, {}, base)
                val_tuned = evaluate_overlay_on_panel(val_panel, fold_artifact.overlay, base)
                fold_results.append({
                    "fold": i + 1,
                    "train_dates": [d.isoformat() for d in sorted({r.as_of_date for r in train_rows})],
                    "val_dates": [d.isoformat() for d in sorted({r.as_of_date for r in val_rows})],
                    "train_artifact": fold_artifact,
                    "val_baseline": val_baseline,
                    "val_tuned": val_tuned,
                })
                vt5 = val_tuned['objective_5d']['rank_ic']
                vt10 = val_tuned['objective_10d']['rank_ic']
                vt20 = val_tuned['objective_20d']['rank_ic']
                vb5 = val_baseline['objective_5d']['rank_ic']
                vb10 = val_baseline['objective_10d']['rank_ic']
                vb20 = val_baseline['objective_20d']['rank_ic']
                print(f"  Fold {i+1}: val rank IC 5d {vb5:+.3f}→{vt5:+.3f} (Δ {vt5-vb5:+.3f}) | "
                      f"10d {vb10:+.3f}→{vt10:+.3f} (Δ {vt10-vb10:+.3f}) | "
                      f"20d {vb20:+.3f}→{vt20:+.3f} (Δ {vt20-vb20:+.3f})")
                for h in (5, 10, 20):
                    bm = val_baseline[f"objective_{h}d"]
                    tm = val_tuned[f"objective_{h}d"]
                    print(
                        f"      {h}d payoff: top_ret {float(bm.get('top_bucket_avg_return', 0.0))*100:+.2f}%"
                        f"→{float(tm.get('top_bucket_avg_return', 0.0))*100:+.2f}% "
                        f"spread {float(bm.get('top_bottom_spread', 0.0))*100:+.2f}%"
                        f"→{float(tm.get('top_bottom_spread', 0.0))*100:+.2f}% "
                        f"mono {float(bm.get('bucket_monotonicity', 0.0)):+.2f}"
                        f"→{float(tm.get('bucket_monotonicity', 0.0)):+.2f}"
                    )
            elapsed = time.monotonic() - t0
            print(f"      total search across {len(folds)} folds: {elapsed:.1f}s")

            # Summary table
            print(f"\n=== K-Fold Aggregate (median across {len(folds)} folds) ===")
            import statistics
            for h in (5, 10, 20):
                lifts = [r['val_tuned'][f'objective_{h}d']['rank_ic'] - r['val_baseline'][f'objective_{h}d']['rank_ic'] for r in fold_results]
                tuneds = [r['val_tuned'][f'objective_{h}d']['rank_ic'] for r in fold_results]
                bases = [r['val_baseline'][f'objective_{h}d']['rank_ic'] for r in fold_results]
                pos_folds = sum(1 for l in lifts if l > 0)
                print(f"  {h}d: median val_lift {statistics.median(lifts):+.4f} | per-fold lifts {[f'{l:+.3f}' for l in lifts]} | positive {pos_folds}/{len(folds)} folds")
                print(f"      tuned IC range: {min(tuneds):+.3f}..{max(tuneds):+.3f}, baseline range: {min(bases):+.3f}..{max(bases):+.3f}")

            # Pick "best fold" artifact for downstream auto-promote (latest fold = most recent training)
            artifact = fold_results[-1]['train_artifact']
            val_metrics_baseline = fold_results[-1]['val_baseline']
            val_metrics_tuned = fold_results[-1]['val_tuned']
            # Compact fold metrics for G9 gate input
            kfold_for_gate = [
                {"val_baseline": fr["val_baseline"], "val_tuned": fr["val_tuned"], "fold": fr["fold"]}
                for fr in fold_results
            ]
            print(f"\n  [auto-promote will use latest fold's artifact + K-fold results for G9 gate]")
            search_elapsed = elapsed

            # Skip the regular [4/4] search and the single-OOS split
            k_fold_done = True
            args.oos = False

    # Walk-forward OOS split (Phase 5)
    if not k_fold_done and args.oos:
        train_rows, val_rows = walk_forward_split(
            rows, train_fraction=args.train_fraction, embargo_days=args.embargo_days,
        )
        train_dates = sorted({r.as_of_date for r in train_rows})
        val_dates = sorted({r.as_of_date for r in val_rows})
        print(f"\n[OOS split] train={len(train_rows)} rows ({len(train_dates)} dates: {train_dates[0]}..{train_dates[-1]}) | val={len(val_rows)} rows ({len(val_dates)} dates: {val_dates[0] if val_dates else '-'}..{val_dates[-1] if val_dates else '-'}) | embargo={args.embargo_days}d")
        if not val_rows:
            print(f"      ERROR: validation set empty (panel only spans {len(set(r.as_of_date for r in rows))} dates with embargo {args.embargo_days}d). Cannot do OOS.", file=sys.stderr)
            return 4
        search_rows = train_rows
    elif not k_fold_done:
        search_rows = rows

    if not k_fold_done:
        print(f"\n[4/4] Running search ({args.max_candidates} candidates × {args.n_iterations} iterations over decision_layer space)...")
        t0 = time.monotonic()
        search_progress_at = [0.0]

        def search_progress(p):
            event = p.get("event")
            if event in {"stage_start", "stage_done"}:
                label = "start" if event == "stage_start" else "done"
                print(
                    f"      search stage {p.get('stage')} {label}: "
                    f"budget={p.get('total', 0)} score={float(p.get('score', 0.0)):.4f} "
                    f"best={float(p.get('best_score', 0.0)):.4f}"
                )
                return
            now = time.monotonic()
            if now - search_progress_at[0] < 5.0 and p.get("candidate") != p.get("total"):
                return
            search_progress_at[0] = now
            stage = p.get("stage")
            stage_part = f" stage={stage}" if stage is not None else ""
            print(
                f"      search{stage_part} cand {p.get('candidate', 0)}/{p.get('total', 0)} "
                f"score={float(p.get('score', 0.0)):.4f} best={float(p.get('best_score', 0.0)):.4f} "
                f"elapsed={p.get('elapsed_seconds', '?')}s"
            )

        initial_overlay = None
        if args.two_stage:
            proxy_rows = _cheap_proxy_rows(
                search_rows,
                max_rows=args.proxy_max_rows,
                seed=f"{universe_label}:proxy",
            )
            print(
                f"      two-stage proxy search: rows={len(proxy_rows)}/{len(search_rows)} "
                f"candidates={args.proxy_candidates}"
            )
            proxy_artifact = fit_global_preset_via_panel(
                proxy_rows,
                as_of_date=as_of,
                base_params=base,
                universe=f"{universe_label}_proxy",
                max_candidates=args.proxy_candidates,
                n_iterations=1,
                use_ic_warmstart=not args.no_warmstart,
                allow_negative_weights=not args.no_negative_weights,
                search_algo=args.search_algo,
                initial_overlay=initial_overlay,
                on_progress=search_progress,
            )
            initial_overlay = proxy_artifact.overlay
            print(f"      proxy best objective score: {proxy_artifact.objective_score:.6f}")

        if args.successive_halving:
            artifact = fit_global_preset_successive_halving(
                search_rows,
                as_of_date=as_of,
                base_params=base,
                universe=universe_label,
                total_budget=args.max_candidates,
                use_ic_warmstart=not args.no_warmstart,
                allow_negative_weights=not args.no_negative_weights,
                search_algo=args.search_algo,
                on_progress=search_progress,
            )
        else:
            artifact = fit_global_preset_via_panel(
                search_rows,
                as_of_date=as_of,
                base_params=base,
                universe=universe_label,
                max_candidates=args.max_candidates,
                n_iterations=args.n_iterations,
                use_ic_warmstart=not args.no_warmstart,
                allow_negative_weights=not args.no_negative_weights,
                search_algo=args.search_algo,
                initial_overlay=initial_overlay,
                on_progress=search_progress,
            )
        if args.two_stage:
            artifact.metrics["two_stage"] = {
                "enabled": True,
                "proxy_candidates": args.proxy_candidates,
                "proxy_max_rows": args.proxy_max_rows,
                "expensive_candidates": args.max_candidates,
                "note": "cheap proxy uses deterministic regime/date-balanced row subset; expensive stage evaluates production replay panel",
            }
        search_elapsed = time.monotonic() - t0
        print(f"      search: {search_elapsed:.2f}s ({artifact.candidate_count}/{search_elapsed:.1f}s = {artifact.candidate_count/max(search_elapsed, 0.001):.0f} cand/sec, {artifact.metrics.get('search_iterations', 1)} iterations)")

        print(f"\n=== Results ===")
        print(f"  best objective score: {artifact.objective_score:.6f}")
        print(f"  candidates evaluated: {artifact.candidate_count}")
        print(f"  panel rows used:      {artifact.metrics.get('panel_n_rows', 0)}")
        for h in (5, 10, 20):
            m = artifact.metrics.get(f"objective_{h}d", {})
            print(f"  {h}d: n={m.get('sample_count', 0):4d} ic={m.get('ic', 0):+.3f} rank_ic={m.get('rank_ic', 0):+.3f} "
                  f"avg_ret={float(m.get('avg_return', 0))*100:+.2f}% top_ret={float(m.get('top_bucket_avg_return', 0))*100:+.2f}% "
                  f"top_win={m.get('top_bucket_win_rate', 0):.2f} spread={float(m.get('top_bottom_spread', 0))*100:+.2f}% "
                  f"mono={m.get('bucket_monotonicity', 0):+.2f} left_tail={float(m.get('top_bucket_left_tail', 0))*100:+.2f}% "
                  f"buy_n={m.get('buy_signals', 0)}")

        print(f"\n  Top 10 weight changes:")
        weight_deltas = [(k, v) for k, v in artifact.overlay.items() if "weights." in k]
        weight_deltas.sort(key=lambda kv: -abs(float(kv[1]) - 1.0))
        for k, v in weight_deltas[:10]:
            print(f"    {k} = {v:.3f}")

    if not args.dry_run:
        path = write_tuning_artifact(artifact)
        print(f"\n  artifact written: {path}")
    else:
        print(f"\n  [dry-run] artifact NOT written")
        print(f"  [dry-run] baseline YAML NOT touched: {args.base_yaml}")

    # ── OOS validation (Phase 5) ──────────────────────────────
    if args.oos:
        print(f"\n=== OOS Validation (held-out {len(val_rows)} rows from {val_dates[0]}..{val_dates[-1]}) ===")
        val_panel = panel_matrix_from_rows(val_rows)
        train_panel = panel_matrix_from_rows(train_rows)
        train_metrics_baseline = evaluate_overlay_on_panel(train_panel, {}, base)
        train_metrics_tuned = artifact.metrics
        val_metrics_baseline = evaluate_overlay_on_panel(val_panel, {}, base)
        val_metrics_tuned = evaluate_overlay_on_panel(val_panel, artifact.overlay, base)

        print(f"\n  {'Metric':30s}  {'Train base':>12s}  {'Train tuned':>12s}  {'Val base':>12s}  {'Val tuned':>12s}  {'Overfit':>10s}")
        print(f"  {'-'*30}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*10}")
        for h in (5, 10, 20):
            tb = train_metrics_baseline[f'objective_{h}d']['rank_ic']
            tt = train_metrics_tuned[f'objective_{h}d']['rank_ic']
            vb = val_metrics_baseline[f'objective_{h}d']['rank_ic']
            vt = val_metrics_tuned[f'objective_{h}d']['rank_ic']
            train_lift = tt - tb
            val_lift = vt - vb
            overfit = train_lift - val_lift
            mark = "✅" if vt > 0 and val_lift > 0 else ("⚠" if val_lift > 0 else "❌")
            print(f"  {f'{h}d rank_ic':30s}  {tb:>+12.4f}  {tt:>+12.4f}  {vb:>+12.4f}  {vt:>+12.4f}  {overfit:>+10.4f} {mark}")
        cb = train_metrics_baseline['composite_objective']['score']
        ct = train_metrics_tuned['composite_objective']['score']
        vcb = val_metrics_baseline['composite_objective']['score']
        vct = val_metrics_tuned['composite_objective']['score']
        print(f"  {'composite':30s}  {cb:>12.4f}  {ct:>12.4f}  {vcb:>12.4f}  {vct:>12.4f}  {(ct-cb)-(vct-vcb):>+10.4f}")
        print(f"\n  Headline: VAL 10d lift = {val_metrics_tuned['objective_10d']['rank_ic'] - val_metrics_baseline['objective_10d']['rank_ic']:+.4f}, "
              f"VAL 10d rank IC = {val_metrics_tuned['objective_10d']['rank_ic']:+.4f}")

    # ── Auto-promotion (Phase 4) ──────────────────────────────
    if args.auto_promote and args.dry_run:
        print(f"\n=== Auto-Promotion Gates ===")
        print("  [dry-run] auto-promotion skipped; no variant YAML or baseline YAML will be written")

    if args.auto_promote and not args.dry_run:
        print(f"\n=== Auto-Promotion Gates ===")
        if args.oos or k_fold_done:
            origin = "K-fold latest fold" if k_fold_done else "single OOS split"
            print(f"      (Gating on VALIDATION set from {origin}, not training)")
            candidate_metrics_for_gate = val_metrics_tuned
            baseline_metrics = val_metrics_baseline
        else:
            panel = panel_matrix_from_rows(rows)
            baseline_metrics = evaluate_overlay_on_panel(panel, {}, base)
            candidate_metrics_for_gate = artifact.metrics
        gate_config: dict = {}
        if args.k_fold_min_positive > 0:
            gate_config["g9_min_positive_folds"] = args.k_fold_min_positive
        gate_config["g4_min_improved_bucket_pct"] = args.regime_min_bucket_pct

        # Pick val panel for downstream stat checks (G4 regime / G5 bootstrap).
        # Latest fold = the artifact we'd promote (its overlay was tuned on the most
        # recent training data without leakage). Earlier folds' val rows ARE part of
        # the latest fold's training window, so pooling them would leak.
        val_panel_for_stats = None
        if args.oos or k_fold_done:
            if k_fold_done:
                val_panel_for_stats = panel_matrix_from_rows(folds[-1][1])
            elif args.oos:
                val_panel_for_stats = panel_matrix_from_rows(val_rows)

        # ── G5 Bootstrap CI: compute on val panel ─────────────
        # K-fold mode: use across-fold t-CI (4 independent OOS lifts → real CI on
        # mean lift; no leakage). Single-OOS mode: bootstrap on the val panel rows.
        bootstrap_results = None
        if k_fold_done and kfold_for_gate:
            bootstrap_results = kfold_aggregate_ci(
                kfold_for_gate,
                confidence=args.bootstrap_confidence,
            )
            print(f"      G5 across-fold t-CI: {len(kfold_for_gate)} folds, conf={args.bootstrap_confidence}")
        elif args.bootstrap_iterations > 0 and val_panel_for_stats is not None:
            t0_boot = time.monotonic()
            bootstrap_results = bootstrap_rank_ic_lift(
                val_panel_for_stats, artifact.overlay, base,
                n_iterations=args.bootstrap_iterations,
                confidence=args.bootstrap_confidence,
            )
            t_boot = time.monotonic() - t0_boot
            print(f"      bootstrap CI: {args.bootstrap_iterations} iter on val panel, {t_boot:.2f}s")

        # ── G4 Regime-bucketed: compute on val panel ──────────
        regime_results = None
        if val_panel_for_stats is not None:
            t0_reg = time.monotonic()
            regime_results = regime_bucketed_rank_ic_lift(
                val_panel_for_stats, artifact.overlay, base,
                min_samples_per_bucket=args.regime_min_samples,
            )
            t_reg = time.monotonic() - t0_reg
            n_buckets_total = sum(len(v) for v in regime_results.values())
            print(f"      regime breakdown: {n_buckets_total} (horizon, regime) buckets ≥ {args.regime_min_samples} samples, {t_reg*1000:.0f}ms")

        decision = evaluate_promotion_gates(
            candidate_metrics_for_gate, baseline_metrics, artifact.overlay,
            config=gate_config or None,
            kfold_results=kfold_for_gate,
            bootstrap_results=bootstrap_results,
            regime_breakdown=regime_results,
            artifact_metrics=artifact.metrics,
        )
        for g in decision.gates:
            mark = "✓" if g.passed else "✗"
            print(f"  {mark} {g.gate_id} {g.name:35s} passed={g.passed}")
            print(f"      {g.detail}")
        print(f"\n  → {decision.summary}")

        base_yaml = Path(args.base_yaml)
        if args.apply_to_baseline:
            variant_path = base_yaml
            print(f"      [apply-to-baseline] gates pass → will overwrite {base_yaml} with backup")
        elif args.variant_output:
            variant_path = Path(args.variant_output)
        else:
            variant_path = base_yaml.with_suffix(".variant.yaml")
        reject_dir = Path("/Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/rejected")
        result = auto_promote_if_passing(
            decision,
            candidate_overlay=artifact.overlay,
            base_yaml=base_yaml,
            variant_output=variant_path,
            reject_dir=reject_dir,
            backup=True,
        )
        # T1.4 horizon-selective output
        applied = result.get("horizons_applied") or []
        kept = result.get("horizons_kept_baseline") or []
        if result.get("variant_path"):
            if applied and not kept:
                print(f"\n  ✅ ACCEPTED ALL — variant YAML written: {result['variant_path']}")
            elif applied:
                print(f"\n  🟡 PARTIAL — horizon-selective variant written: {result['variant_path']}")
                print(f"     ▸ applied:        {', '.join(applied)} (passed G4+G5+G9 per-horizon)")
                print(f"     ▸ kept baseline:  {', '.join(kept)} (failed at least one per-horizon gate)")
            if result.get("backup_path"):
                print(f"     backup: {result['backup_path']}")
        else:
            print(f"\n  ⚠ REJECTED — no horizon passes per-horizon gates")
            print(f"     reject report: {result.get('reject_path', '(no reject_dir)')}")

    print(f"\n  total wall time: {panel_elapsed + search_elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
