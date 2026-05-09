"""`ifa stock ...` CLI — Stock Edge single-stock trade plan."""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.core.report.timezones import BJT, bjt_now
from ifa.families.stock.backtest import (
    fit_global_preset,
    fit_pre_report_overlay,
    plan_global_preset_refresh,
    plan_pre_report_tuning,
    write_tuning_artifact,
)
from ifa.families.stock.backtest.data import (
    load_daily_bars_for_tuning,
    load_top_liquidity_universe,
    load_universe_daily_bars_with_backfill,
)
from ifa.families.stock.data.tushare_backfill import backfill_core_stock_window
from ifa.families.stock import StockEdgeRequest
from ifa.families.stock.context import build_context
from ifa.families.stock.data import build_local_snapshot
from ifa.families.stock.data.intraday_backfill import (
    IntradayBackfillSpec,
    backfill_intraday_sweep,
    default_intraday_sweep,
    estimate_intraday_storage,
)
from ifa.families.stock.report import run_stock_edge_report
from ifa.families.stock.params import load_params
from sqlalchemy import text

console = Console()
app = typer.Typer(no_args_is_help=True, help="Stock Edge — single-stock trade plan.")


@app.command("report")
def report_cmd(
    query: str = typer.Argument(..., help="Stock code/name, e.g. 300042.SZ or 朗科科技"),
    mode: str = typer.Option("quick", "--mode", help="quick | deep | update"),
    run_mode: str | None = typer.Option(None, "--run-mode", help="manual | production | test; defaults to settings.run_mode"),
    requested_at: str | None = typer.Option(
        None,
        "--requested-at",
        help="Beijing time ISO datetime for reproducible as-of routing, e.g. 2026-05-05T15:01:00",
    ),
    fresh: bool = typer.Option(False, "--fresh", help="Bypass reusable stock.analysis_record asset"),
    base_position_shares: int | None = typer.Option(None, "--base-position-shares", help="Shares currently held; enables T+0 section"),
) -> None:
    """Generate a Stock Edge report in manual/production output layout."""
    settings = get_settings()
    engine = get_engine(settings)
    ts_code = _resolve_ts_code(query, engine)
    req_at = _parse_requested_at(requested_at)
    request = StockEdgeRequest(
        ts_code=ts_code,
        requested_at=req_at,
        mode=mode,  # type: ignore[arg-type]
        run_mode=run_mode or settings.run_mode.value,  # type: ignore[arg-type]
        has_base_position=base_position_shares is not None and base_position_shares > 0,
        base_position_shares=base_position_shares,
        fresh=fresh,
    )
    result = run_stock_edge_report(request, engine=engine, settings=settings)
    if result.reused:
        console.print(f"[green]✓ reused[/green] record_id={result.record_id}")
    else:
        console.print(f"[green]✓ generated[/green] record_id={result.record_id}")
    if result.html_path:
        console.print(f"[green]HTML[/green] → {result.html_path}")
    if result.rendered:
        console.print(f"[green]MD[/green]   → {result.rendered.md_path}")


def _parse_requested_at(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    parsed = dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BJT)
    return parsed


def _resolve_ts_code(query: str, engine) -> str:
    q = query.strip()
    if re.match(r"^\d{6}\.(SZ|SH|BJ)$", q, re.IGNORECASE):
        return q.upper()
    if re.match(r"^\d{6}$", q):
        first = q[0]
        suffix = {"0": "SZ", "3": "SZ", "6": "SH", "8": "BJ", "4": "BJ", "9": "BJ"}.get(first, "SZ")
        return f"{q}.{suffix}"
    try:
        from ifa.families.research.resolver import resolve

        return resolve(q, engine).ts_code
    except Exception as exc:
        raise typer.BadParameter(f"Could not resolve stock {query!r}: {exc}") from exc


@app.command("quick")
def quick_alias(
    query: str = typer.Argument(...),
    fresh: bool = typer.Option(False, "--fresh"),
    base_position_shares: int | None = typer.Option(None, "--base-position-shares"),
) -> None:
    report_cmd(query=query, mode="quick", run_mode=None, requested_at=None, fresh=fresh, base_position_shares=base_position_shares)


@app.command("today")
def today_alias(
    query: str = typer.Argument(...),
    fresh: bool = typer.Option(False, "--fresh"),
) -> None:
    report_cmd(
        query=query,
        mode="quick",
        run_mode=None,
        requested_at=bjt_now().isoformat(),
        fresh=fresh,
        base_position_shares=None,
    )


@app.command("diagnose")
def diagnose_cmd(
    queries: list[str] = typer.Argument(..., help="One or more stock codes/names, e.g. 300042.SZ 朗科科技"),
    requested_at: str | None = typer.Option(
        None,
        "--requested-at",
        help="Beijing time ISO datetime for reproducible as-of routing, e.g. 2026-05-08T15:01:00",
    ),
    run_mode: str | None = typer.Option(None, "--run-mode", help="manual | production | test; defaults to settings.run_mode"),
    output_format: str = typer.Option("markdown", "--format", help="markdown | json | html"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write artifact to a file or directory. Multiple stocks require a directory."),
    full_stock_edge: bool = typer.Option(False, "--full-stock-edge", help="Also run the expensive full Stock Edge strategy matrix and decision layer."),
) -> None:
    """Build a read-only multi-perspective single-stock diagnostic report."""
    settings = get_settings()
    engine = get_engine(settings)
    from ifa.families.stock.diagnostic import DiagnosticRequest, build_diagnostic_report
    from ifa.families.stock.diagnostic.service import render_html, render_markdown

    if output_format not in {"markdown", "json", "html"}:
        raise typer.BadParameter("--format must be markdown, json, or html")
    if len(queries) > 1 and output is not None and output.suffix:
        raise typer.BadParameter("--output must be a directory when diagnosing multiple stocks")

    written: list[Path] = []
    rendered_payloads: list[str] = []
    for query in queries:
        ts_code = _resolve_ts_code(query, engine)
        report = build_diagnostic_report(
            DiagnosticRequest(
                ts_code=ts_code,
                requested_at=_parse_requested_at(requested_at),
                run_mode=run_mode or settings.run_mode.value,
                include_full_stock_edge=full_stock_edge,
            ),
            engine=engine,
        )
        payload = _render_diagnostic_payload(report, output_format, render_markdown=render_markdown, render_html=render_html)
        if output:
            path = _diagnostic_output_path(output, report, output_format, default_dir=_default_diagnostic_output_dir(settings, report, run_mode))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
            written.append(path)
        else:
            rendered_payloads.append(payload)

    if written:
        for path in written:
            console.print(f"[green]{output_format.upper()}[/green] → {path}")
    else:
        console.print("\n\n".join(rendered_payloads))


def _render_diagnostic_payload(report, output_format: str, *, render_markdown, render_html) -> str:
    if output_format == "json":
        return json.dumps(report.to_dict(), ensure_ascii=False, default=str, indent=2) + "\n"
    if output_format == "html":
        return render_html(report)
    return render_markdown(report)


def _default_diagnostic_output_dir(settings, report, run_mode: str | None) -> Path:
    from ifa.families.stock.output import output_dir_for_stock_edge

    return output_dir_for_stock_edge(settings, report.as_of_trade_date, run_mode=run_mode or settings.run_mode.value) / "diagnostic"


def _diagnostic_output_path(output: Path, report, output_format: str, *, default_dir: Path) -> Path:
    suffix = {"markdown": ".md", "json": ".json", "html": ".html"}[output_format]
    if output.suffix:
        return output
    stem = (
        f"CN_stock_edge_diagnostic_{report.ts_code.replace('.', '_')}_"
        f"{report.as_of_trade_date.strftime('%Y%m%d')}_"
        f"{bjt_now().strftime('%H%M%S')}"
    )
    directory = output if str(output) not in {"", "."} else default_dir
    return directory / f"{stem}{suffix}"


@app.command("data-check")
def data_check_cmd(
    query: str = typer.Argument(..., help="Stock code/name"),
    requested_at: str | None = typer.Option(None, "--requested-at", help="Beijing ISO datetime for reproducible cutoff"),
    allow_backfill: bool = typer.Option(False, "--backfill/--no-backfill", help="Allow minimal Tushare backfill if core data is missing"),
) -> None:
    """Show local data freshness without generating a report."""
    settings = get_settings()
    engine = get_engine(settings)
    ts_code = _resolve_ts_code(query, engine)
    ctx = build_context(
        StockEdgeRequest(ts_code=ts_code, requested_at=_parse_requested_at(requested_at), run_mode=settings.run_mode.value),  # type: ignore[arg-type]
        engine=engine,
    )
    snapshot = build_local_snapshot(ctx, engine=engine, allow_backfill=allow_backfill)
    console.print(f"[bold]{ts_code}[/] as_of={ctx.as_of.as_of_trade_date} cutoff_rule={ctx.as_of.rule}")
    for item in snapshot.freshness:
        color = "green" if item["status"] == "ok" else ("yellow" if item["status"] in ("partial", "stale") else "red")
        console.print(
            f"  [{color}]{item['status']:<8}[/] {item['name']:<18} "
            f"source={item['source']:<8} rows={item['rows']:<4} as_of={item['as_of'] or '-'}"
        )
        if item.get("message"):
            console.print(f"           [dim]{item['message']}[/dim]")


@app.command("intraday-sweep")
def intraday_sweep_cmd(
    query: str = typer.Argument(..., help="Stock code/name"),
    end: str | None = typer.Option(None, "--end", help="End date YYYY-MM-DD; defaults to today"),
    five_days: int = typer.Option(30, "--five-days", help="5min lookback calendar/trading proxy days"),
    thirty_days: int = typer.Option(60, "--thirty-days", help="30min lookback days"),
    sixty_days: int = typer.Option(90, "--sixty-days", help="60min lookback days"),
    estimate_only: bool = typer.Option(False, "--estimate-only", help="Only print storage estimate; do not call TuShare"),
) -> None:
    """Backfill target-stock intraday bars into ifaenv DuckDB/Parquet."""
    settings = get_settings()
    engine = get_engine(settings)
    ts_code = _resolve_ts_code(query, engine)
    specs = [
        IntradayBackfillSpec(ts_code, "5min", five_days),
        IntradayBackfillSpec(ts_code, "30min", thirty_days),
        IntradayBackfillSpec(ts_code, "60min", sixty_days),
    ]
    if five_days == 30 and thirty_days == 60 and sixty_days == 90:
        specs = default_intraday_sweep(ts_code)
    estimate = estimate_intraday_storage(specs)
    console.print(
        f"[bold]{ts_code}[/] intraday sweep estimate: rows≈{estimate['rows']:.0f}, "
        f"uncompressed≈{estimate['uncompressed_mb']:.3f} MB, parquet≈{estimate['parquet_mb_estimate']:.3f} MB"
    )
    if estimate_only:
        return
    result = backfill_intraday_sweep(
        specs,
        end_date=dt.date.fromisoformat(end) if end else None,
        settings=settings,
        on_log=lambda m: console.print(m),
    )
    console.print(f"[green]✓ intraday rows fetched[/green] {result.rows_written}")
    for path in result.files_written:
        console.print(f"[green]parquet[/green] → {path}")


@app.command("tune-overlay")
def tune_overlay_cmd(
    query: str = typer.Argument(..., help="Stock code/name"),
    as_of: str | None = typer.Option(None, "--as-of", help="As-of trade date YYYY-MM-DD; defaults to latest local raw_daily date"),
    max_candidates: int = typer.Option(64, "--max-candidates", min=1, help="Continuous candidates to evaluate"),
    write: bool = typer.Option(True, "--write/--dry-run", help="Write artifact under ifaenv"),
) -> None:
    """Run standalone pre-report single-stock overlay tuning."""
    settings = get_settings()
    engine = get_engine(settings)
    ts_code = _resolve_ts_code(query, engine)
    as_of_date = _parse_as_of_date(as_of, engine)
    params = load_params()
    bars = load_daily_bars_for_tuning(
        engine,
        ts_code=ts_code,
        as_of_date=as_of_date,
        lookback_rows=int(params.get("tuning", {}).get("pre_report_overlay", {}).get("max_history_rows", 900)),
    )
    overlay_cfg = params.get("tuning", {}).get("pre_report_overlay", {})
    min_history_rows = int(overlay_cfg.get("min_history_rows", params.get("tuning", {}).get("min_history_rows", 360)))
    max_history_rows = int(overlay_cfg.get("max_history_rows", params.get("tuning", {}).get("max_history_rows", 900)))
    plan = plan_pre_report_tuning(
        bars,
        ts_code=ts_code,
        as_of_trade_date=as_of_date,
        stale_after_days=int(overlay_cfg.get("ttl_days", 10)),
        min_history_rows=min_history_rows,
        max_history_rows=max_history_rows,
    )
    if (
        not plan.should_tune
        and plan.history_rows < min_history_rows
        and bool(overlay_cfg.get("backfill_on_short_history", params.get("data", {}).get("tushare_backfill_on_missing", True)))
    ):
        console.print(f"[yellow]history short[/yellow] {plan.history_rows}/{min_history_rows}; trying TuShare backfill")
        backfill = backfill_core_stock_window(
            engine,
            ts_code,
            as_of_date,
            daily_rows=max_history_rows,
            basic_rows=max(20, int(params.get("runtime", {}).get("default_lookback_days", 7))),
            moneyflow_rows=max(20, int(params.get("runtime", {}).get("default_lookback_days", 7))),
        )
        console.print(
            f"[yellow]backfill[/yellow] dates={len(backfill.requested_dates)} "
            f"errors={len(backfill.errors)} counts={backfill.fetched_counts}"
        )
        bars = load_daily_bars_for_tuning(
            engine,
            ts_code=ts_code,
            as_of_date=as_of_date,
            lookback_rows=max_history_rows,
        )
        plan = plan_pre_report_tuning(
            bars,
            ts_code=ts_code,
            as_of_trade_date=as_of_date,
            stale_after_days=int(overlay_cfg.get("ttl_days", 10)),
            min_history_rows=min_history_rows,
            max_history_rows=max_history_rows,
        )
    console.print(f"[bold]{ts_code}[/] overlay plan: {plan.reason}")
    if not plan.should_tune:
        raise typer.Exit(code=0)
    artifact = fit_pre_report_overlay(
        bars,
        ts_code=ts_code,
        as_of_trade_date=as_of_date,
        base_params=params,
        max_candidates=max_candidates,
    )
    console.print(
        f"[green]✓ overlay tuned[/green] score={artifact.objective_score:.4f} "
        f"samples={artifact.metrics.get('sample_count', 0)} candidates={artifact.candidate_count}"
    )
    if write:
        path = write_tuning_artifact(artifact)
        console.print(f"[green]artifact[/green] → {path}")


@app.command("tune-global-preset")
def tune_global_preset_cmd(
    as_of: str | None = typer.Option(None, "--as-of", help="As-of trade date YYYY-MM-DD; defaults to latest local raw_daily date"),
    universe: str = typer.Option("top_liquidity_500", "--universe", help="Universe label for artifact namespace"),
    limit: int = typer.Option(500, "--limit", min=1, help="Top-liquidity stock count"),
    max_candidates: int = typer.Option(96, "--max-candidates", min=1, help="Continuous candidates to evaluate"),
    write: bool = typer.Option(True, "--write/--dry-run", help="Write artifact under ifaenv"),
) -> None:
    """Run standalone weekend/overnight global preset tuning."""
    settings = get_settings()
    engine = get_engine(settings)
    as_of_date = _parse_as_of_date(as_of, engine)
    params = load_params()
    preset_cfg = params.get("tuning", {}).get("global_preset", {})
    plan = plan_global_preset_refresh(
        as_of_date=as_of_date,
        universe=universe,
        min_stocks=int(preset_cfg.get("min_stocks", 300)),
        max_stocks=int(preset_cfg.get("max_stocks", 800)),
        refresh_after_days=int(preset_cfg.get("artifact_ttl_days", 10)),
    )
    console.print(f"[bold]{universe}[/] global preset plan: {plan.reason}")
    ts_codes = load_top_liquidity_universe(engine, as_of_date=as_of_date, limit=limit)
    if not ts_codes:
        raise typer.BadParameter("No local raw_daily rows found for global preset universe.")
    console.print(f"loading daily bars for {len(ts_codes)} stocks...")
    bars_by_stock, backfill_meta = load_universe_daily_bars_with_backfill(
        engine,
        ts_codes=ts_codes,
        as_of_date=as_of_date,
        lookback_rows=int(params.get("tuning", {}).get("pre_report_overlay", {}).get("max_history_rows", 900)),
        min_history_rows=int(params.get("tuning", {}).get("min_history_rows", 360)),
        backfill_short_history=bool(preset_cfg.get("backfill_short_history", params.get("data", {}).get("tushare_backfill_on_missing", True))),
        max_backfill_stocks=int(preset_cfg.get("max_backfill_stocks", 50)),
        on_log=console.print,
    )
    if backfill_meta.get("backfill_attempted"):
        console.print(
            f"[yellow]backfill[/yellow] attempted={backfill_meta['backfill_attempted']} "
            f"errors={backfill_meta['backfill_errors']} short_after={backfill_meta.get('short_history_after_backfill', 0)}"
        )
    artifact = fit_global_preset(
        bars_by_stock,
        as_of_date=as_of_date,
        base_params=params,
        universe=universe,
        max_candidates=max_candidates,
    )
    console.print(
        f"[green]✓ global preset tuned[/green] score={artifact.objective_score:.4f} "
        f"stocks={artifact.metrics.get('stock_count', 0)} samples={artifact.metrics.get('sample_count', 0)} "
        f"candidates={artifact.candidate_count}"
    )
    if write:
        path = write_tuning_artifact(artifact)
        console.print(f"[green]artifact[/green] → {path}")


@app.command("tune")
def tune_cmd(
    as_of: str | None = typer.Option(None, "--as-of", help="Latest as_of trade date YYYY-MM-DD; defaults to latest raw_daily date"),
    top: int = typer.Option(100, "--top", min=1, help="Top N by liquidity"),
    liquidity_offset: int = typer.Option(0, "--liquidity-offset", min=0, help="Skip the top K liquidity names for OOC cohorts"),
    pit_samples: int = typer.Option(24, "--pit-samples", min=2, help="PIT trading days to sample"),
    max_candidates: int = typer.Option(768, "--max-candidates", min=1, help="Search candidate budget"),
    workers: int = typer.Option(-1, "--workers", help="Parallel workers (-1 = auto)"),
    k_fold: int = typer.Option(4, "--k-fold", min=0, help="Rolling walk-forward folds"),
    val_dates_per_fold: int = typer.Option(2, "--val-dates-per-fold", min=1, help="Validation dates per fold"),
    min_train_dates: int = typer.Option(4, "--min-train-dates", min=1, help="Minimum train dates for first fold"),
    bootstrap_iterations: int = typer.Option(1000, "--bootstrap-iterations", min=0, help="Bootstrap iterations for G5"),
    search_algo: str = typer.Option("tpe", "--search-algo", help="random | tpe"),
    successive_halving: bool = typer.Option(True, "--successive-halving/--no-successive-halving", help="Use staged coarse-to-fine search"),
    auto_promote: bool = typer.Option(True, "--auto-promote/--no-auto-promote", help="Run promotion gates after tuning"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not write artifacts/YAML"),
    apply_to_baseline: bool = typer.Option(False, "--apply-to-baseline", help="If gates pass, overwrite baseline YAML with backup"),
    variant_output: str | None = typer.Option(None, "--variant-output", help="Variant YAML path when not applying to baseline"),
    universe_id: str = typer.Option("top_liquidity", "--universe-id", help="Replay panel cache namespace prefix"),
    universe_mode: str = typer.Option("latest", "--universe-mode", help="latest | pit-local | stratified-pit"),
    stratified_pool_multiple: int = typer.Option(8, "--stratified-pool-multiple", min=2, help="Candidate pool multiple for stratified-pit"),
    two_stage: bool = typer.Option(False, "--two-stage", help="Cheap proxy prefilter before expensive replay search"),
    proxy_candidates: int = typer.Option(128, "--proxy-candidates", min=1, help="Cheap proxy candidate budget"),
    proxy_max_rows: int = typer.Option(600, "--proxy-max-rows", min=1, help="Max rows for cheap proxy subset"),
    include_llm: bool = typer.Option(False, "--include-llm", help="Include LLM signals in panel build"),
) -> None:
    """Run production-aligned Stock Edge panel tuning.

    This wraps `scripts/stock_edge_panel_tune.py`, not the legacy surrogate
    global-preset tuner. Use `--liquidity-offset 100` for OOC holdout cohorts.
    """
    if universe_mode not in {"latest", "pit-local", "stratified-pit"}:
        raise typer.BadParameter("--universe-mode must be one of: latest, pit-local, stratified-pit")
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "stock_edge_panel_tune.py"
    cmd = [
        sys.executable,
        str(script),
        "--top", str(top),
        "--liquidity-offset", str(liquidity_offset),
        "--pit-samples", str(pit_samples),
        "--max-candidates", str(max_candidates),
        "--workers", str(workers),
        "--k-fold", str(k_fold),
        "--val-dates-per-fold", str(val_dates_per_fold),
        "--min-train-dates", str(min_train_dates),
        "--bootstrap-iterations", str(bootstrap_iterations),
        "--search-algo", search_algo,
        "--universe-id", universe_id,
        "--universe-mode", universe_mode,
        "--stratified-pool-multiple", str(stratified_pool_multiple),
    ]
    if as_of:
        cmd.extend(["--as-of", as_of])
    if successive_halving:
        cmd.append("--successive-halving")
    if auto_promote:
        cmd.append("--auto-promote")
    if dry_run:
        cmd.append("--dry-run")
    if apply_to_baseline:
        cmd.append("--apply-to-baseline")
    if variant_output:
        cmd.extend(["--variant-output", variant_output])
    if include_llm:
        cmd.append("--include-llm")
    if two_stage:
        cmd.extend([
            "--two-stage",
            "--proxy-candidates", str(proxy_candidates),
            "--proxy-max-rows", str(proxy_max_rows),
        ])
    console.print("[bold]running[/] " + " ".join(cmd))
    raise typer.Exit(code=subprocess.call(cmd, cwd=str(root)))


def _parse_as_of_date(raw: str | None, engine) -> dt.date:
    if raw:
        return dt.date.fromisoformat(raw)
    today = bjt_now().date()
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT MAX(trade_date) FROM smartmoney.raw_daily WHERE trade_date <= :today"),
            {"today": today},
        ).scalar_one_or_none()
    if value is None:
        raise typer.BadParameter("No local smartmoney.raw_daily trade_date is available.")
    return value
