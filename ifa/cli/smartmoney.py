"""`ifa smartmoney ...` CLI — ETL / backfill / backtest / report."""
from __future__ import annotations

import datetime as dt
import os

import typer
from rich.console import Console
from rich.table import Table

console = Console()
app = typer.Typer(no_args_is_help=True, help="Smart Money Flow Intelligence module.")

# Sub-app for param management
params_app = typer.Typer(no_args_is_help=True, help="Manage SmartMoney param versions.")
app.add_typer(params_app, name="params")

# Sub-app for backtest listing / inspection
bt_app = typer.Typer(no_args_is_help=True, help="Browse backtest results.")
app.add_typer(bt_app, name="bt")


def _override_mode(mode: str | None) -> None:
    if mode:
        os.environ["IFA_RUN_MODE"] = mode


def _engine():
    from ifa.core.db.engine import get_engine
    return get_engine()


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


# ─── Factor compute ───────────────────────────────────────────────────────────

@app.command("compute")
def compute(
    report_date: str = typer.Option(None, "--report-date", help="YYYY-MM-DD (single day)"),
    start: str = typer.Option(None, "--start", help="YYYY-MM-DD (range start)"),
    end: str = typer.Option(None, "--end", help="YYYY-MM-DD (range end)"),
    mode: str | None = typer.Option(None, "--mode"),
) -> None:
    """Compute factors + role/cycle + leader/candidate for a date or range.

    Runs in order: flow factors → market state → role → cycle → leaders → candidates.
    Requires raw ETL data to already be loaded.
    """
    _override_mode(mode)
    from ifa.families.smartmoney.factors.flow import compute_factors_for_date
    from ifa.families.smartmoney.factors.liquidity import compute_market_state, write_market_state
    from ifa.families.smartmoney.factors.role import compute_roles_for_date
    from ifa.families.smartmoney.factors.cycle import compute_phases_for_date, write_sector_states
    from ifa.families.smartmoney.factors.leader import compute_leaders_for_date, write_stock_signals
    from ifa.families.smartmoney.factors.candidate import compute_candidates_for_date
    from ifa.families.smartmoney.params.store import get_active_params

    engine = _engine()
    params = get_active_params(engine)

    # Resolve date(s)
    if report_date:
        dates = [dt.datetime.strptime(report_date, "%Y-%m-%d").date()]
    elif start and end:
        s = dt.datetime.strptime(start, "%Y-%m-%d").date()
        e = dt.datetime.strptime(end, "%Y-%m-%d").date()
        # Fetch trade calendar to iterate only trading days
        from ifa.core.db.engine import get_engine as _ge
        from sqlalchemy import text as _t
        with engine.connect() as conn:
            rows = conn.execute(_t("""
                SELECT DISTINCT trade_date FROM smartmoney.raw_daily
                WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date
            """), {"s": s, "e": e}).fetchall()
        dates = [r[0] for r in rows]
        if not dates:
            console.print(f"[yellow]No raw_daily data found for [{s}, {e}] — run backfill first.[/yellow]")
            raise typer.Exit(1)
    else:
        console.print("[red]Provide --report-date or --start + --end[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]SmartMoney Compute · {dates[0]} → {dates[-1]}[/bold]  ({len(dates)} days)")

    for i, td in enumerate(dates, 1):
        console.print(f"\n[cyan]--- {i}/{len(dates)} · {td} ---[/cyan]")

        # 1. Flow factors → factor_daily
        try:
            written = compute_factors_for_date(engine, td, params=params)
            total_f = sum(written.values())
            console.print(f"  factors:      {written}  ({total_f} rows)")
        except Exception as exc:
            console.print(f"  [red]factors FAIL:[/red] {exc}")
            continue

        # 2. Market state → market_state_daily
        try:
            snap = compute_market_state(engine, td, params=params)
            write_market_state(engine, snap)
            console.print(f"  market_state: {snap.market_state}  (total {snap.total_amount:.0f}亿)")
        except Exception as exc:
            console.print(f"  [yellow]market_state WARN:[/yellow] {exc}")

        # 3. Roles → sector_state_daily (role column)
        try:
            roles = compute_roles_for_date(engine, td, params=params)
            console.print(f"  roles:        {len(roles)} sectors")
        except Exception as exc:
            console.print(f"  [yellow]roles WARN:[/yellow] {exc}")
            roles = []

        # 4. Cycle phases → sector_state_daily (cycle_phase column)
        try:
            phases = compute_phases_for_date(engine, td, params=params)
            console.print(f"  phases:       {len(phases)} sectors")
        except Exception as exc:
            console.print(f"  [yellow]phases WARN:[/yellow] {exc}")
            phases = []

        # 5. Write sector_state_daily (roles + phases merged)
        if roles or phases:
            try:
                n = write_sector_states(engine, roles=roles, phases=phases)
                console.print(f"  sector_state: {n} rows upserted")
            except Exception as exc:
                console.print(f"  [red]sector_state FAIL:[/red] {exc}")

        # 6. Leaders → stock_signals_daily
        try:
            signals = compute_leaders_for_date(engine, td, params=params)
            n = write_stock_signals(engine, signals)
            console.print(f"  leaders:      {n} signals")
        except Exception as exc:
            console.print(f"  [yellow]leaders WARN:[/yellow] {exc}")

        # 7. Candidates → stock_signals_daily
        try:
            candidates = compute_candidates_for_date(engine, td, params=params)
            n = write_stock_signals(engine, candidates)
            console.print(f"  candidates:   {n} signals")
        except Exception as exc:
            console.print(f"  [yellow]candidates WARN:[/yellow] {exc}")

    console.print(f"\n[bold green]Compute done.[/bold green] {len(dates)} days processed.")


# ─── ETL ──────────────────────────────────────────────────────────────────────

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


# ─── Evening report ───────────────────────────────────────────────────────────

@app.command("evening")
def evening(
    report_date: str = typer.Option(..., "--report-date", help="YYYY-MM-DD"),
    cutoff_time: str = typer.Option("18:00", "--cutoff-time"),
    triggered_by: str | None = typer.Option(None, "--triggered-by"),
    mode: str | None = typer.Option(None, "--mode"),
    generate_pdf: bool = typer.Option(False, "--generate-pdf", help="Also render a PDF alongside the HTML."),
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
    if generate_pdf:
        from ifa.core.render.pdf import html_to_pdf
        pdf_path = html_to_pdf(path)
        console.print(f"[bold green]PDF saved:[/bold green] {pdf_path}")


# ─── Multi-day ETL backfill ───────────────────────────────────────────────────

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


# ─── ML Training (B8) ─────────────────────────────────────────────────────────

@app.command("train")
def train(
    in_sample_start: str = typer.Option("2021-01-04", "--in-sample-start"),
    in_sample_end: str = typer.Option("2025-10-31", "--in-sample-end"),
    oos_start: str = typer.Option("2025-11-01", "--oos-start"),
    oos_end: str = typer.Option("2026-04-30", "--oos-end"),
    version: str = typer.Option("v2026_05", "--version", help="Model version tag"),
    short_horizon: int = typer.Option(1, "--short-horizon",
                                       help="RF prediction horizon in trading days"),
    long_horizon: int = typer.Option(20, "--long-horizon",
                                      help="XGB prediction horizon in trading days"),
    source: str = typer.Option("sw_l2", "--source"),
    mode: str | None = typer.Option(None, "--mode"),
) -> None:
    """B8: train RF (short) + XGB (long) on SW L2 in-sample, evaluate OOS, persist.

    Outputs models to ~/claude/ifaenv/models/smartmoney/ with the version tag.
    """
    _override_mode(mode)
    from ifa.families.smartmoney.ml.train import train_and_persist
    import datetime as _dt

    iss = _dt.datetime.strptime(in_sample_start, "%Y-%m-%d").date()
    ise = _dt.datetime.strptime(in_sample_end, "%Y-%m-%d").date()
    oos = _dt.datetime.strptime(oos_start, "%Y-%m-%d").date()
    ooe = _dt.datetime.strptime(oos_end, "%Y-%m-%d").date()

    console.print(f"[bold]SmartMoney Train · {version}[/bold]")
    console.print(f"  in-sample: {iss} → {ise}  ({(ise-iss).days} days)")
    console.print(f"  OOS:       {oos} → {ooe}  ({(ooe-oos).days} days)")
    console.print(f"  RF horizon = {short_horizon}d  ·  XGB horizon = {long_horizon}d")
    console.print(f"  source     = {source}")

    engine = _engine()
    rf_res, xgb_res = train_and_persist(
        engine,
        in_sample_start=iss, in_sample_end=ise,
        oos_start=oos, oos_end=ooe,
        version_tag=version,
        source=source,
        short_horizon=short_horizon,
        long_horizon=long_horizon,
        on_log=lambda m: console.print(f"  {m}"),
    )

    # Print summary
    t = Table(title=f"Training Results · {version}")
    t.add_column("model", style="cyan")
    t.add_column("horizon", justify="right")
    t.add_column("train rows", justify="right")
    t.add_column("OOS rows", justify="right")
    t.add_column("in-sample AUC", justify="right")
    t.add_column("OOS AUC", justify="right")
    t.add_column("OOS prec.", justify="right")
    t.add_column("OOS recall", justify="right")
    t.add_column("seconds", justify="right")

    for r in (rf_res, xgb_res):
        t.add_row(
            r.model_name,
            f"{r.horizon_days}d",
            f"{r.in_sample_n:,}",
            f"{r.oos_n:,}",
            f"{r.in_sample_metrics.get('val_auc', 'NaN')}",
            f"{r.oos_metrics.get('val_auc', 'NaN')}",
            f"{r.oos_metrics.get('val_precision', 'NaN')}",
            f"{r.oos_metrics.get('val_recall', 'NaN')}",
            f"{r.train_seconds:.0f}s",
        )
    console.print(t)

    console.print("\n[dim]Top features (RF):[/dim]")
    for n, v in rf_res.top_features[:10]:
        console.print(f"  {n:35s} {v:.4f}")
    console.print("\n[dim]Top features (XGB):[/dim]")
    for n, v in xgb_res.top_features[:10]:
        console.print(f"  {n:35s} {v:.4f}")


# ─── Backtest ─────────────────────────────────────────────────────────────────

@app.command("backtest")
def backtest(
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD start date"),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD end date"),
    param_version: str | None = typer.Option(None, "--param-version",
                                              help="Named param version from DB; default=active"),
    no_ml: bool = typer.Option(False, "--no-ml", help="Skip walk-forward ML AUC evaluation"),
    windows: str = typer.Option("1,5", "--windows",
                                 help="Forward return windows in trading days, comma-separated"),
    topn: int = typer.Option(5, "--topn", help="N for top-N hit rate"),
    notes: str | None = typer.Option(None, "--notes", help="Human-readable notes"),
    mode: str | None = typer.Option(None, "--mode"),
) -> None:
    """Run SmartMoney factor + ML backtest and persist metrics to DB."""
    _override_mode(mode)
    from ifa.families.smartmoney.backtest.runner import run_smartmoney_backtest

    s = dt.datetime.strptime(start, "%Y-%m-%d").date()
    e = dt.datetime.strptime(end, "%Y-%m-%d").date()
    fwd_windows = tuple(int(w.strip()) for w in windows.split(","))

    console.print(f"[bold]SmartMoney Backtest · {s} → {e}[/bold]")
    console.print(f"  forward windows: {fwd_windows}  |  topN: {topn}  |  ML: {not no_ml}")
    if param_version:
        console.print(f"  param version: {param_version}")

    engine = _engine()
    result, run_id = run_smartmoney_backtest(
        engine,
        start=s,
        end=e,
        param_version=param_version,
        run_ml_walkforward=not no_ml,
        forward_windows=fwd_windows,
        topn=topn,
        notes=notes,
        on_log=lambda m: console.print(f"  {m}"),
    )

    # Print factor metrics summary
    console.print(f"\n[bold green]Backtest complete.[/bold green]  run_id=[cyan]{run_id}[/cyan]\n")
    _print_factor_summary(result)

    # Print ML walk-forward summary
    if result.ml_results:
        console.print()
        _print_ml_summary(result)

    console.print(f"\n[dim]To freeze params from this run:[/dim]")
    console.print(f"  ifa smartmoney params freeze --name <version> --from-backtest {run_id}")


def _print_factor_summary(result) -> None:
    """Rich table: factor × window → IC / RankIC / TopN / Q1–Q5 spread."""
    from ifa.families.smartmoney.backtest.engine import FACTOR_COLS

    # One table per forward window
    windows = sorted({r.window_days for r in result.factor_results})
    for w in windows:
        t = Table(title=f"Factor Metrics — {w}d Forward Return", show_lines=False)
        t.add_column("Factor", style="cyan")
        t.add_column("IC mean", justify="right")
        t.add_column("IC IR", justify="right")
        t.add_column("RankIC mean", justify="right")
        t.add_column("RankIC IR", justify="right")
        t.add_column("TopN hit%", justify="right")
        t.add_column("Q5-Q1 spread", justify="right")
        t.add_column("N dates", justify="right")

        for fc in FACTOR_COLS:
            rows = [r for r in result.factor_results if r.factor_name == fc and r.window_days == w]
            if not rows:
                continue
            r = rows[0]
            q5 = r.group_returns.get("Q5", float("nan"))
            q1 = r.group_returns.get("Q1", float("nan"))
            spread = q5 - q1 if (q5 == q5 and q1 == q1) else float("nan")

            def _fmt(v: float, pct: bool = False, decimals: int = 4) -> str:
                if v != v:  # nan
                    return "—"
                if pct:
                    return f"{v * 100:.1f}%"
                return f"{v:.{decimals}f}"

            t.add_row(
                fc,
                _fmt(r.ic_mean),
                _fmt(r.ic_ir, decimals=2),
                _fmt(r.rank_ic_mean),
                _fmt(r.rank_ic_ir, decimals=2),
                _fmt(r.topn_hit_rate, pct=True),
                _fmt(spread),
                str(r.n_dates),
            )
        console.print(t)


def _print_ml_summary(result) -> None:
    t = Table(title="Walk-Forward ML AUC", show_lines=False)
    t.add_column("Model", style="cyan")
    t.add_column("Mean AUC", justify="right")
    t.add_column("Std AUC", justify="right")
    t.add_column("N steps", justify="right")
    t.add_column("N pred rows", justify="right")

    for ml in result.ml_results:
        def _f(v: float) -> str:
            return f"{v:.4f}" if v == v else "—"
        t.add_row(
            ml.model_name,
            _f(ml.mean_auc),
            _f(ml.std_auc),
            str(ml.n_steps),
            f"{ml.n_pred_rows:,}",
        )
    console.print(t)


# ─── Backtest inspection sub-commands ────────────────────────────────────────

@bt_app.command("list")
def bt_list(
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """List recent backtest runs."""
    from ifa.families.smartmoney.backtest.runner import list_backtest_runs
    engine = _engine()
    runs = list_backtest_runs(engine, limit=limit)

    t = Table(title="Recent SmartMoney Backtest Runs", show_lines=False)
    t.add_column("run_id (short)", style="cyan")
    t.add_column("start_date")
    t.add_column("end_date")
    t.add_column("param_version")
    t.add_column("status")
    t.add_column("started_at")
    t.add_column("notes", overflow="fold")

    for r in runs:
        t.add_row(
            r["run_id"][:8] + "...",
            str(r["start_date"]),
            str(r["end_date"]),
            r["param_version"] or "—",
            r["status"],
            str(r["started_at"])[:16] if r["started_at"] else "—",
            r["notes"] or "",
        )
    console.print(t)


@bt_app.command("show")
def bt_show(
    run_id: str = typer.Argument(..., help="Full or partial backtest_run_id UUID"),
) -> None:
    """Show metrics for a backtest run (provide full UUID or first 8 chars)."""
    from ifa.families.smartmoney.backtest.runner import get_backtest_metrics, list_backtest_runs
    engine = _engine()

    # Resolve partial UUID
    if len(run_id) < 36:
        runs = list_backtest_runs(engine, limit=100)
        matches = [r for r in runs if r["run_id"].startswith(run_id)]
        if not matches:
            console.print(f"[red]No backtest run starts with '{run_id}'[/red]")
            raise typer.Exit(1)
        if len(matches) > 1:
            console.print(f"[yellow]Ambiguous: {len(matches)} runs match '{run_id}'[/yellow]")
            raise typer.Exit(1)
        run_id = matches[0]["run_id"]

    metrics = get_backtest_metrics(engine, run_id)
    if not metrics:
        console.print(f"[yellow]No metrics found for run {run_id}[/yellow]")
        return

    t = Table(title=f"Metrics · {run_id[:8]}...", show_lines=False)
    t.add_column("factor", style="cyan")
    t.add_column("metric")
    t.add_column("window_days", justify="right")
    t.add_column("group")
    t.add_column("value", justify="right")
    t.add_column("n_samples", justify="right")

    for m in metrics:
        val = f"{m['metric_value']:.6f}" if m["metric_value"] is not None else "—"
        t.add_row(
            m["factor_name"], m["metric_name"],
            str(m["window_days"]), m["group_label"] or "",
            val, f"{m['n_samples']:,}" if m["n_samples"] else "—",
        )
    console.print(t)


# ─── Params sub-commands ──────────────────────────────────────────────────────

@params_app.command("list")
def params_list() -> None:
    """List all param versions in the DB."""
    from ifa.families.smartmoney.params.store import list_param_versions
    engine = _engine()
    versions = list_param_versions(engine)

    if not versions:
        console.print("[yellow]No param versions in DB.[/yellow]")
        return

    t = Table(title="SmartMoney Param Versions", show_lines=False)
    t.add_column("version_name", style="cyan")
    t.add_column("status")
    t.add_column("frozen_at")
    t.add_column("backtest_run_id (short)")
    t.add_column("notes", overflow="fold")

    for v in versions:
        bt = (v["backtest_run_id"] or "")[:8] + ("..." if v["backtest_run_id"] else "")
        t.add_row(
            v["version_name"],
            v["status"],
            str(v["frozen_at"])[:16] if v["frozen_at"] else "—",
            bt or "—",
            v["notes"] or "",
        )
    console.print(t)


@params_app.command("freeze")
def params_freeze(
    name: str = typer.Option(..., "--name", help="Version name, e.g. v2026_05"),
    from_backtest: str | None = typer.Option(None, "--from-backtest",
                                               help="backtest_run_id to link"),
    notes: str | None = typer.Option(None, "--notes"),
    no_activate: bool = typer.Option(False, "--no-activate",
                                      help="Save as 'draft' instead of making active"),
) -> None:
    """Freeze current default.yaml params as a named version in the DB."""
    from ifa.families.smartmoney.params.store import freeze_params, load_default_params
    engine = _engine()
    params = load_default_params()

    version_id = freeze_params(
        engine,
        version_name=name,
        params=params,
        backtest_run_id=from_backtest,
        notes=notes,
        make_active=not no_activate,
    )
    status = "draft" if no_activate else "active"
    console.print(f"[bold green]Params frozen:[/bold green] {name}  (id={version_id}, status={status})")


@params_app.command("archive")
def params_archive(
    name: str = typer.Argument(..., help="Version name to archive"),
) -> None:
    """Archive a param version (mark as non-active)."""
    from ifa.families.smartmoney.params.store import archive_params
    engine = _engine()
    ok = archive_params(engine, name)
    if ok:
        console.print(f"[green]Archived:[/green] {name}")
    else:
        console.print(f"[yellow]Not found or already archived:[/yellow] {name}")
