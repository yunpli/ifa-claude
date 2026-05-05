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
    bootstrap_rank_ic_lift,
    evaluate_overlay_on_panel,
    k_fold_rolling_walk_forward,
    panel_matrix_from_rows,
    regime_bucketed_rank_ic_lift,
    walk_forward_split,
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
            for i, (train_rows, val_rows) in enumerate(folds):
                fold_artifact = fit_global_preset_via_panel(
                    train_rows, as_of_date=as_of, base_params=base,
                    universe=f"{args.universe_id}_top{args.top}_fold{i}",
                    max_candidates=args.max_candidates,
                    n_iterations=args.n_iterations,
                    use_ic_warmstart=not args.no_warmstart,
                    allow_negative_weights=not args.no_negative_weights,
                    on_progress=None,
                )
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
        artifact = fit_global_preset_via_panel(
            search_rows,
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
    if args.auto_promote:
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

        # Pick val panel for downstream stat checks
        val_panel_for_stats = None
        if args.oos or k_fold_done:
            if k_fold_done:
                val_panel_for_stats = panel_matrix_from_rows(folds[-1][1])
            elif args.oos:
                val_panel_for_stats = panel_matrix_from_rows(val_rows)

        # ── G5 Bootstrap CI: compute on val panel ─────────────
        bootstrap_results = None
        if args.bootstrap_iterations > 0 and val_panel_for_stats is not None:
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
