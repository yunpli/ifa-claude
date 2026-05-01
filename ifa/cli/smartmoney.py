"""`ifa smartmoney ...` CLI — ETL / backfill / backtest / report."""
from __future__ import annotations

import datetime as dt
import os

import typer
from rich.console import Console
from rich.table import Table

console = Console()
app = typer.Typer(no_args_is_help=True, help="Smart Money Flow Intelligence module.")


def _override_mode(mode: str | None) -> None:
    if mode:
        os.environ["IFA_RUN_MODE"] = mode


def _print_day_stats(s) -> None:
    if not s.tables:
        console.print(f"[yellow]{s.trade_date}: nothing loaded (non-trading day or all errors).[/yellow]")
        return
    t = Table(title=f"ETL stats · {s.trade_date}", show_lines=False)
    t.add_column("Table", style="cyan")
    t.add_column("Rows", justify="right")
    t.add_column("Seconds", justify="right")
    t.add_column("Error", overflow="fold")
    for ts in s.tables:
        t.add_row(ts.table, f"{ts.rows_loaded:,}", f"{ts.seconds:.1f}",
                  ts.error or "")
    console.print(t)
    console.print(f"[bold]Total: {s.total_rows:,} rows in {s.total_seconds:.1f}s[/bold]")


@app.command("etl")
def etl(
    report_date: str = typer.Option(..., "--report-date", help="YYYY-MM-DD trading date (Beijing)"),
    skip_chips: bool = typer.Option(False, "--skip-chips", help="Skip per-stock cyq_chips fetch"),
    mode: str | None = typer.Option(None, "--mode"),
) -> None:
    """One-day raw ETL into smartmoney.raw_* tables."""
    _override_mode(mode)
    from ifa.families.smartmoney.etl.runner import run_etl_for_date
    d = dt.datetime.strptime(report_date, "%Y-%m-%d").date()
    console.print(f"[bold]SmartMoney ETL · {d}[/bold]")
    s = run_etl_for_date(trade_date=d, on_log=lambda m: console.print(f"  {m}"),
                        skip_chips=skip_chips)
    _print_day_stats(s)


@app.command("evening")
def evening(
    report_date: str = typer.Option(..., "--report-date", help="YYYY-MM-DD"),
    cutoff_time: str = typer.Option("18:00", "--cutoff-time"),
    triggered_by: str | None = typer.Option(None, "--triggered-by"),
    mode: str | None = typer.Option(None, "--mode"),
) -> None:
    """Render a SmartMoney evening report and save HTML."""
    _override_mode(mode)
    from ifa.core.report.timezones import parse_bjt_cutoff
    from ifa.families.smartmoney.evening import run_smartmoney_evening
    rd = dt.datetime.strptime(report_date, "%Y-%m-%d").date()
    cutoff_utc = parse_bjt_cutoff(report_date, cutoff_time)
    path = run_smartmoney_evening(
        report_date=rd, data_cutoff_at=cutoff_utc,
        triggered_by=triggered_by,
        on_log=lambda m: console.print(f"  {m}"),
    )
    console.print(f"\n[bold green]Report saved:[/bold green] {path}")


@app.command("backfill")
def backfill(
    start: str = typer.Option(..., "--start", help="YYYYMMDD"),
    end: str = typer.Option(..., "--end", help="YYYYMMDD"),
    skip_chips: bool = typer.Option(True, "--skip-chips/--with-chips",
                                     help="Default skip per-stock chips for speed"),
    mode: str | None = typer.Option(None, "--mode"),
) -> None:
    """Multi-day raw ETL backfill."""
    _override_mode(mode)
    from ifa.families.smartmoney.etl.runner import run_backfill
    s = dt.datetime.strptime(start, "%Y%m%d").date()
    e = dt.datetime.strptime(end, "%Y%m%d").date()
    console.print(f"[bold]SmartMoney backfill · {s} → {e}[/bold]")
    days = run_backfill(start=s, end=e,
                        on_log=lambda m: console.print(f"  {m}"),
                        skip_chips=skip_chips)
    console.print(f"\n[bold green]Backfill done.[/bold green] {len(days)} days loaded.")
