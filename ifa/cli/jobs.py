"""`ifa job ...` — pre-job orchestration commands."""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ifa.config import RunMode, get_settings
from ifa.jobs.common.runner import JobReport
from ifa.jobs.macro_policy_memory import run_macro_policy_memory
from ifa.jobs.macro_text_capture import run_macro_text_capture

console = Console()
app = typer.Typer(no_args_is_help=True, help="Pre-jobs that feed report runs.")


def _print_report(report: JobReport) -> None:
    console.print(f"\n[bold]{report.job_name}[/bold]  mode={report.run_mode.value}")
    duration = (report.finished_at - report.started_at).total_seconds() if report.finished_at else 0
    console.print(f"  started={report.started_at.isoformat()}  duration={duration:.1f}s")
    console.print(f"  totals: rows_scanned={report.rows_scanned_total}  "
                  f"candidates={report.candidates_filtered_total}  extracted={report.extracted_total}")

    table = Table(title="Per-source breakdown", show_lines=False)
    table.add_column("Source", style="cyan")
    table.add_column("Rows", justify="right")
    table.add_column("Cands", justify="right")
    table.add_column("Extr", justify="right")
    table.add_column("Batches", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("New high-water", overflow="fold")
    for s in report.per_source.values():
        table.add_row(
            s.label, str(s.rows_scanned), str(s.candidates_filtered),
            str(s.extracted), str(s.batches_attempted), str(s.batches_failed),
            s.new_high_water.isoformat() if s.new_high_water else "—",
        )
    console.print(table)

    if report.errors:
        console.print(f"\n[yellow]{len(report.errors)} error(s):[/yellow]")
        for e in report.errors[:10]:
            console.print(f"  · {e}")
        if len(report.errors) > 10:
            console.print(f"  … {len(report.errors) - 10} more (suppressed)")


def _override_mode(mode: str | None) -> None:
    """Allow `--mode test` on the CLI to override IFA_RUN_MODE before settings are read."""
    if mode:
        import os
        os.environ["IFA_RUN_MODE"] = mode


@app.command("text-capture")
def text_capture(
    lookback_days: int = typer.Option(90, "--lookback-days", help="How far back to scan on first run."),
    batch_size: int = typer.Option(5, "--batch-size"),
    mode: str | None = typer.Option(None, "--mode", help="Override IFA_RUN_MODE: test|manual|production"),
) -> None:
    """Run macro_text_derived_capture_job: 新增贷款 / 贷款余额 etc. from major_news/news/npr."""
    _override_mode(mode)
    settings = get_settings()
    console.print(f"[bold]Running macro_text_derived_capture[/bold]  "
                  f"mode={settings.run_mode.value}  lookback_days={lookback_days}")
    report = run_macro_text_capture(
        lookback_days=lookback_days,
        batch_size=batch_size,
        on_log=lambda m: console.print(f"  {m}"),
    )
    _print_report(report)


@app.command("policy-memory")
def policy_memory(
    lookback_days: int = typer.Option(90, "--lookback-days"),
    batch_size: int = typer.Option(5, "--batch-size"),
    mode: str | None = typer.Option(None, "--mode"),
) -> None:
    """Run macro_policy_event_memory_job: curate active policy events."""
    _override_mode(mode)
    settings = get_settings()
    console.print(f"[bold]Running macro_policy_event_memory[/bold]  "
                  f"mode={settings.run_mode.value}  lookback_days={lookback_days}")
    report = run_macro_policy_memory(
        lookback_days=lookback_days,
        batch_size=batch_size,
        on_log=lambda m: console.print(f"  {m}"),
    )
    _print_report(report)
