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
import datetime as dt
import json
import os
import random
import sys
import time
from pathlib import Path

from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.families.stock.backtest.optimizer import fit_global_preset_via_panel
from ifa.families.stock.backtest.panel_evaluator import (
    evaluate_overlay_on_panel,
    panel_matrix_from_rows,
)
from ifa.families.stock.backtest.promotion import auto_promote_if_passing, evaluate_promotion_gates
from ifa.families.stock.backtest.replay_panel import build_replay_panel
from ifa.families.stock.backtest.tuning_artifact import write_tuning_artifact
from ifa.families.stock.params import load_params


def _select_universe(engine, *, top_n: int, lookback_days: int = 20, as_of: dt.date) -> list[str]:
    """Top N by 20-day average daily turnover, ending at as_of."""
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT ts_code, AVG(amount) AS avg_amount
            FROM smartmoney.raw_daily
            WHERE trade_date <= :as_of AND trade_date >= :start
            GROUP BY ts_code
            HAVING COUNT(*) >= :min_days
            ORDER BY avg_amount DESC
            LIMIT :n
        """), {
            "as_of": as_of,
            "start": as_of - dt.timedelta(days=lookback_days * 2),
            "min_days": int(lookback_days * 0.7),
            "n": top_n,
        }).all()
    return [r[0] for r in rows]


def _select_pit_dates(engine, *, n_samples: int, latest_as_of: dt.date, forward_min_days: int = 25) -> list[dt.date]:
    """Pick N trading days that have at least `forward_min_days` of future bars in DB.

    Strategy: take all SSE trading days between (latest - 18 months) and (latest - forward_min_days * 1.5/business),
    sort descending, evenly sample.
    """
    horizon_days = max(35, int(forward_min_days * 1.5))
    end_max = latest_as_of - dt.timedelta(days=horizon_days)
    start = latest_as_of - dt.timedelta(days=18 * 30)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Stock Edge panel-based coarse tuning")
    parser.add_argument("--as-of", default=None, help="Latest as_of trade date (default: auto)")
    parser.add_argument("--top", type=int, default=50, help="Top N by liquidity (default 50)")
    parser.add_argument("--pit-samples", type=int, default=8, help="PIT trading days to sample (default 8)")
    parser.add_argument("--max-candidates", type=int, default=256, help="Search candidates (default 256)")
    parser.add_argument("--workers", type=int, default=-1, help="Parallel workers (-1 = auto, default -1)")
    parser.add_argument("--universe-id", default="top_liquidity", help="Cache key prefix")
    parser.add_argument("--include-llm", action="store_true", help="Include LLM signals (slower)")
    parser.add_argument("--dry-run", action="store_true", help="Build panel + tune but don't write artifact")
    parser.add_argument("--n-iterations", type=int, default=3, help="Search iterations (default 3)")
    parser.add_argument("--no-warmstart", action="store_true", help="Disable IC-derived warmstart")
    parser.add_argument("--no-negative-weights", action="store_true", help="Disable negative weights for inverted signals")
    parser.add_argument("--auto-promote", action="store_true", help="Apply gates; if passed, write YAML variant")
    parser.add_argument("--variant-output", default=None, help="Where to write YAML variant (default: side-by-side .variant.yaml)")
    parser.add_argument("--base-yaml", default="ifa/families/stock/params/stock_edge_v2.2.yaml")
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
    print(f"  PIT samples:     {args.pit_samples}")
    print(f"  candidates:      {args.max_candidates}")
    print(f"  workers:         {args.workers if args.workers > 0 else os.cpu_count() - 1}")
    print(f"  skip_llm:        {not args.include_llm}")
    print(f"  dry_run:         {args.dry_run}")

    print(f"\n[1/4] Selecting universe...")
    t0 = time.monotonic()
    ts_codes = _select_universe(engine, top_n=args.top, as_of=as_of)
    print(f"      {len(ts_codes)} stocks selected ({time.monotonic()-t0:.1f}s)")

    print(f"\n[2/4] Selecting PIT trading days...")
    t0 = time.monotonic()
    pit_dates = _select_pit_dates(engine, n_samples=args.pit_samples, latest_as_of=as_of)
    if len(pit_dates) < args.pit_samples:
        print(f"      WARN: only {len(pit_dates)} dates available")
    print(f"      dates: {[d.isoformat() for d in pit_dates]} ({time.monotonic()-t0:.1f}s)")

    print(f"\n[3/4] Building replay panel ({len(ts_codes)} × {len(pit_dates)} = {len(ts_codes)*len(pit_dates)} rows)...")
    t0 = time.monotonic()
    url = engine.url.render_as_string(hide_password=False)
    base = load_params()
    n_workers = args.workers if args.workers > 0 else max(1, (os.cpu_count() or 4) - 1)

    last_progress_at = [time.monotonic()]
    def on_progress(p):
        if p.get("event") == "cache_hit":
            print(f"      [cache] reused panel: {p['rows']} rows from {p['path']}")
            return
        if p.get("event") == "row_error":
            return  # silent
        now = time.monotonic()
        if now - last_progress_at[0] >= 5.0 or p.get("completed") == p.get("total"):
            last_progress_at[0] = now
            print(f"      progress: {p['completed']}/{p['total']} ok={p.get('ok')} fail={p.get('failed')} "
                  f"rate={p.get('rate_per_min')}/min eta={p.get('eta_sec')}s")

    rows, manifest = build_replay_panel(
        url,
        ts_codes=ts_codes,
        as_of_dates=pit_dates,
        base_params=base,
        universe_id=f"{args.universe_id}_top{args.top}",
        skip_llm=not args.include_llm,
        n_workers=n_workers,
        on_progress=on_progress,
    )
    panel_elapsed = time.monotonic() - t0
    print(f"      panel built: {len(rows)} rows in {panel_elapsed:.1f}s ({len(rows)*60/max(panel_elapsed,1):.1f} rows/min)")
    print(f"      cached at: {manifest.panel_path}")

    if not rows:
        print("ERROR: panel is empty; cannot tune", file=sys.stderr)
        return 3

    print(f"\n[4/4] Running search ({args.max_candidates} candidates × {args.n_iterations} iterations over decision_layer space)...")
    t0 = time.monotonic()
    artifact = fit_global_preset_via_panel(
        rows,
        as_of_date=as_of,
        base_params=base,
        universe=f"{args.universe_id}_top{args.top}",
        max_candidates=args.max_candidates,
        n_iterations=args.n_iterations,
        use_ic_warmstart=not args.no_warmstart,
        allow_negative_weights=not args.no_negative_weights,
        on_progress=lambda p: print(f"      iter {p.get('iteration', 0)} cand {p['candidate']}/{p['total']} score={p['score']:.4f} best={p['best_score']:.4f}") if p.get("candidate", 0) % max(1, args.max_candidates // 8) == 0 else None,
    )
    search_elapsed = time.monotonic() - t0
    print(f"      search: {search_elapsed:.2f}s ({artifact.candidate_count}/{search_elapsed:.1f}s = {artifact.candidate_count/max(search_elapsed, 0.001):.0f} cand/sec, {artifact.metrics.get('search_iterations', 1)} iterations)")

    print(f"\n=== Results ===")
    print(f"  best objective score: {artifact.objective_score:.6f}")
    print(f"  candidates evaluated: {artifact.candidate_count}")
    print(f"  panel rows used:      {artifact.metrics.get('panel_n_rows', 0)}")
    for h in (5, 10, 20):
        m = artifact.metrics.get(f"objective_{h}d", {})
        print(f"  {h}d: n={m.get('sample_count', 0):4d} ic={m.get('ic', 0):+.3f} rank_ic={m.get('rank_ic', 0):+.3f} "
              f"pos_ret={m.get('positive_return_rate', 0):.2f} target_first={m.get('target_first_rate', 0):.2f} "
              f"buy_n={m.get('buy_signals', 0)} buy_hit={m.get('buy_hit_rate', 0):.2f}")

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

    # ── Auto-promotion (Phase 4) ──────────────────────────────
    if args.auto_promote:
        print(f"\n=== Auto-Promotion Gates ===")
        panel = panel_matrix_from_rows(rows)
        baseline_metrics = evaluate_overlay_on_panel(panel, {}, base)
        decision = evaluate_promotion_gates(
            artifact.metrics, baseline_metrics, artifact.overlay,
        )
        for g in decision.gates:
            mark = "✓" if g.passed else "✗"
            print(f"  {mark} {g.gate_id} {g.name:35s} passed={g.passed}")
            print(f"      {g.detail}")
        print(f"\n  → {decision.summary}")

        base_yaml = Path(args.base_yaml)
        variant_path = Path(args.variant_output) if args.variant_output else base_yaml.with_suffix(".variant.yaml")
        reject_dir = Path("/Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/rejected")
        result = auto_promote_if_passing(
            decision,
            candidate_overlay=artifact.overlay,
            base_yaml=base_yaml,
            variant_output=variant_path,
            reject_dir=reject_dir,
            backup=True,
        )
        if result["accepted"]:
            print(f"\n  ✅ ACCEPTED — variant YAML written: {result['variant_path']}")
            if result.get("backup_path"):
                print(f"     backup: {result['backup_path']}")
        else:
            print(f"\n  ⚠ REJECTED — reject report: {result.get('reject_path', '(no reject_dir)')}")

    print(f"\n  total wall time: {panel_elapsed + search_elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
