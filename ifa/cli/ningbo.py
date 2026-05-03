"""`ifa ningbo ...` CLI — 宁波短线策略报告.

Subcommands:
    evening   Generate evening report (16:30, EOD-based)
    backfill  Backfill historical signals + tracking
    train     Train ML scoring models (Phase 3+)
    params    Manage param versions

Slot subcommand pattern (mirrors smartmoney):
    ifa ningbo evening    ← Phase 1
    ifa ningbo intraday   ← Phase 4 (future, 10:30 mid-morning report)
"""
from __future__ import annotations

import datetime as dt
import os

import typer
from rich.console import Console

console = Console()
app = typer.Typer(no_args_is_help=True, help="宁波短线策略报告 — Phase 1: heuristic; Phase 3+: ML.")

# Sub-app for param management
params_app = typer.Typer(no_args_is_help=True, help="Manage ningbo strategy / model param versions.")
app.add_typer(params_app, name="params")


def _override_mode(mode: str | None) -> None:
    if mode:
        os.environ["IFA_RUN_MODE"] = mode


# ─── evening report ──────────────────────────────────────────────────────────
@app.command("evening")
def evening_command(
    report_date: str = typer.Option(
        None, "--report-date", help="YYYY-MM-DD (Beijing); default = today BJT"
    ),
    cutoff_time: str = typer.Option(
        "16:30", "--cutoff-time", help="HH:MM Beijing time (default 16:30)"
    ),
    triggered_by: str = typer.Option("manual", "--triggered-by"),
    mode: str = typer.Option("test", "--mode", help="test | manual | production"),
    scoring: str = typer.Option(
        "heuristic", "--scoring",
        help="heuristic (Phase 1+) | ml (Phase 3+) | both (Phase 3+ — render both sections)",
    ),
    model_version: str = typer.Option(
        None, "--model-version",
        help="ML model version tag (only used when --scoring=ml or both); default = active",
    ),
    user: str = typer.Option("default", "--user"),
    generate_pdf: bool = typer.Option(False, "--generate-pdf"),
) -> None:
    """Generate ningbo evening short-term strategy report."""
    _override_mode(mode)

    from ifa.core.report.timezones import parse_bjt_cutoff
    from ifa.families.ningbo.evening import run_ningbo_evening

    if report_date is None:
        from ifa.core.calendar import today_bjt
        rd = today_bjt()
    else:
        rd = dt.datetime.strptime(report_date, "%Y-%m-%d").date()

    cutoff_utc = parse_bjt_cutoff(rd, cutoff_time)

    if scoring == "both":
        scoring_modes = ("heuristic", "ml")
    elif scoring == "dual":
        # New dual-track flow: heuristic + ml_aggressive + ml_conservative + consensus matrix
        scoring_modes = ("dual",)
    elif scoring in ("heuristic", "ml"):
        scoring_modes = (scoring,)
    else:
        console.print(f"[red]Unknown --scoring value: {scoring}[/red]")
        raise typer.Exit(2)

    console.print(f"[cyan]starting Ningbo evening report for {rd} user={user} scoring={scoring}[/cyan]")
    path = run_ningbo_evening(
        report_date=rd,
        data_cutoff_at=cutoff_utc,
        user=user,
        triggered_by=triggered_by,
        scoring_modes=scoring_modes,
        on_log=lambda m: console.print(f"  {m}"),
    )
    console.print(f"\n[bold green]Report saved:[/bold green] {path}")

    if generate_pdf:
        from ifa.core.render.pdf import html_to_pdf
        pdf_path = html_to_pdf(path)
        console.print(f"[bold green]PDF saved:[/bold green] {pdf_path}")


# ─── backfill ────────────────────────────────────────────────────────────────
@app.command("backfill")
def backfill_command(
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD"),
    scoring: str = typer.Option(
        "heuristic", "--scoring",
        help="heuristic (Phase 2 default) | ml (Phase 3+)",
    ),
    skip_tracking: bool = typer.Option(
        False, "--skip-tracking", help="Skip run_tracking_batch per day (faster, outcomes unset)"
    ),
    mode: str = typer.Option("manual", "--mode"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress per-day progress logs"),
) -> None:
    """Backfill historical recommendations + tracking + outcomes.

    Reads from existing smartmoney.raw_* tables (no ETL, no LLM).
    Generates ningbo signals for every trading day in [start, end].
    Idempotent — safe to re-run (all DB writes use UPSERT).

    Example:
        ifa ningbo backfill --start 2021-01-04 --end 2026-04-30
        ifa ningbo backfill --start 2026-04-01 --end 2026-04-30  # quick test
    """
    _override_mode(mode)

    import time
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    start_date = dt.datetime.strptime(start, "%Y-%m-%d").date()
    end_date   = dt.datetime.strptime(end,   "%Y-%m-%d").date()

    console.print(
        f"[cyan]ningbo backfill  {start_date} → {end_date}  "
        f"scoring={scoring}  skip_tracking={skip_tracking}[/cyan]"
    )

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.backfill import run_ningbo_backfill

    settings = get_settings()
    engine = get_engine(settings)

    progress_state = {"current": ""}

    def _on_progress(i: int, n: int, d) -> None:
        progress_state["current"] = f"{d}  [{i}/{n}]"

    def _on_log(msg: str) -> None:
        if not quiet or msg.startswith("  ❌") or msg.startswith("  ⚠") or msg.startswith("──"):
            console.print(msg)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        disable=quiet,
    ) as progress:
        task = progress.add_task("backfilling…", total=None)

        def _on_progress_rich(i: int, n: int, d) -> None:
            progress.update(task, description=f"[cyan]{d}  [{i}/{n}][/cyan]")

        t0 = time.time()
        summary = run_ningbo_backfill(
            engine,
            start_date,
            end_date,
            scoring_mode=scoring,
            skip_tracking=skip_tracking,
            on_log=_on_log,
            on_progress=_on_progress_rich,
        )

    elapsed = time.time() - t0
    console.print(
        f"\n[bold green]Backfill complete[/bold green]  "
        f"{summary.trading_days_processed}/{summary.trading_days_total} days  "
        f"({summary.trading_days_skipped} skipped)  "
        f"recs={summary.recommendations_inserted}  "
        f"tracking_rows={summary.tracking_rows_added}  "
        f"errors={len(summary.errors)}  "
        f"elapsed={elapsed:.0f}s"
    )
    if summary.errors:
        console.print(f"\n[red]Errors ({len(summary.errors)}):[/red]")
        for d, msg in summary.errors[:20]:
            console.print(f"  {d}: {msg}")
        if len(summary.errors) > 20:
            console.print(f"  … and {len(summary.errors)-20} more")

    if summary.trading_days_skipped > summary.trading_days_total * 0.1:
        console.print("[yellow]⚠️  More than 10% of days were skipped — check data coverage.[/yellow]")
        raise typer.Exit(1)


# ─── refresh: weekly/monthly/quarterly ML governance ────────────────────────
refresh_app = typer.Typer(no_args_is_help=True,
                           help="Periodic ML refresh: weekly retrain, monthly health, quarterly review.")
app.add_typer(refresh_app, name="refresh")


@refresh_app.command("weekly")
def refresh_weekly(
    in_sample_start: str = typer.Option("2024-01-02", "--in-sample-start"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Weekly Champion-Challenger: train all candidates, maybe promote per slot."""
    _override_mode(mode)
    is_start = dt.datetime.strptime(in_sample_start, "%Y-%m-%d").date()

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.refresh import run_weekly_refresh
    engine = get_engine(get_settings())
    result = run_weekly_refresh(engine, in_sample_start=is_start,
                                 on_log=lambda m: console.print(m))
    console.print(f"\n[bold green]Done.[/bold green]  promoted={result['promoted_slots']}")
    console.print(f"Report: {result['report_path']}")


@refresh_app.command("monthly")
def refresh_monthly(mode: str = typer.Option("manual", "--mode")) -> None:
    """Monthly walk-forward stability check + recent-30d health."""
    _override_mode(mode)
    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.refresh import run_monthly_refresh
    engine = get_engine(get_settings())
    result = run_monthly_refresh(engine, on_log=lambda m: console.print(m))
    if result["alerts"]:
        console.print(f"\n[bold red]🚨 {len(result['alerts'])} alerts[/bold red]")
    else:
        console.print(f"\n[bold green]✅ All healthy[/bold green]")
    console.print(f"Report: {result['report_path']}")


@refresh_app.command("quarterly")
def refresh_quarterly(mode: str = typer.Option("manual", "--mode")) -> None:
    """Quarterly architecture review — re-test rejected model families (Kronos etc)."""
    _override_mode(mode)
    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.refresh import run_quarterly_refresh
    engine = get_engine(get_settings())
    result = run_quarterly_refresh(engine, on_log=lambda m: console.print(m))
    console.print(f"\nReport: {result['report_path']}")


# ─── registry management ────────────────────────────────────────────────────
registry_app = typer.Typer(no_args_is_help=True,
                            help="Inspect and manage the ML model registry (active model per slot).")
app.add_typer(registry_app, name="registry")


@registry_app.command("status")
def registry_status(mode: str = typer.Option("manual", "--mode")) -> None:
    """Show active model per slot + recent promotion history."""
    _override_mode(mode)
    from rich.table import Table
    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.champion_challenger import (
        get_active_for_slot, list_versions_for_slot,
        SLOT_AGGRESSIVE, SLOT_CONSERVATIVE, SLOT_HEURISTIC,
    )
    engine = get_engine(get_settings())

    console.print("[bold cyan]Active models per slot[/bold cyan]")
    t = Table(show_header=True, header_style="bold")
    t.add_column("Slot")
    t.add_column("Active version")
    t.add_column("Model")
    t.add_column("Activated at")
    t.add_column("T5_Mean")
    t.add_column("Sharpe")
    for slot in (SLOT_AGGRESSIVE, SLOT_CONSERVATIVE, SLOT_HEURISTIC):
        a = get_active_for_slot(engine, slot)
        if a is None:
            t.add_row(slot, "—", "—", "—", "—", "—")
        else:
            metrics = a.get("metrics") or {}
            if isinstance(metrics, str):
                import json as _j
                metrics = _j.loads(metrics)
            t.add_row(
                slot,
                a["model_version"],
                a["model_name"],
                str(a.get("activated_at", "—")),
                f"{(metrics.get('oos_top5_avg_return') or 0)*100:+.2f}%",
                f"{metrics.get('oos_top5_sharpe') or 0:.2f}",
            )
    console.print(t)

    console.print("\n[bold cyan]Recent versions per slot[/bold cyan]")
    for slot in (SLOT_AGGRESSIVE, SLOT_CONSERVATIVE):
        console.print(f"\n[bold]{slot}[/bold]")
        rows = list_versions_for_slot(engine, slot, limit=10)
        if not rows:
            console.print("  (no versions yet)")
            continue
        for r in rows:
            mark = " ⭐" if r["is_active"] else ""
            console.print(
                f"  {r['model_version']}  {r['model_name']}  "
                f"T5_mean={float(r['top5_mean'] or 0)*100:+.2f}%  "
                f"sharpe={float(r['sharpe'] or 0):.2f}{mark}"
            )


@registry_app.command("promote")
def registry_promote(
    slot: str = typer.Argument(..., help="aggressive | conservative"),
    version: str = typer.Argument(..., help="model_version to activate"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Manually promote a model version to active for a slot."""
    _override_mode(mode)
    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.champion_challenger import activate_version
    engine = get_engine(get_settings())
    prev = activate_version(
        engine, slot, version,
        reason=f"manual override by operator on {dt.date.today()}",
        event_type="manual_override",
    )
    console.print(f"[bold green]✓ {slot} active: {prev or 'none'} → {version}[/bold green]")


@registry_app.command("rollback")
def registry_rollback(
    slot: str = typer.Argument(..., help="aggressive | conservative"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Roll back to the previously active model version (one step back)."""
    _override_mode(mode)
    from sqlalchemy import text
    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.champion_challenger import activate_version
    engine = get_engine(get_settings())
    with engine.connect() as c:
        # Find most recent deactivated version
        row = c.execute(text("""
            SELECT model_version FROM ningbo.model_registry
            WHERE slot=:slot AND is_active=FALSE AND deactivated_at IS NOT NULL
            ORDER BY deactivated_at DESC LIMIT 1
        """), {"slot": slot}).fetchone()
    if not row:
        console.print(f"[red]No prior version to roll back to for {slot}[/red]")
        raise typer.Exit(1)
    prev_version = row[0]
    activate_version(
        engine, slot, prev_version,
        reason=f"emergency rollback by operator on {dt.date.today()}",
        event_type="emergency_rollback",
    )
    console.print(f"[bold yellow]↩  Rolled back {slot} → {prev_version}[/bold yellow]")


# ─── tracking (bulk post-backfill pass) ──────────────────────────────────────
@app.command("tracking")
def tracking_command(
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD"),
    scoring: str = typer.Option("heuristic", "--scoring"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Compute tracking + outcomes in bulk SQL (post-backfill pass).

    Run AFTER 'backfill --skip-tracking' completes (e.g. after parallel years).
    Uses a single efficient SQL batch instead of per-day Python loops.

    Example:
        ifa ningbo tracking --start 2021-01-04 --end 2026-04-30
    """
    _override_mode(mode)

    import time
    start_date = dt.datetime.strptime(start, "%Y-%m-%d").date()
    end_date   = dt.datetime.strptime(end,   "%Y-%m-%d").date()

    console.print(
        f"[cyan]ningbo tracking (bulk SQL)  {start_date} → {end_date}  "
        f"scoring={scoring}[/cyan]"
    )

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.backfill import run_bulk_tracking_sql

    settings = get_settings()
    engine = get_engine(settings)

    t0 = time.time()
    result = run_bulk_tracking_sql(
        engine, start_date, end_date,
        scoring_mode=scoring,
        on_log=lambda m: console.print(m),
    )
    console.print(
        f"\n[bold green]Tracking complete[/bold green]  "
        f"tracking_rows={result['tracking_rows_inserted']:,}  "
        f"outcomes={result['outcomes_upserted']:,}  "
        f"elapsed={time.time()-t0:.0f}s"
    )


# ─── ML training v2 (Phase 3.B — full candidate pool + ranking) ─────────────
@app.command("train-v2")
def train_v2_command(
    in_sample_end: str = typer.Option("2025-09-30", "--in-sample-end"),
    oos_end: str = typer.Option("2026-04-30", "--oos-end"),
    in_sample_start: str = typer.Option("2024-01-02", "--in-sample-start"),
    version: str = typer.Option(None, "--version"),
    activate: bool = typer.Option(False, "--activate/--no-activate"),
    best_by: str = typer.Option("top5_avg_return", "--best-by",
                                help="top5_avg_return | top5_med_return | ndcg5 | oos_auc"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Phase 3.B training: full candidate pool + LR/RF/XGB-clf/XGB-ranker.

    Requires candidates_daily + candidate_outcomes populated
    (via `ifa ningbo backfill-candidates`).
    """
    _override_mode(mode)
    import time as _time

    is_start = dt.datetime.strptime(in_sample_start, "%Y-%m-%d").date()
    is_end   = dt.datetime.strptime(in_sample_end,   "%Y-%m-%d").date()
    oos_end_d = dt.datetime.strptime(oos_end,        "%Y-%m-%d").date()

    console.print(f"[cyan]ningbo train-v2 (full pool + ranker): "
                  f"{is_start} → {is_end} → {oos_end_d}[/cyan]")

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.features_v2 import build_candidate_feature_matrix
    from ifa.families.ningbo.ml.trainer_v2  import train_models_v2
    from ifa.families.ningbo.ml.registry    import save_artifacts, set_active

    engine = get_engine(get_settings())

    t0 = _time.time()
    console.print("\n[bold]1. Building feature matrix from candidates_daily…[/bold]")
    feat_df = build_candidate_feature_matrix(engine, is_start, oos_end_d, include_outcomes=True)
    console.print(
        f"   feature_df: {feat_df.shape}  "
        f"[{_time.time()-t0:.1f}s]  "
        f"({feat_df['rec_date'].nunique()} days, "
        f"{(feat_df['outcome_status']=='take_profit').sum()} take_profit, "
        f"{(feat_df['outcome_status']=='stop_loss').sum()} stop_loss)"
    )

    t1 = _time.time()
    console.print("\n[bold]2. Training models (LR + RF + XGB-clf + XGB-ranker)…[/bold]")
    art = train_models_v2(
        feat_df, in_sample_end=is_end, model_version=version, best_by=best_by,
        on_log=lambda m: console.print(f"   {m}"),
    )
    console.print(f"   training done in {_time.time()-t1:.1f}s")

    # ── Saving (use TrainingArtifacts adapter for registry compatibility) ────
    from ifa.families.ningbo.ml.trainer import TrainingArtifacts
    art_adapter = TrainingArtifacts(
        model_version=art.model_version,
        feature_columns=art.feature_columns,
        base_models={k: v for k, v in art.base_models.items() if not isinstance(v, tuple)},
        stacking_model=art.production_model,
        metrics={
            n: __import__("ifa.families.ningbo.ml.trainer", fromlist=["ModelResult"]).ModelResult(
                name=n, model=None,
                train_auc=float("nan"),
                oos_auc=m.oos_auc, oos_avg_precision=m.oos_avg_precision,
                oos_brier=float("nan"), oos_log_loss=float("nan"),
                oos_top5_precision=m.oos_top5_precision,
                oos_top5_avg_return=m.oos_top5_avg_return,
                feature_importances=m.feature_importances,
            )
            for n, m in art.metrics.items()
        },
        train_range=art.train_range, oos_range=art.oos_range,
        n_train=art.n_train, n_oos=art.n_oos,
        pos_rate_train=0.0, pos_rate_oos=0.0,
    )

    saved = save_artifacts(art_adapter)
    console.print(f"\n[bold]3. Saved →[/bold] {saved}")
    if activate:
        set_active(art.model_version)
        console.print(f"   [bold green]→ activated[/bold green]")

    # ── Comparison table ────────────────────────────────────────────────────
    from rich.table import Table
    console.print(f"\n[bold cyan]V2 Model Comparison — OOS metrics[/bold cyan]")
    t = Table(show_header=True, header_style="bold")
    t.add_column("Model", style="bold")
    t.add_column("Obj")
    t.add_column("AUC",        justify="right")
    t.add_column("NDCG@5",     justify="right", style="cyan")
    t.add_column("Top5_Prec",  justify="right", style="green")
    t.add_column("Top5_Mean",  justify="right", style="cyan")
    t.add_column("Top5_Med",   justify="right", style="cyan")

    for name in ("heuristic", "lr", "rf", "xgb_clf", "xgb_ranker"):
        m = art.metrics.get(name)
        if m is None: continue
        def _f(v): return f"{v:.3f}" if v == v else "—"
        style = "bold yellow" if name == ("xgb_ranker" if best_by == "ndcg5" else max(
            ("lr","rf","xgb_clf","xgb_ranker"),
            key=lambda n: getattr(art.metrics.get(n, None), {
                "top5_avg_return":"oos_top5_avg_return",
                "top5_med_return":"oos_top5_med_return",
                "ndcg5":"oos_ndcg5",
                "oos_auc":"oos_auc",
            }.get(best_by, "oos_top5_avg_return"), -999) if art.metrics.get(n) else -999,
        )) else ""
        t.add_row(
            name, m.objective, _f(m.oos_auc), _f(m.oos_ndcg5),
            f"{m.oos_top5_precision*100:.1f}%",
            f"{m.oos_top5_avg_return*100:+.2f}%",
            f"{m.oos_top5_med_return*100:+.2f}%",
            style=style,
        )
    console.print(t)
    console.print(f"\n[dim]version={art.model_version}  |  "
                  f"train={art.n_train:,} candidates / {art.n_train_days} days  |  "
                  f"oos={art.n_oos:,} / {art.n_oos_days} days[/dim]")


# ─── backfill historical dual recommendations ───────────────────────────────
@app.command("backfill-dual")
def backfill_dual_command(
    days: int = typer.Option(30, "--days", help="trading days back to backfill (default 30)"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Score historical candidate pools with active ML models, persist top-10 per slot.

    Run after `ifa ningbo refresh weekly` activates ML models. Populates
    ml_aggressive / ml_conservative recommendations for past dates so the
    consensus matrix shows ★★★+ stocks (rather than only ★/★★ from heuristic-only).
    """
    _override_mode(mode)
    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.backfill_dual import backfill_dual_recs
    engine = get_engine(get_settings())
    result = backfill_dual_recs(engine, days_back=days, on_log=lambda m: console.print(m))
    console.print(f"\n[bold green]Done.[/bold green]  "
                  f"days={result['days_processed']}  inserted={result['inserted']}")


# ─── Candidate outcomes only (post-backfill bulk SQL) ────────────────────────
@app.command("candidate-outcomes")
def candidate_outcomes_command(
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Compute 15-day forward labels for already-backfilled candidates.

    Run after `backfill-candidates --skip-outcomes` finishes.  Pure SQL, ~2 min.
    """
    _override_mode(mode)
    import time
    s = dt.datetime.strptime(start, "%Y-%m-%d").date()
    e = dt.datetime.strptime(end,   "%Y-%m-%d").date()

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.candidates import compute_candidate_outcomes

    engine = get_engine(get_settings())
    t0 = time.time()
    result = compute_candidate_outcomes(
        engine, s, e, on_log=lambda m: console.print(m),
    )
    console.print(
        f"\n[bold green]Done[/bold green]  "
        f"{result['outcomes_upserted']:,} outcomes  "
        f"elapsed={time.time()-t0:.0f}s"
    )


# ─── ML candidates backfill (Phase 3.B) ──────────────────────────────────────
@app.command("backfill-candidates")
def backfill_candidates_command(
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD"),
    skip_outcomes: bool = typer.Option(False, "--skip-outcomes",
                                       help="Skip the bulk SQL outcomes pass"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Backfill the FULL candidate pool (~310 hits/day) for ML training.

    Unlike `backfill` (which writes top-5), this writes EVERY strategy hit
    to ningbo.candidates_daily.  Required for Phase 3.B ML training to
    avoid sample selection bias.

    Then computes 15-day forward labels in one bulk SQL pass.

    Example:
        ifa ningbo backfill-candidates --start 2024-01-02 --end 2026-04-30
    """
    _override_mode(mode)
    import time

    start_d = dt.datetime.strptime(start, "%Y-%m-%d").date()
    end_d   = dt.datetime.strptime(end,   "%Y-%m-%d").date()
    console.print(f"[cyan]Candidate backfill {start_d} → {end_d}[/cyan]")

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.candidates import (
        backfill_candidates, compute_candidate_outcomes,
    )

    engine = get_engine(get_settings())

    t0 = time.time()
    summary = backfill_candidates(
        engine, start_d, end_d,
        on_log=lambda m: console.print(m),
    )
    console.print(
        f"\n[bold green]Candidate backfill done[/bold green]  "
        f"{summary.trading_days_processed} days  "
        f"{summary.candidates_inserted:,} candidates "
        f"(sniper={summary.by_strategy.get('sniper',0):,}, "
        f"basin={summary.by_strategy.get('treasure_basin',0):,}, "
        f"hyd={summary.by_strategy.get('half_year_double',0):,})  "
        f"elapsed={time.time()-t0:.0f}s"
    )

    if not skip_outcomes:
        console.print("\n[cyan]Computing 15-day forward outcomes (bulk SQL)…[/cyan]")
        t1 = time.time()
        result = compute_candidate_outcomes(
            engine, start_d, end_d, on_log=lambda m: console.print(m),
        )
        console.print(
            f"[bold green]Outcomes done[/bold green]  "
            f"{result['outcomes_upserted']:,} rows  elapsed={time.time()-t1:.0f}s"
        )


# ─── stats ────────────────────────────────────────────────────────────────────
@app.command("stats")
def stats_command(
    scoring: str = typer.Option("heuristic", "--scoring"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Print strategy performance statistics from backfill results.

    Shows win rate / loss rate / average return per strategy per year.
    Run after backfill to validate historical signal quality.
    """
    _override_mode(mode)

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.backfill import print_backfill_stats

    settings = get_settings()
    engine = get_engine(settings)
    print_backfill_stats(engine, scoring_mode=scoring)


# ─── train (Phase 3+) ────────────────────────────────────────────────────────
@app.command("train")
def train_command(
    in_sample_start: str = typer.Option("2024-01-02", "--in-sample-start"),
    in_sample_end: str = typer.Option("2025-09-30", "--in-sample-end"),
    oos_end: str = typer.Option("2026-04-30", "--oos-end"),
    version: str = typer.Option(None, "--version", help="e.g. v2026.05.02; auto if None"),
    activate: bool = typer.Option(False, "--activate/--no-activate",
                                  help="If set, mark this version as active after training"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Train ML scoring models (LR + RF + XGB + Stacking) with calibration.

    Strict temporal split. Trains on [in-sample-start, in-sample-end],
    evaluates on (in-sample-end, oos-end].

    Example:
        ifa ningbo train --in-sample-end 2025-09-30 --oos-end 2026-04-30 --activate
    """
    _override_mode(mode)
    import time as _time

    is_start = dt.datetime.strptime(in_sample_start, "%Y-%m-%d").date()
    is_end   = dt.datetime.strptime(in_sample_end,   "%Y-%m-%d").date()
    oos_end_d = dt.datetime.strptime(oos_end,        "%Y-%m-%d").date()

    console.print(
        f"[cyan]ningbo train: in-sample {is_start} → {is_end}, OOS → {oos_end_d}[/cyan]"
    )

    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from ifa.families.ningbo.ml.features import build_feature_matrix
    from ifa.families.ningbo.ml.trainer  import train_models
    from ifa.families.ningbo.ml.registry import save_artifacts, set_active

    engine = get_engine(get_settings())

    t0 = _time.time()
    console.print(f"\n[bold]1. Building feature matrix…[/bold]")
    feat_df = build_feature_matrix(engine, is_start, oos_end_d, include_outcomes=True)
    console.print(
        f"   feature_df: {feat_df.shape}  "
        f"({len(feat_df)} recs, {len(feat_df.columns)} cols)  "
        f"[{_time.time()-t0:.1f}s]"
    )

    t1 = _time.time()
    console.print(f"\n[bold]2. Training models (this takes 30-90s)…[/bold]")
    art = train_models(
        feat_df, in_sample_end=is_end, model_version=version,
        on_log=lambda m: console.print(f"   {m}"),
    )
    console.print(f"   training done in {_time.time()-t1:.1f}s")

    t2 = _time.time()
    console.print(f"\n[bold]3. Saving artifacts…[/bold]")
    saved_path = save_artifacts(art)
    console.print(f"   → {saved_path}")
    if activate:
        set_active(art.model_version)
        console.print(f"   [bold green]→ activated as ningbo's current ML model[/bold green]")
    else:
        console.print(
            f"   (run [cyan]ifa ningbo params freeze ml {art.model_version}[/cyan] to activate)"
        )

    # ── Final summary table ──────────────────────────────────────────────────
    from rich.table import Table
    console.print(f"\n[bold cyan]Model Comparison — OOS metrics[/bold cyan]")
    t = Table(show_header=True, header_style="bold")
    t.add_column("Model", style="bold")
    t.add_column("AUC",        justify="right")
    t.add_column("AvgPrec",    justify="right")
    t.add_column("Brier",      justify="right")
    t.add_column("Top5_Prec",  justify="right", style="green")
    t.add_column("Top5_AvgRet",justify="right", style="cyan")

    for name in ("heuristic", "lr", "rf", "xgb", "stacking"):
        m = art.metrics.get(name)
        if m is None:
            continue
        style = "bold yellow" if name == "stacking" else ""
        def _f(v): return f"{v:.3f}" if v == v else "—"  # NaN check
        t.add_row(
            name, _f(m.oos_auc), _f(m.oos_avg_precision), _f(m.oos_brier),
            f"{m.oos_top5_precision*100:.1f}%",
            f"{m.oos_top5_avg_return*100:+.2f}%",
            style=style,
        )
    console.print(t)
    console.print(
        f"\n[dim]Total: {_time.time()-t0:.1f}s  |  "
        f"version={art.model_version}  |  "
        f"train_n={art.n_train}  oos_n={art.n_oos}[/dim]"
    )


# ─── params ──────────────────────────────────────────────────────────────────
@params_app.command("list")
def params_list_command(
    strategy: str = typer.Option(None, "--strategy"),
    scoring_mode: str = typer.Option(None, "--scoring-mode"),
):
    """List ningbo strategy / model param versions."""
    console.print("[yellow]NOT IMPLEMENTED — Phase 3.7[/yellow]")
    raise typer.Exit(1)


@params_app.command("freeze")
def params_freeze_command(
    strategy: str = typer.Argument(...),
    version_tag: str = typer.Argument(...),
):
    """Mark a param version as active (used by next report runs)."""
    console.print(f"[yellow]NOT IMPLEMENTED — Phase 3.7: freeze {strategy} {version_tag}[/yellow]")
    raise typer.Exit(1)
