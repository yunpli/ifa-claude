"""`ifa generate macro ...` — render a Macro morning or evening report."""
from __future__ import annotations

import datetime as dt
import os

import typer
from rich.console import Console

from ifa.core.report.timezones import parse_bjt_cutoff

console = Console()
app = typer.Typer(no_args_is_help=True, help="Render a structured iFA report.")


def _override_mode(mode: str | None) -> None:
    if mode:
        os.environ["IFA_RUN_MODE"] = mode


@app.command("macro")
def macro(
    slot: str = typer.Option(..., "--slot", help="morning | evening"),
    report_date: str = typer.Option(..., "--report-date", help="YYYY-MM-DD (Beijing date)"),
    cutoff_time: str | None = typer.Option(
        None, "--cutoff-time",
        help="HH:MM Beijing time. Default 08:45 for morning, 17:30 for evening."),
    triggered_by: str | None = typer.Option(None, "--triggered-by"),
    mode: str | None = typer.Option(None, "--mode", help="test|manual|production"),
) -> None:
    """Render a Macro report end-to-end and save HTML to the output directory."""
    _override_mode(mode)

    rd = dt.datetime.strptime(report_date, "%Y-%m-%d").date()
    if slot == "morning":
        cutoff_str = cutoff_time or "08:45"
        from ifa.families.macro.morning import run_macro_morning
        cutoff_utc = parse_bjt_cutoff(report_date, cutoff_str)
        path = run_macro_morning(
            report_date=rd,
            data_cutoff_at=cutoff_utc,
            triggered_by=triggered_by,
            on_log=lambda m: console.print(f"  {m}"),
        )
    elif slot == "evening":
        cutoff_str = cutoff_time or "17:30"
        from ifa.families.macro.evening import run_macro_evening
        cutoff_utc = parse_bjt_cutoff(report_date, cutoff_str)
        path = run_macro_evening(
            report_date=rd,
            data_cutoff_at=cutoff_utc,
            triggered_by=triggered_by,
            on_log=lambda m: console.print(f"  {m}"),
        )
    else:
        raise typer.BadParameter(f"slot must be 'morning' or 'evening' (got {slot})")

    console.print(f"\n[bold green]Report saved:[/bold green] {path}")


@app.command("asset")
def asset(
    slot: str = typer.Option(..., "--slot", help="morning | evening"),
    report_date: str = typer.Option(..., "--report-date", help="YYYY-MM-DD (Beijing date)"),
    cutoff_time: str | None = typer.Option(
        None, "--cutoff-time",
        help="HH:MM Beijing time. Default 08:50 for morning, 17:30 for evening."),
    triggered_by: str | None = typer.Option(None, "--triggered-by"),
    mode: str | None = typer.Option(None, "--mode", help="test|manual|production"),
) -> None:
    """Render an Asset (cross-asset transmission) report and save HTML."""
    _override_mode(mode)

    rd = dt.datetime.strptime(report_date, "%Y-%m-%d").date()
    if slot == "morning":
        cutoff_str = cutoff_time or "08:50"
        from ifa.families.asset.morning import run_asset_morning
        cutoff_utc = parse_bjt_cutoff(report_date, cutoff_str)
        path = run_asset_morning(
            report_date=rd,
            data_cutoff_at=cutoff_utc,
            triggered_by=triggered_by,
            on_log=lambda m: console.print(f"  {m}"),
        )
    elif slot == "evening":
        cutoff_str = cutoff_time or "17:30"
        from ifa.families.asset.evening import run_asset_evening
        cutoff_utc = parse_bjt_cutoff(report_date, cutoff_str)
        path = run_asset_evening(
            report_date=rd,
            data_cutoff_at=cutoff_utc,
            triggered_by=triggered_by,
            on_log=lambda m: console.print(f"  {m}"),
        )
    else:
        raise typer.BadParameter(f"slot must be 'morning' or 'evening' (got {slot})")

    console.print(f"\n[bold green]Report saved:[/bold green] {path}")
