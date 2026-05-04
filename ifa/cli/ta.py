"""`ifa ta ...` CLI — TA family commands."""
from __future__ import annotations

import logging
from datetime import date, timedelta

import typer
from rich.console import Console

from ifa.core.db import get_engine
from ifa.core.report.timezones import bjt_now
from ifa.families.ta.regime.classifier import classify_regime
from ifa.families.ta.regime.loader import load_regime_context
from ifa.families.ta.regime.repo import upsert_regime_daily
from ifa.families.ta.regime.transitions import build_transition_matrix
from ifa.families.ta.setups.context_loader import build_contexts
from ifa.families.ta.setups.ranker import rank as rank_candidates
from ifa.families.ta.setups.repo import upsert_candidates, upsert_warnings
from ifa.families.ta.setups.scanner import scan as scan_setups
from ifa.families.ta.setups.tracking import evaluate_for_date
from ifa.families.ta.setups.judgments import evaluate_judgments
from ifa.families.ta.metrics import compute_setup_metrics
from ifa.families.ta.report import build_evening_report, render_html, render_markdown

log = logging.getLogger(__name__)
console = Console()
app = typer.Typer(no_args_is_help=True, help="TA family — technical analysis & regime")


@app.command("classify-regime")
def classify_regime_cmd(
    on_date: str = typer.Option(None, "--date", help="Trade date YYYY-MM-DD (default: today BJT)"),
    persist: bool = typer.Option(True, "--persist/--no-persist", help="Write to ta.regime_daily"),
    show_transitions: bool = typer.Option(False, "--transitions", help="Print transition predictions"),
) -> None:
    """Classify market regime for a date and (optionally) persist to ta.regime_daily."""
    target = date.fromisoformat(on_date) if on_date else bjt_now().date()
    engine = get_engine()

    ctx = load_regime_context(engine, target)
    result = classify_regime(ctx)

    console.print(f"[bold]{target}[/] → [cyan]{result.regime}[/] (confidence {result.confidence:.2f})")
    scores = result.evidence.get("scores", {})
    for regime, score in sorted(scores.items(), key=lambda kv: -kv[1])[:5]:
        console.print(f"  {regime:25} {score:.3f}")

    transitions_json = None
    if show_transitions or persist:
        try:
            tm = build_transition_matrix(engine, lookback_days=120, on_date=target)
            if tm.samples > 0:
                probs = tm.predict(result.regime)
                transitions_json = {
                    "samples": tm.samples,
                    "lookback_days": tm.lookback_days,
                    "next_probs": probs,
                }
                if show_transitions:
                    console.print(f"\n[bold]P(next | {result.regime})[/] — based on {tm.samples} samples")
                    for r, p in sorted(probs.items(), key=lambda kv: -kv[1])[:5]:
                        console.print(f"  {r:25} {p:.3f}")
        except Exception as e:
            log.warning("transition matrix unavailable: %s", e)

    if persist:
        upsert_regime_daily(engine, target, result, transitions_json)
        console.print(f"[green]✓ persisted to ta.regime_daily[/]")


@app.command("scan-candidates")
def scan_candidates(
    on_date: str = typer.Option(None, "--date", help="Trade date YYYY-MM-DD (default: today BJT)"),
    top_n: int = typer.Option(20, "--top-n", help="Top-N marked in_top_watchlist"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
) -> None:
    """Scan 28 setups (long + warning) across the full market; rank long pool + persist."""
    target = date.fromisoformat(on_date) if on_date else bjt_now().date()
    engine = get_engine()

    # Pull today's regime so setups can use it as a tailwind/headwind
    from sqlalchemy import text
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT regime FROM ta.regime_daily WHERE trade_date = :d"),
            {"d": target},
        ).fetchone()
    regime = row[0] if row else None
    if regime is None:
        console.print(f"[yellow]⚠[/yellow]  no regime in ta.regime_daily for {target}; "
                      f"setups using regime_tailwind will not score that bonus.")

    contexts = build_contexts(engine, target, regime=regime)
    console.print(f"[bold]{target}[/]  contexts: {len(contexts):>5}  regime: {regime or '(none)'}")

    candidates, warnings = scan_setups(contexts.values())
    n_long_only = sum(1 for c in contexts.values() if c.in_long_universe)
    console.print(f"  long-pool stocks: {n_long_only} / liquid {len(contexts)}; "
                  f"long candidates: {len(candidates)}, warnings: {len(warnings)}")

    # Load setup_metrics for the most recent date <= target — governs M5.3 gating
    from sqlalchemy import text as _text
    with engine.connect() as _conn:
        latest = _conn.execute(
            _text("SELECT MAX(trade_date) FROM ta.setup_metrics_daily WHERE trade_date <= :d"),
            {"d": target},
        ).scalar()
        setup_metrics: dict = {}
        if latest:
            for row in _conn.execute(
                _text("""SELECT setup_name, decay_score, suitable_regimes,
                                winrate_60d, regime_winrates, combined_score_60d
                         FROM ta.setup_metrics_daily WHERE trade_date = :d"""),
                {"d": latest},
            ):
                setup_metrics[row[0]] = {
                    "decay_score": float(row[1]) if row[1] is not None else None,
                    "suitable_regimes": list(row[2]) if row[2] else [],
                    "winrate_60d": float(row[3]) if row[3] is not None else None,
                    "regime_winrates": (row[4] if isinstance(row[4], dict) else {}),
                    "combined_score_60d": float(row[5]) if row[5] is not None else None,
                }
    if setup_metrics:
        console.print(f"  metrics from {latest}: {len(setup_metrics)} setups")

    ranked = rank_candidates(candidates, top_n=top_n,
                             current_regime=regime, setup_metrics=setup_metrics)
    if not ranked:
        console.print("[yellow]no candidates triggered today[/]")
        return

    # Quick by-setup summary
    from collections import Counter
    setup_counts = Counter(rc.candidate.setup_name for rc in ranked)
    console.print("\n[bold]hits by setup:[/]")
    for name, n in sorted(setup_counts.items(), key=lambda kv: -kv[1]):
        console.print(f"  {name:25} {n:>5}")

    # Show top-N watchlist
    console.print(f"\n[bold]top {top_n}:[/]")
    for rc in ranked[:top_n]:
        c = rc.candidate
        stars = "★" * rc.star_rating + "☆" * (5 - rc.star_rating)
        console.print(f"  #{rc.rank:>3} {c.ts_code:12} {c.setup_name:25} "
                      f"{c.score:.2f} {stars}")

    if persist:
        n = upsert_candidates(engine, target, ranked, regime_at_gen=regime)
        console.print(f"\n[green]✓ persisted {n} rows to ta.candidates_daily[/]")
        if warnings:
            nw = upsert_warnings(engine, target, warnings, regime_at_gen=regime)
            console.print(f"[green]✓ persisted {nw} rows to ta.warnings_daily[/]")


@app.command("track-candidates")
def track_candidates(
    start: str = typer.Option(..., "--start", help="Candidate generation date YYYY-MM-DD"),
    horizons: list[int] = typer.Option([1, 3, 5, 10, 30], "--horizon",
                                       help="Horizons in trade days (repeatable)"),
) -> None:
    """For candidates generated on `start`, evaluate T+h outcomes and write ta.candidate_tracking."""
    engine = get_engine()
    start_d = date.fromisoformat(start)
    total = 0
    for h in horizons:
        n = evaluate_for_date(engine, start_d, horizon_days=h)
        console.print(f"  h={h:>2}  tracked {n:>5} candidates")
        total += n
    console.print(f"\n[bold green]done.[/] {total} tracking rows written.")


@app.command("backtest")
def backtest_cmd(
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD inclusive"),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD inclusive"),
    horizon: int = typer.Option(10, "--horizon", help="Outcome horizon (trade days)"),
    top_only: bool = typer.Option(False, "--top-only",
                                  help="Restrict to in_top_watchlist candidates"),
) -> None:
    """Aggregate setup performance over a date range. Read-only — no writes."""
    from sqlalchemy import text
    engine = get_engine()
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)

    where_top = "AND c.in_top_watchlist" if top_only else ""
    sql = text(f"""
        SELECT c.setup_name,
               COUNT(*) AS n,
               100.0 * COUNT(*) FILTER (WHERE t.return_pct >= 5.0) / NULLIF(COUNT(*), 0) AS win_rate,
               AVG(t.return_pct) AS avg_ret,
               AVG(t.max_return_pct) AS avg_max_ret,
               AVG(t.max_drawdown_pct) AS avg_max_dd,
               AVG(t.return_pct) FILTER (WHERE t.return_pct >= 5.0) AS avg_gain,
               AVG(t.return_pct) FILTER (WHERE t.return_pct <= -3.0) AS avg_loss
        FROM ta.candidates_daily c
        JOIN ta.candidate_tracking t
          ON t.candidate_id = c.candidate_id AND t.horizon_days = :h
        WHERE c.trade_date >= :s AND c.trade_date <= :e
        {where_top}
        GROUP BY c.setup_name
        HAVING COUNT(*) >= 5
        ORDER BY win_rate DESC NULLS LAST
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"s": start_d, "e": end_d, "h": horizon}).fetchall()

    if not rows:
        console.print("[yellow]no data in range[/]")
        return

    console.print(f"\n[bold]Backtest {start_d} → {end_d} · h={horizon}[/]"
                  f"{'  (top_watchlist only)' if top_only else ''}")
    console.print(f"{'Setup':<24} {'n':>6} {'win%':>6} {'avg':>7} "
                  f"{'maxRet':>7} {'maxDD':>7} {'PL':>5}")
    for r in rows:
        setup, n, wr, avg, mxr, mxd, ag, al = r
        wr_s = f"{wr:.1f}" if wr is not None else "-"
        ag_s = f"{avg:+.2f}" if avg is not None else "-"
        mxr_s = f"{mxr:+.2f}" if mxr is not None else "-"
        mxd_s = f"{mxd:+.2f}" if mxd is not None else "-"
        plr = (float(ag) / abs(float(al))) if (ag is not None and al not in (None, 0)) else None
        plr_s = f"{plr:.2f}" if plr is not None else "-"
        console.print(f"{setup:<24} {n:>6} {wr_s:>6} {ag_s:>7} {mxr_s:>7} {mxd_s:>7} {plr_s:>5}")


@app.command("daily-etl", help="M10 P1.7 — run all TA ETLs for a trade_date.")
def daily_etl_cmd(
    trade_date: str = typer.Option(..., "--date", help="YYYY-MM-DD"),
    skip_factor_pro: bool = typer.Option(False, "--skip-factor-pro"),
    skip_cyq: bool = typer.Option(False, "--skip-cyq"),
    skip_suspend: bool = typer.Option(False, "--skip-suspend"),
    skip_events: bool = typer.Option(False, "--skip-events"),
    skip_blacklist: bool = typer.Option(False, "--skip-blacklist"),
) -> None:
    """Pull TA family inputs from Tushare for one trade_date."""
    from ifa.core.tushare.client import TuShareClient
    from ifa.families.ta.etl.runner import run_ta_daily_etls
    engine = get_engine()
    client = TuShareClient()
    target = date.fromisoformat(trade_date)
    out = run_ta_daily_etls(
        client, engine, trade_date=target,
        skip_factor_pro=skip_factor_pro, skip_cyq=skip_cyq,
        skip_suspend=skip_suspend, skip_events=skip_events,
        skip_blacklist=skip_blacklist,
    )
    console.print(f"[bold]TA daily ETL {target}[/]")
    for source, n in out.items():
        marker = "[red]error[/]" if n < 0 else f"{n} rows"
        console.print(f"  {source:<18} {marker}")


@app.command("coverage", help="M10 P1.7 — per-setup coverage in last N days.")
def coverage_cmd(
    on_date: str = typer.Option(..., "--date", help="YYYY-MM-DD"),
    lookback: int = typer.Option(30, "--lookback", help="Trade days lookback"),
    threshold: int = typer.Option(30, "--threshold",
                                   help="Min monthly coverage; below = flag"),
) -> None:
    """Show per-setup candidate counts in last N days; flag tight setups."""
    from ifa.families.ta.etl.runner import coverage_check
    engine = get_engine()
    target = date.fromisoformat(on_date)
    cov = coverage_check(
        engine, on_date=target,
        lookback_days=lookback, min_monthly_coverage=threshold,
    )
    console.print(f"\n[bold]Setup coverage in last {lookback} days ending {target}[/]")
    console.print(f"{'Setup':<26} {'dates':>6} {'total':>7} {'status':>14}")
    for setup, m in sorted(cov.items(), key=lambda kv: kv[1]['total_candidates']):
        color = "red" if m["status"] != "ok" else "green"
        console.print(
            f"{setup:<26} {m['trade_dates_with_hits']:>6} "
            f"{m['total_candidates']:>7}   [{color}]{m['status']:<12}[/]"
        )


@app.command("walk-forward", help="M10 P1.4 — walk-forward backtest using position_events.")
def walk_forward_cmd(
    start: str = typer.Option(..., "--start", help="Window start YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="Window end YYYY-MM-DD"),
    skip_scan: bool = typer.Option(False, "--skip-scan",
                                    help="Don't re-scan; just aggregate existing data"),
    horizon: int = typer.Option(15, "--horizon", help="Position-tracking horizon (trade days)"),
) -> None:
    """Run scan + position-tracking + aggregation for a date range, using
    the T+15-weighted combined objective (per ta_v2.3.yaml.backtest_objective)."""
    from ifa.families.ta.backtest import backtest_window
    engine = get_engine()
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    result = backtest_window(engine, start_d, end_d,
                              skip_scan=skip_scan, horizon=horizon)
    console.print(f"\n[bold]Walk-forward backtest[/] {start_d} → {end_d}")
    console.print(f"  days={result.n_days_processed}  candidates={result.n_candidates_total}  "
                  f"positions={result.n_positions_total}  setups={len(result.metrics)}")
    if not result.metrics:
        console.print("[yellow]no positions in window — try a date range with full T+15 forward data[/]")
        return
    console.print("\n[bold]Setups by combined T+15 objective:[/]")
    console.print(f"{'Setup':<26} {'n':>5} {'wr_t15':>7} {'avg_t15':>8} "
                  f"{'wr_t5':>7} {'wr_t10':>7} {'combined':>9}")
    for setup, m in sorted(result.metrics.items(), key=lambda kv: -kv[1]['combined']):
        console.print(
            f"{setup:<26} {m['n']:>5} "
            f"{m['wr_t15']:>6.1f}% {m['avg_ret_t15']:>+7.2f}% "
            f"{m['wr_t5']:>6.1f}% {m['wr_t10']:>6.1f}% "
            f"{m['combined']:>9.4f}"
        )


@app.command("tier-perf", help="M10 P2 — Tier A/B portfolio performance over a window.")
def tier_perf_cmd(
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD"),
) -> None:
    """Aggregate Tier A and Tier B equal-weight portfolio T+5/T+10/T+15
    returns from position_events_daily; print combined-objective summary."""
    from ifa.families.ta.backtest import analyze_tier_perf
    engine = get_engine()
    s = date.fromisoformat(start); e = date.fromisoformat(end)
    console.print(f"\n[bold]Tier portfolio performance {s} → {e}[/]")
    console.print(f"\n[bold cyan]State-machine outcomes (real fill→exit, T+15 horizon):[/]")
    console.print(f"{'Tier':<5} {'picks':>6} {'fill%':>6} "
                  f"{'target_hit':>11} {'stop_hit':>9} {'time_exit':>10} "
                  f"{'success%':>9} {'realized':>9}")
    for tier in ("A", "B"):
        perf = analyze_tier_perf(engine, start=s, end=e, tier=tier)
        console.print(
            f"{tier:<5} {perf.n_positions:>6} {perf.fill_rate*100:>5.1f}% "
            f"{perf.n_target_hit:>11} {perf.n_stop_hit:>9} {perf.n_time_exit:>10} "
            f"{perf.success_rate*100:>8.1f}% "
            f"{perf.avg_realized_return:>+8.2f}%"
        )
    console.print(f"\n[bold cyan]Close-based forward returns (no exit, all filled positions):[/]")
    console.print(f"{'Tier':<5} {'avg_t5':>7} {'avg_t10':>8} {'avg_t15':>8} "
                  f"{'wr_t5':>6} {'wr_t10':>7} {'wr_t15':>7} "
                  f"{'avgDD':>6} {'combined':>9}")
    for tier in ("A", "B"):
        perf = analyze_tier_perf(engine, start=s, end=e, tier=tier)
        console.print(
            f"{tier:<5} "
            f"{perf.avg_t5:>+6.2f}% {perf.avg_t10:>+7.2f}% {perf.avg_t15:>+7.2f}% "
            f"{perf.wr_t5:>5.1f}% {perf.wr_t10:>6.1f}% {perf.wr_t15:>6.1f}% "
            f"{perf.avg_max_dd:>+5.1f}% {perf.combined:>9.4f}"
        )


@app.command("evening", help="Alias for evening-report")
def evening_alias(
    on_date: str = typer.Option(None, "--date"),
    output: str = typer.Option(None, "--output"),
    slot: str = typer.Option("evening", "--slot"),
    llm: bool = typer.Option(False, "--llm/--no-llm"),
) -> None:
    evening_report_cmd(on_date=on_date, output=output, slot=slot, llm=llm)


@app.command("scan", help="Alias for scan-candidates")
def scan_alias(
    on_date: str = typer.Option(None, "--date"),
    top_n: int = typer.Option(20, "--top-n"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
) -> None:
    scan_candidates(on_date=on_date, top_n=top_n, persist=persist)


@app.command("evaluate-judgments")
def evaluate_judgments_cmd(
    judgment_date: str = typer.Option(..., "--judgment-date",
                                      help="Date when the judgment was made (YYYY-MM-DD)"),
) -> None:
    """Score pending judgments from a given date against realized data."""
    target = date.fromisoformat(judgment_date)
    engine = get_engine()
    n = evaluate_judgments(engine, target)
    console.print(f"[green]✓ evaluated {n} judgments from {target}[/]")


@app.command("compute-metrics")
def compute_metrics_cmd(
    on_date: str = typer.Option(None, "--date", help="Date YYYY-MM-DD (default: today BJT)"),
) -> None:
    """Compute ta.setup_metrics_daily — rolling 60d/250d edge per setup."""
    target = date.fromisoformat(on_date) if on_date else bjt_now().date()
    engine = get_engine()
    n = compute_setup_metrics(engine, target)
    console.print(f"[green]✓ wrote {n} rows to ta.setup_metrics_daily for {target}[/]")


@app.command("evening-report")
def evening_report_cmd(
    on_date: str = typer.Option(None, "--date", help="Trade date YYYY-MM-DD (default: today BJT)"),
    output: str = typer.Option(None, "--output",
                               help="Output dir override; default = "
                                    "<output_root>/<mode>/<YYYYMMDD>/ta/. "
                                    "Pass '-' to print MD to stdout."),
    slot: str = typer.Option("evening", "--slot", help="Report slot label (evening/morning/intraday)"),
    llm: bool = typer.Option(False, "--llm/--no-llm", help="Add LLM narrative sections"),
) -> None:
    """Generate the TA evening report (HTML + MD).

    Filename: ifa_TA_{slot}_{trade_date YYYYMMDD}_{generation HHMM BJT}.{html,md}
    """
    from pathlib import Path

    target = date.fromisoformat(on_date) if on_date else bjt_now().date()
    engine = get_engine()
    augmenter = None
    if llm:
        from ifa.families.ta.report.llm_aug import TALLMAugmenter
        augmenter = TALLMAugmenter()
    report = build_evening_report(engine, target, augmenter=augmenter)

    if output == "-":
        console.print(render_markdown(report))
        return

    if output is None:
        from ifa.config import get_settings
        from ifa.core.report.output import output_dir_for_family
        out_dir = output_dir_for_family(get_settings(), "ta", target)
    else:
        out_dir = Path(output)
        out_dir.mkdir(parents=True, exist_ok=True)
    stamp_date = target.strftime("%Y%m%d")
    stamp_time = bjt_now().strftime("%H%M")
    base = f"ifa_TA_{slot}_{stamp_date}_{stamp_time}"
    html_path = out_dir / f"{base}.html"
    md_path = out_dir / f"{base}.md"
    html_path.write_text(render_html(report), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    console.print(f"[green]✓ HTML[/]  {html_path}")
    console.print(f"[green]✓ MD[/]    {md_path}")


@app.command("backfill-regime")
def backfill_regime(
    start: str = typer.Option(..., "--start", help="Start date YYYY-MM-DD inclusive"),
    end: str = typer.Option(None, "--end", help="End date YYYY-MM-DD inclusive (default: today BJT)"),
) -> None:
    """Backfill ta.regime_daily over a date range. Skips dates without index data."""
    engine = get_engine()
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end) if end else bjt_now().date()

    from ifa.core.calendar import trading_days_between
    trade_days = trading_days_between(engine, start_d, end_d)
    n_done = 0
    for cur in trade_days:
        ctx = load_regime_context(engine, cur)
        if ctx.n_up is None:
            console.print(f"  [yellow]{cur} skipped[/] — no market_state_daily row "
                          f"(trade_cal says open; data not loaded yet)")
            continue
        result = classify_regime(ctx)
        upsert_regime_daily(engine, cur, result)
        console.print(f"  {cur} → {result.regime:25} ({result.confidence:.2f})")
        n_done += 1

    skipped = (end_d - start_d).days + 1 - len(trade_days)
    console.print(f"\n[bold green]Done.[/] {n_done} classified across {len(trade_days)} trade days "
                  f"(non-trade days skipped via trade_cal: {skipped}).")
