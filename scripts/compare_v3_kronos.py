#!/usr/bin/env python3
"""Head-to-head comparison: V3 (handcrafted features only) vs V3+Kronos.

Trains both variants using the SAME train/OOS split, then prints a side-by-side
table showing whether Kronos embeddings actually improve performance.

Usage:
    uv run python scripts/compare_v3_kronos.py
"""
from __future__ import annotations

import datetime as dt
import time

import pandas as pd
from rich.console import Console
from rich.table import Table


def main():
    console = Console()
    console.print("[bold cyan]V3 head-to-head: handcrafted vs +Kronos[/bold cyan]\n")

    is_start = dt.date(2024, 1, 2)
    is_end   = dt.date(2025, 9, 30)
    oos_end  = dt.date(2026, 4, 30)

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.features_v2     import build_candidate_feature_matrix
    from ifa.families.ningbo.ml.kronos_features import attach_kronos_embeddings
    from ifa.families.ningbo.ml.trainer_v3      import train_models_v3

    engine = get_engine(get_settings())

    console.print("[bold]Step 1/4: Building base feature matrix…[/bold]")
    t0 = time.time()
    feat_df_base = build_candidate_feature_matrix(engine, is_start, oos_end, include_outcomes=True)
    console.print(f"  {feat_df_base.shape}  [{time.time()-t0:.1f}s]\n")

    console.print("[bold]Step 2/4: Attaching Kronos embeddings…[/bold]")
    t0 = time.time()
    feat_df_kronos = attach_kronos_embeddings(engine, feat_df_base)
    console.print(f"  {feat_df_kronos.shape}  [{time.time()-t0:.1f}s]\n")

    console.print("[bold]Step 3/4: Training V3 (no Kronos)…[/bold]")
    t0 = time.time()
    art_base = train_models_v3(
        feat_df_base, in_sample_end=is_end,
        use_tabnet=False, use_kronos_features=False,
        on_log=lambda m: console.print(f"  [dim]{m}[/dim]"),
    )
    console.print(f"  done in {time.time()-t0:.1f}s\n")

    console.print("[bold]Step 4/4: Training V3+Kronos (39 + 256 features)…[/bold]")
    t0 = time.time()
    art_kron = train_models_v3(
        feat_df_kronos, in_sample_end=is_end,
        use_tabnet=False, use_kronos_features=True,
        on_log=lambda m: console.print(f"  [dim]{m}[/dim]"),
    )
    console.print(f"  done in {time.time()-t0:.1f}s\n")

    # ── Side-by-side table ──────────────────────────────────────────────────
    console.print(f"[bold cyan]Head-to-head Comparison[/bold cyan]\n")
    t = Table(show_header=True, header_style="bold")
    t.add_column("Model")
    t.add_column("Variant")
    t.add_column("AUC",      justify="right")
    t.add_column("NDCG@5",   justify="right")
    t.add_column("T5_Mean",  justify="right", style="green")
    t.add_column("T5_Med",   justify="right")
    t.add_column("Sharpe",   justify="right")
    t.add_column("WinRate",  justify="right")
    t.add_column("MaxDD",    justify="right", style="red")

    model_names = ["heuristic", "lr", "rf", "xgb_clf", "lgbm_clf", "cat_clf",
                   "xgb_pair", "xgb_ndcg", "lgbm_lamda", "ensemble_meanrank"]
    for name in model_names:
        for label, art, style in [("base", art_base, ""), ("+kronos", art_kron, "yellow")]:
            m = art.metrics.get(name)
            if m is None:
                continue
            t.add_row(
                name, label,
                f"{m.oos_auc:.3f}",
                f"{m.oos_ndcg5:.3f}",
                f"{m.oos_top5_avg_return*100:+.2f}%",
                f"{m.oos_top5_med_return*100:+.2f}%",
                f"{m.oos_top5_sharpe:.2f}",
                f"{m.oos_top5_winrate*100:.0f}%",
                f"{m.oos_max_drawdown*100:+.1f}%",
                style=style,
            )
    console.print(t)

    # Δ table — improvement of +kronos over base
    console.print(f"\n[bold cyan]Δ Improvement (+kronos − base)[/bold cyan]\n")
    delta = Table(show_header=True, header_style="bold")
    delta.add_column("Model")
    delta.add_column("ΔAUC",     justify="right")
    delta.add_column("ΔNDCG@5",  justify="right")
    delta.add_column("ΔT5_Mean", justify="right", style="bold")
    delta.add_column("ΔT5_Med",  justify="right")
    delta.add_column("ΔSharpe",  justify="right")
    delta.add_column("ΔWinRate", justify="right")
    delta.add_column("ΔMaxDD",   justify="right")

    for name in model_names:
        if name == "heuristic":  # heuristic doesn't change with kronos features
            continue
        mb = art_base.metrics.get(name)
        mk = art_kron.metrics.get(name)
        if mb is None or mk is None:
            continue
        def _d(a, b, pct=False):
            d = a - b
            sign = "+" if d > 0 else ""
            if pct:
                return f"{sign}{d*100:.2f}pp"
            return f"{sign}{d:.3f}"
        delta.add_row(
            name,
            _d(mk.oos_auc,            mb.oos_auc),
            _d(mk.oos_ndcg5,          mb.oos_ndcg5),
            _d(mk.oos_top5_avg_return,mb.oos_top5_avg_return, pct=True),
            _d(mk.oos_top5_med_return,mb.oos_top5_med_return, pct=True),
            _d(mk.oos_top5_sharpe,    mb.oos_top5_sharpe),
            _d(mk.oos_top5_winrate,   mb.oos_top5_winrate, pct=True),
            _d(mk.oos_max_drawdown,   mb.oos_max_drawdown, pct=True),
        )
    console.print(delta)

    console.print(f"\n[bold]Final decision[/bold]")
    console.print(f"  base:    {art_base.production_model_name} → {art_base.decision}")
    console.print(f"  +kronos: {art_kron.production_model_name} → {art_kron.decision}")


if __name__ == "__main__":
    main()
