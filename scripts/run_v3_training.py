#!/usr/bin/env python3
"""Run Stage C.1 — full model matrix on candidate pool (no Kronos yet).

Usage:
    uv run python scripts/run_v3_training.py
    uv run python scripts/run_v3_training.py --no-tabnet  # skip TabNet if MPS issues
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

from rich.console import Console
from rich.table import Table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in-sample-end", default="2025-09-30")
    parser.add_argument("--oos-end",       default="2026-04-30")
    parser.add_argument("--in-sample-start", default="2024-01-02")
    parser.add_argument("--no-tabnet", action="store_true", help="skip TabNet")
    parser.add_argument("--with-kronos", action="store_true",
                        help="use precomputed Kronos features (requires Stage C.2)")
    args = parser.parse_args()

    console = Console()

    is_start = dt.date.fromisoformat(args.in_sample_start)
    is_end   = dt.date.fromisoformat(args.in_sample_end)
    oos_end  = dt.date.fromisoformat(args.oos_end)

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.features_v2 import build_candidate_feature_matrix
    from ifa.families.ningbo.ml.trainer_v3  import train_models_v3

    engine = get_engine(get_settings())

    console.print(f"[bold cyan]Stage C.1 V3 Training[/bold cyan]")
    console.print(f"  in-sample  : {is_start} → {is_end}")
    console.print(f"  oos        : {is_end} → {oos_end}")
    console.print(f"  TabNet     : {'OFF' if args.no_tabnet else 'ON'}")
    console.print(f"  Kronos     : {'ON' if args.with_kronos else 'OFF'}")

    t_global = time.time()

    # Build feature matrix
    console.print(f"\n[bold]1. Building feature matrix from candidates_daily…[/bold]")
    t0 = time.time()
    feat_df = build_candidate_feature_matrix(engine, is_start, oos_end, include_outcomes=True)

    # Optionally augment with Kronos embeddings (if precomputed)
    if args.with_kronos:
        from ifa.families.ningbo.ml.kronos_features import attach_kronos_embeddings
        feat_df = attach_kronos_embeddings(engine, feat_df)

    console.print(
        f"   feature_df: {feat_df.shape}  "
        f"({feat_df['rec_date'].nunique()} days, "
        f"{(feat_df['outcome_status']=='take_profit').sum():,} take_profit)  "
        f"[{time.time()-t0:.1f}s]"
    )

    # Train models
    console.print(f"\n[bold]2. Training comprehensive model matrix…[/bold]")
    t1 = time.time()
    art = train_models_v3(
        feat_df, in_sample_end=is_end,
        use_tabnet=not args.no_tabnet,
        use_kronos_features=args.with_kronos,
        on_log=lambda m: console.print(f"   {m}"),
    )
    console.print(f"   training done in {time.time()-t1:.1f}s")

    # Comparison table
    console.print(f"\n[bold cyan]Model Matrix — OOS Performance (sorted by Top5_Mean)[/bold cyan]")
    sorted_metrics = sorted(
        art.metrics.items(),
        key=lambda kv: kv[1].oos_top5_avg_return,
        reverse=True,
    )

    t = Table(show_header=True, header_style="bold")
    t.add_column("Model", style="bold")
    t.add_column("Obj")
    t.add_column("AUC",       justify="right")
    t.add_column("NDCG@5",    justify="right", style="cyan")
    t.add_column("T5_Prec",   justify="right")
    t.add_column("T5_Mean",   justify="right", style="green")
    t.add_column("T5_Med",    justify="right")
    t.add_column("Sharpe",    justify="right")
    t.add_column("WinRate",   justify="right")
    t.add_column("MaxDD",     justify="right", style="red")
    t.add_column("p_value",   justify="right", style="yellow")
    t.add_column("✓",         justify="center")

    for name, m in sorted_metrics:
        def _f(v): return f"{v:.3f}" if v == v else "—"
        passes = "✓" if m.passes_promotion else "✗" if name != "heuristic" else "—"
        is_winner = (name == art.production_model_name)
        style = "bold yellow" if is_winner else ""
        t.add_row(
            name + (" ⭐" if is_winner else ""),
            m.objective,
            _f(m.oos_auc), _f(m.oos_ndcg5),
            f"{m.oos_top5_precision*100:.1f}%",
            f"{m.oos_top5_avg_return*100:+.2f}%",
            f"{m.oos_top5_med_return*100:+.2f}%",
            f"{m.oos_top5_sharpe:.2f}",
            f"{m.oos_top5_winrate*100:.0f}%",
            f"{m.oos_max_drawdown*100:+.1f}%",
            f"{m.bootstrap_p_value:.3f}" if m.bootstrap_p_value == m.bootstrap_p_value else "—",
            passes,
            style=style,
        )
    console.print(t)

    console.print(f"\n[bold]Decision:[/bold] {art.decision}")
    console.print(f"[dim]version={art.model_version}  total elapsed={time.time()-t_global:.1f}s[/dim]")


if __name__ == "__main__":
    main()
