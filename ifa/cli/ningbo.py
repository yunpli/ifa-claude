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
        False, "--skip-tracking", help="Skip recomputing tracking + outcomes"
    ),
    skip_llm: bool = typer.Option(
        True, "--skip-llm/--with-llm",
        help="Skip LLM narrative (default: skip; only useful for fresh-day reports)",
    ),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Backfill historical recommendations + tracking + outcomes.

    Reads from existing smartmoney.raw_* tables (no ETL).
    Generates ningbo signals for every trading day in [start, end].
    """
    _override_mode(mode)
    console.print(f"[cyan]ningbo backfill {start} → {end}, scoring={scoring}[/cyan]")
    console.print("[yellow]NOT IMPLEMENTED — Phase 2.1[/yellow]")
    raise typer.Exit(1)


# ─── train (Phase 3+) ────────────────────────────────────────────────────────
@app.command("train")
def train_command(
    in_sample_start: str = typer.Option("2021-01-04", "--in-sample-start"),
    in_sample_end: str = typer.Option("2025-10-31", "--in-sample-end"),
    oos_start: str = typer.Option("2025-11-01", "--oos-start"),
    oos_end: str = typer.Option("2026-04-30", "--oos-end"),
    version: str = typer.Option(None, "--version", help="e.g. v2026.05; auto-generated if None"),
    models: str = typer.Option(
        "lr,rf,xgb,lgbm,catboost,stacking",
        "--models",
        help="Comma list: lr | rf | xgb | lgbm | catboost | stacking",
    ),
    calibrate: bool = typer.Option(True, "--calibrate/--no-calibrate"),
    mode: str = typer.Option("manual", "--mode"),
) -> None:
    """Train ML scoring models on historical recommendations + outcomes."""
    _override_mode(mode)
    console.print(f"[cyan]ningbo train: {models}; in-sample {in_sample_start}→{in_sample_end}[/cyan]")
    console.print("[yellow]NOT IMPLEMENTED — Phase 3.3[/yellow]")
    raise typer.Exit(1)


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
