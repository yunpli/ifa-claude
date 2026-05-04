"""`ifa research ...` CLI — single-stock report + peer scan + batch.

Subcommands:
    ifa research report <name-or-code> [--output tmp/]
    ifa research peer-scan <name-or-code> [--max-peers N] [--full]
    ifa research batch <code1> <code2> ...

The CLI is a thin wrapper around the analyzer + report layers; it does not
add new business logic. Reports are written to `tmp/research_<ts>.{html,md}`
by default. Use `--output -` to print markdown to stdout.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ifa.core.report.timezones import bjt_now
from ifa.families.research.analyzer.balance import compute_balance
from ifa.families.research.analyzer.cash_quality import compute_cash_quality
from ifa.families.research.analyzer.data import load_company_snapshot
from ifa.families.research.analyzer.factors import load_params
from ifa.families.research.analyzer.governance import compute_governance
from ifa.families.research.analyzer.growth import compute_growth
from ifa.families.research.analyzer.peer import attach_peer_ranks
from ifa.families.research.analyzer.persistence import persist_all_families
from ifa.families.research.analyzer.profitability import compute_profitability
from ifa.families.research.analyzer.scoring import score_results
from ifa.families.research.fetcher.client import fetch_all, fetch_stock_basic
from ifa.families.research.peer_scan import scan_l2_universe, scan_universe
from ifa.families.research.jobs.company_events import extract_events_for_company
from ifa.families.research.report import build_research_report, render_markdown
from ifa.families.research.report.html import HtmlRenderer
from ifa.families.research.resolver import (
    AmbiguousCompanyError,
    CompanyNotFoundError,
    CompanyRef,
    resolve,
    upsert_company_identity,
)

console = Console()
app = typer.Typer(no_args_is_help=True, help="Research family — equity research reports.")

_DEFAULT_OUTPUT = Path("tmp")


def _engine():
    from ifa.core.db import get_engine
    return get_engine()


def _resolve_or_bootstrap(query: str, engine) -> CompanyRef:
    """Resolve query → CompanyRef. If it's a ts_code that's not in
    company_identity yet, fetch stock_basic and upsert before retrying.
    """
    try:
        return resolve(query, engine)
    except CompanyNotFoundError:
        pass

    # Try bootstrap: if it looks like a ts_code or 6-digit, call stock_basic.
    if "." in query or query.isdigit():
        ts_code = query.upper()
        if ts_code.isdigit() and len(ts_code) == 6:
            # Infer suffix
            first = ts_code[0]
            ts_code = f"{ts_code}.{ {'0':'SZ','3':'SZ','6':'SH','8':'BJ','4':'BJ','9':'BJ'}.get(first,'SZ') }"
        sb = fetch_stock_basic(engine, ts_code)
        if sb:
            info = sb[0]
            upsert_company_identity(
                engine, ts_code=ts_code,
                name=str(info.get("name") or ""),
                exchange=str(info.get("exchange") or ""),
                market=info.get("market"),
                list_status=info.get("list_status"),
            )
            console.print(f"[dim]bootstrapped identity for {ts_code}[/dim]")
            return resolve(ts_code, engine)
    raise CompanyNotFoundError(query)


# ─── report ───────────────────────────────────────────────────────────────────

@app.command("report")
def cmd_report(
    query: str = typer.Argument(..., help="Stock code (001339.SZ) or name (智微智能)"),
    output: str = typer.Option(str(_DEFAULT_OUTPUT), "--output", "-o",
                               help="Output dir, or '-' for stdout markdown"),
    no_persist: bool = typer.Option(False, "--no-persist",
                                    help="Skip writing factor_value rows (read-only mode)"),
    cutoff: str | None = typer.Option(None, "--cutoff",
                                      help="Data cutoff YYYY-MM-DD (default: today)"),
    llm: bool = typer.Option(False, "--llm",
                             help="Add LLM narrative paragraphs to each section"),
    tier: str = typer.Option("standard", "--tier",
                             help="Report tier: quick (rules-only, ~5s) | "
                                  "standard (default, +trends/timeline) | "
                                  "deep (+watchpoints, requires --llm)"),
    pdf: bool = typer.Option(False, "--pdf",
                             help="Also produce a PDF (requires Chrome installed)"),
) -> None:
    """Generate a single-stock research report.

    Pipeline: resolve → fetch_all (cached) → snapshot → compute 28 factors
              → persist → peer rank → score → render HTML+MD.
    """
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    engine = _engine()
    cutoff_date = date.fromisoformat(cutoff) if cutoff else bjt_now().date()

    try:
        company = _resolve_or_bootstrap(query, engine)
    except (CompanyNotFoundError, AmbiguousCompanyError) as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[bold]{company.name}[/bold] · {company.ts_code} · {company.exchange}")

    fetch_all(engine, company.ts_code, company.exchange, verbose=False)
    snap = load_company_snapshot(engine, company, data_cutoff_date=cutoff_date)

    params = load_params()
    results_by_family = {
        "profitability": compute_profitability(snap, params),
        "growth":        compute_growth(snap, params),
        "cash_quality":  compute_cash_quality(snap, params),
        "balance":       compute_balance(snap, params),
        "governance":    compute_governance(snap, params),
    }

    if not no_persist:
        persist_all_families(engine, company.ts_code, results_by_family)
    for results in results_by_family.values():
        attach_peer_ranks(engine, results, snap)
    if not no_persist:
        persist_all_families(engine, company.ts_code, results_by_family)

    scoring = score_results(results_by_family, params)

    if tier not in ("quick", "standard", "deep"):
        console.print(f"[red]✗[/red] Invalid --tier {tier!r}; must be quick|standard|deep")
        raise typer.Exit(1)

    # Quick tier never invokes LLM (saves API cost; the whole point is fast).
    augmenter = None
    if llm and tier == "quick":
        console.print("[yellow]⚠[/yellow]  --llm is ignored for --tier quick "
                      "(quick tier is rules-only by design).")
    elif llm:
        from ifa.families.research.report.llm_aug import LLMAugmenter
        augmenter = LLMAugmenter(cache_engine=engine)
        console.print(f"[dim]LLM narratives enabled (tier={tier}, cached per factor state)[/dim]")
    elif tier == "deep":
        console.print("[yellow]⚠[/yellow]  --tier deep without --llm: "
                      "watchpoints section will be empty (requires LLM).")

    report = build_research_report(
        snap, results_by_family, scoring, params,
        tier=tier, augmenter=augmenter, engine=engine,
    )

    if output == "-":
        console.print(render_markdown(report))
        return

    out_dir = Path(output)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{tier}" if tier != "standard" else ""
    stamp = bjt_now().strftime("%Y%m%d")
    base = f"Stock-Analysis-{company.ts_code}-{stamp}{suffix}"
    html_path = out_dir / f"{base}.html"
    md_path = out_dir / f"{base}.md"
    html_path.write_text(HtmlRenderer().render(report=report), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    pdf_path: Path | None = None
    if pdf:
        try:
            from ifa.core.render.pdf import html_to_pdf
            pdf_path = html_to_pdf(html_path)
        except FileNotFoundError as e:
            console.print(f"[yellow]⚠[/yellow]  PDF skipped: {e}")
        except RuntimeError as e:
            # Chrome not installed / conversion failed
            console.print(f"[yellow]⚠[/yellow]  PDF generation failed: {e}")
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow]  PDF unexpected error: {e}")

    _print_score_summary(company, scoring)
    console.print(f"[green]✓[/green] HTML → {html_path}")
    console.print(f"[green]✓[/green] MD   → {md_path}")
    if pdf_path:
        console.print(f"[green]✓[/green] PDF  → {pdf_path}")


# ─── peer-scan ────────────────────────────────────────────────────────────────

@app.command("peer-scan")
def cmd_peer_scan(
    query: str = typer.Argument(..., help="Anchor stock for L2 lookup"),
    max_peers: int = typer.Option(20, "--max-peers", "-n",
                                  help="Cap peers per L2 (default 20)"),
    full: bool = typer.Option(False, "--full",
                              help="Production mode: scan entire SW L2 (no cap)"),
    skip_fresh: bool = typer.Option(True, "--skip-fresh/--no-skip-fresh",
                                    help="Skip stocks computed within last 24h"),
) -> None:
    """Populate research.factor_value for the anchor's SW L2 cohort.

    Use --full in production for statistically meaningful peer percentiles
    (default sample of 20 is a dev/test convenience). Full scan of one ~100-
    member L2 takes ~30 minutes.
    """
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    engine = _engine()

    try:
        company = _resolve_or_bootstrap(query, engine)
    except (CompanyNotFoundError, AmbiguousCompanyError) as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)

    cap = None if full else max_peers
    console.print(f"Scanning SW L2 of {company.name} ({company.ts_code}); "
                  f"cap={'full' if full else cap}")
    result = scan_l2_universe(engine, company.ts_code,
                              max_peers=cap, skip_fresh=skip_fresh)
    console.print(result.summary())
    if result.failures:
        console.print(f"[yellow]failures (first 5): {result.failures[:5]}[/yellow]")


# ─── scan-universe (production multi-L2) ─────────────────────────────────────

@app.command("scan-universe")
def cmd_scan_universe(
    anchors: list[str] = typer.Option(
        None, "--anchor", "-a",
        help="Anchor stocks (multi-allowed); their L2s will be scanned."
    ),
    l2: list[str] = typer.Option(
        None, "--l2",
        help="Explicit SW L2 codes (e.g. 801101.SI); multi-allowed."
    ),
    full: bool = typer.Option(False, "--full",
                              help="Production mode: no max_peers cap."),
    max_peers: int = typer.Option(20, "--max-peers", "-n",
                                  help="Cap per L2 when --full not set"),
    concurrency: int = typer.Option(3, "--concurrency", "-c",
                                    help="Parallel workers per L2 (default 3, max suggested 5)"),
    skip_fresh: bool = typer.Option(True, "--skip-fresh/--no-skip-fresh",
                                    help="Skip stocks already computed within 24h"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Show planned L2s + member counts; do not execute"),
) -> None:
    """Production peer-universe scan across multiple SW L2 cohorts.

    Picks L2s by: --l2 (explicit) > --anchor (anchors' L2s) > all L2s of
    stocks already in research.company_identity.

    Each L2 is run sequentially but stocks within an L2 are processed
    concurrently (default 3 workers; raise carefully — Tushare rate-limits
    above ~6 req/sec).

    Audit trail: each invocation gets a run_id, written to research.scan_run.
    """
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    engine = _engine()

    cap = None if full else max_peers

    if dry_run:
        from datetime import date

        from sqlalchemy import text

        from ifa.families.research.peer_scan import _resolve_targeted_l2s
        targeted = _resolve_targeted_l2s(
            engine, l2_codes=l2 or None,
            anchor_ts_codes=anchors or None,
            on_date=bjt_now().date(),
        )
        snapshot_month = bjt_now().date().replace(day=1)
        t = Table(title="Dry run — planned scan")
        t.add_column("L2", style="cyan")
        t.add_column("Anchor")
        t.add_column("Members", justify="right")
        t.add_column("Already fresh (24h)", justify="right")
        with engine.connect() as c:
            for l2_code, anchor in targeted:
                n_members = c.execute(text("""
                    SELECT COUNT(*) FROM smartmoney.sw_member_monthly
                    WHERE l2_code = :c AND snapshot_month = :sm
                """), {"c": l2_code, "sm": snapshot_month}).scalar() or 0
                n_fresh = c.execute(text("""
                    SELECT COUNT(DISTINCT fv.ts_code)
                    FROM research.factor_value fv
                    JOIN smartmoney.sw_member_monthly sm
                      ON fv.ts_code = sm.ts_code
                     AND sm.snapshot_month = :sm
                     AND sm.l2_code = :c
                    WHERE fv.computed_at > NOW() - INTERVAL '24 hours'
                """), {"c": l2_code, "sm": snapshot_month}).scalar() or 0
                cap_str = "all" if cap is None else str(min(n_members, cap))
                t.add_row(l2_code, anchor, f"{n_members} (cap={cap_str})", str(n_fresh))
        console.print(t)
        console.print(f"[dim]Would execute {len(targeted)} L2 scans. Use without --dry-run to run.[/dim]")
        return

    console.print(f"[bold]scan-universe[/bold] cap={'full' if full else cap}, "
                  f"concurrency={concurrency}, skip_fresh={skip_fresh}")

    report = scan_universe(
        engine,
        anchor_ts_codes=anchors or None,
        l2_codes=l2 or None,
        max_peers=cap,
        skip_fresh=skip_fresh,
        concurrency=concurrency,
    )

    console.print()
    t = Table(title=f"Universe scan {report.run_id}", show_lines=False)
    t.add_column("L2", style="cyan")
    t.add_column("Name")
    t.add_column("Members", justify="right")
    t.add_column("Scanned", justify="right")
    t.add_column("Fresh", justify="right")
    t.add_column("Delisted", justify="right")
    t.add_column("Failed", justify="right")
    for r in report.l2_results:
        t.add_row(
            r.sw_l2_code or "—",
            r.sw_l2_name or "—",
            str(r.members_total),
            str(r.scanned),
            str(r.skipped_fresh),
            str(r.skipped_delisted),
            str(r.failed),
        )
    console.print(t)
    console.print(f"[bold]{report.summary()}[/bold]")


# ─── scan-status (read-only health view) ─────────────────────────────────────

@app.command("scan-status")
def cmd_scan_status(
    hours: int = typer.Option(24, "--hours",
                              help="Look back this many hours (default 24)"),
    show_failures: bool = typer.Option(False, "--failures",
                                       help="Include per-L2 failure details"),
) -> None:
    """Inspect scan_run audit trail — confirm scans completed and surface failures.

    Health rules of thumb:
      · partial / failed L2s in last 24h → investigate
      · age of last 'succeeded' for each L2 should be ≤ 25h for daily cron
      · scanned + skipped_fresh ≈ members_total means coverage is healthy
    """
    from sqlalchemy import text
    engine = _engine()

    with engine.connect() as c:
        # Aggregate distribution
        agg = c.execute(text("""
            SELECT status, COUNT(*) AS n,
                   SUM(scanned) AS total_scanned,
                   SUM(skipped_fresh) AS total_fresh,
                   SUM(failed) AS total_failed
            FROM research.scan_run
            WHERE started_at > NOW() - make_interval(hours => :h)
            GROUP BY status
            ORDER BY 1
        """), {"h": hours}).fetchall()

        # Per-L2 latest run. For 'running' rows, dur_s = elapsed since started
        # (so the operator can spot stuck/zombie rows). For finished rows,
        # dur_s = actual end - start.
        rows = c.execute(text("""
            SELECT DISTINCT ON (l2_code)
                   l2_code, l2_name, status, members_total,
                   scanned, skipped_fresh, failed,
                   started_at, completed_at,
                   EXTRACT(EPOCH FROM (
                       COALESCE(completed_at, NOW()) - started_at
                   ))::int AS dur_s,
                   failures
            FROM research.scan_run
            WHERE started_at > NOW() - make_interval(hours => :h)
            ORDER BY l2_code, started_at DESC
        """), {"h": hours}).fetchall()

        # Coverage: total stocks in factor_value
        n_stocks = c.execute(text("""
            SELECT COUNT(DISTINCT ts_code) FROM research.factor_value
        """)).scalar()

    if not rows:
        console.print(f"[yellow]No scan_run rows in last {hours}h.[/yellow]")
        return

    # Aggregate panel
    a = Table(title=f"scan_run aggregate — last {hours}h", show_lines=False)
    a.add_column("Status")
    a.add_column("L2s", justify="right")
    a.add_column("Scanned", justify="right")
    a.add_column("Fresh", justify="right")
    a.add_column("Failed", justify="right")
    icon_map = {"succeeded": "🟢", "partial": "🟡",
                "failed": "🔴", "running": "⏳"}
    for status, n, sc, fr, fl in agg:
        a.add_row(f"{icon_map.get(status,'?')} {status}",
                  str(n), str(sc or 0), str(fr or 0), str(fl or 0))
    console.print(a)
    console.print(f"\n[dim]Total stocks in factor_value: {n_stocks}[/dim]\n")

    # Per-L2 detail
    t = Table(title=f"per-L2 latest run — last {hours}h",
              show_lines=False)
    t.add_column("L2", style="cyan")
    t.add_column("Name")
    t.add_column("Status", justify="center")
    t.add_column("Mem", justify="right")
    t.add_column("Scan", justify="right")
    t.add_column("Fresh", justify="right")
    t.add_column("Fail", justify="right")
    t.add_column("Dur", justify="right")
    t.add_column("Started")
    for r in rows:
        (l2_code, l2_name, status, mem, sc, fr, fl,
         started, completed, dur_s, _failures) = r
        from ifa.core.report.timezones import to_bjt
        started_bjt = to_bjt(started)
        t.add_row(
            l2_code or "—",
            (l2_name or "—")[:14],
            f"{icon_map.get(status, '?')} {status}",
            str(mem),
            str(sc),
            str(fr),
            f"[red]{fl}[/red]" if fl else "0",
            f"{dur_s or 0}s",
            started_bjt.strftime("%m-%d %H:%M BJT") if started_bjt else "—",
        )
    console.print(t)

    if show_failures:
        any_failed = False
        for r in rows:
            failures = r[10]
            if failures:
                if not any_failed:
                    console.print("\n[bold]Failures:[/bold]")
                    any_failed = True
                console.print(f"[yellow]L2 {r[0]} ({r[1]}):[/yellow]")
                for ts_code, err in failures[:10]:
                    console.print(f"  · {ts_code}: {err}")
        if not any_failed:
            console.print("[green]No failures recorded.[/green]")


# ─── scan-cleanup (finalize zombie 'running' rows) ───────────────────────────

@app.command("scan-cleanup")
def cmd_scan_cleanup(
    older_than_minutes: int = typer.Option(
        30, "--older-than", "-t",
        help="Mark 'running' rows older than this as 'failed' (default 30 min)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Show what would be cleaned, don't update"),
) -> None:
    """Reset zombie 'running' rows from interrupted scans.

    A scan_run row enters 'running' state at L2 start and is meant to be
    finalized to 'succeeded'/'partial'/'failed' at end. If a scan-universe
    process is killed mid-flight (Ctrl-C, OOM, terminal closed) the row
    stays 'running' forever, polluting scan-status.

    This command finds rows still 'running' after `older_than_minutes` and
    marks them as 'failed' with reason='stale_run_cleanup'. Safe to run any
    time — won't touch in-flight scans (they finish in seconds-to-minutes,
    not 30 minutes).
    """
    from sqlalchemy import text
    engine = _engine()

    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT run_id, l2_code, l2_name,
                   EXTRACT(EPOCH FROM (NOW() - started_at))::int AS age_s
            FROM research.scan_run
            WHERE status = 'running'
              AND started_at < NOW() - make_interval(mins => :m)
            ORDER BY started_at
        """), {"m": older_than_minutes}).fetchall()

    if not rows:
        console.print(f"[green]✓[/green] No zombie 'running' rows older than "
                      f"{older_than_minutes} min.")
        return

    console.print(f"[yellow]Found {len(rows)} zombie row(s):[/yellow]")
    for run_id, l2_code, l2_name, age_s in rows:
        console.print(f"  · {l2_code} ({l2_name or '—'}) — age {age_s}s "
                      f"(run_id={str(run_id)[:8]})")

    if dry_run:
        console.print("[dim]--dry-run; no changes made.[/dim]")
        return

    with engine.begin() as c:
        n = c.execute(text("""
            UPDATE research.scan_run
            SET status = 'failed',
                completed_at = NOW(),
                failures = COALESCE(failures, '[]'::jsonb)
                           || '[["__stale__", "stale_run_cleanup"]]'::jsonb
            WHERE status = 'running'
              AND started_at < NOW() - make_interval(mins => :m)
        """), {"m": older_than_minutes}).rowcount
    console.print(f"[green]✓[/green] Marked {n} row(s) as failed.")


# ─── extract-events (LLM event memory job) ───────────────────────────────────

@app.command("extract-events")
def cmd_extract_events(
    query: str = typer.Argument(..., help="Stock code or name"),
    max_per_source: int = typer.Option(15, "--max-per-source",
                                       help="Cap items per source (announcements/research/IRM)"),
    max_age_days: int = typer.Option(365, "--max-age-days",
                                     help="Ignore disclosures older than this"),
) -> None:
    """Extract structured events from disclosures and persist to
    research.company_event_memory. Idempotent: re-runs skip already-extracted
    events (same event_id).

    Use this as a periodic job (after each major filing) to keep the event
    memory current. The data feeds future report sections (§07/§09/§12).
    """
    from datetime import date

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    engine = _engine()

    try:
        company = _resolve_or_bootstrap(query, engine)
    except (CompanyNotFoundError, AmbiguousCompanyError) as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)

    fetch_all(engine, company.ts_code, company.exchange, verbose=False)
    snap = load_company_snapshot(engine, company, data_cutoff_date=bjt_now().date())

    console.print(f"[bold]{company.name}[/bold] · extracting events…")
    report = extract_events_for_company(
        engine, snap,
        max_per_source=max_per_source,
        max_age_days=max_age_days,
    )
    console.print(report.summary())
    if report.failures:
        console.print(f"[yellow]Failures (first 5):[/yellow] {report.failures[:5]}")


# ─── peer-rank-refresh (recompute peer ranks for all persisted stocks) ──────

@app.command("peer-rank-refresh")
def cmd_peer_rank_refresh(
    factor: str = typer.Option(
        None, "--factor",
        help="Restrict to one factor (e.g. ROE). Default: all industry-sensitive factors.",
    ),
    limit: int = typer.Option(
        0, "--limit",
        help="Cap number of (stock × factor) updates (0 = no cap). For testing.",
    ),
) -> None:
    """Recompute peer_rank / peer_percentile for all rows in research.factor_value.

    Why this exists separately from scan-universe:
      · scan-universe persists factor values WITHOUT peer rank (by design —
        peer rank requires the full cohort to be present, which it isn't
        until the entire L2 has been scanned).
      · This command does pure SQL + arithmetic, no Tushare hits, takes
        a few minutes for the whole market.

    Run AFTER scan-universe completes to populate peer rank columns. Idempotent;
    re-running is safe.
    """
    from sqlalchemy import text

    from ifa.core.report.timezones import bjt_now
    from ifa.families.research.analyzer.peer import _rank_against_peers

    engine = _engine()
    snapshot_month = bjt_now().date().replace(day=1)

    # Find all (factor_name, l2_code) groups that have ≥ min_peer_count members
    # AND industry_sensitive direction (peer rank only meaningful for those).
    where_factor = "AND fv.factor_name = :factor" if factor else ""

    sql_groups = text(f"""
        SELECT fv.factor_name, sm.l2_code, sm.l1_code, fv.direction,
               COUNT(*) AS n
        FROM research.factor_value fv
        JOIN smartmoney.sw_member_monthly sm
          ON fv.ts_code = sm.ts_code
         AND sm.snapshot_month = :sm
        WHERE fv.value IS NOT NULL
          AND fv.direction IN ('higher_better', 'lower_better', 'in_band')
          {where_factor}
        GROUP BY fv.factor_name, sm.l2_code, sm.l1_code, fv.direction
        HAVING COUNT(*) >= 8
    """)
    params = {"sm": snapshot_month}
    if factor:
        params["factor"] = factor

    with engine.connect() as c:
        groups = c.execute(sql_groups, params).fetchall()

    console.print(f"[dim]Computing peer ranks across {len(groups)} (factor × L2) groups…[/dim]")

    update_sql = text("""
        UPDATE research.factor_value
        SET peer_rank = :rank, peer_total = :total, peer_percentile = :pct
        WHERE ts_code = :tc AND factor_name = :fn AND period = :period
    """)

    total_updates = 0
    for factor_name, l2_code, l1_code, direction, n in groups:
        if limit and total_updates >= limit:
            break
        # Pull all (ts_code, value, period) for this group
        rows = []
        with engine.connect() as c:
            data = c.execute(
                text("""
                    SELECT fv.ts_code, fv.value, fv.period
                    FROM research.factor_value fv
                    JOIN smartmoney.sw_member_monthly sm
                      ON fv.ts_code = sm.ts_code
                     AND sm.snapshot_month = :sm
                     AND sm.l2_code = :l2
                    WHERE fv.factor_name = :fn AND fv.value IS NOT NULL
                """),
                {"sm": snapshot_month, "l2": l2_code, "fn": factor_name},
            ).fetchall()

        peers = [(r[0], float(r[1])) for r in data]
        period_by_code = {r[0]: r[2] for r in data}

        with engine.begin() as c:
            for ts_code, _value in peers:
                if limit and total_updates >= limit:
                    break
                rank_result = _rank_against_peers(
                    ts_code=ts_code, value=peers and dict(peers)[ts_code],
                    peers=peers, direction=direction, universe="sw_l2",
                )
                c.execute(update_sql, {
                    "rank": rank_result.rank,
                    "total": rank_result.total,
                    "pct": float(rank_result.percentile_0_100),
                    "tc": ts_code,
                    "fn": factor_name,
                    "period": period_by_code[ts_code],
                })
                total_updates += 1

    console.print(f"[green]✓[/green] Updated {total_updates} (stock × factor) peer ranks.")


# ─── rank (cross-stock screening) ────────────────────────────────────────────

@app.command("rank")
def cmd_rank(
    factor: str = typer.Option(
        "overall_score", "--factor", "-f",
        help="Factor name (e.g. ROE, GPM, NPM_DEDT, FCF) or 'overall_score' for 5-dim avg"
    ),
    family: str = typer.Option(
        None, "--family",
        help="Restrict to one family: profitability/growth/cash_quality/balance/governance"
    ),
    l2: str = typer.Option(
        None, "--l2",
        help="Restrict to one SW L2 code (e.g. 801101.SI)"
    ),
    top: int = typer.Option(20, "--top", "-n", help="Show top N (and bottom N if --bottom)"),
    bottom: bool = typer.Option(False, "--bottom",
                                help="Also show bottom N (worst rather than best)"),
    direction: str = typer.Option(
        "auto", "--direction",
        help="Sort: auto (use factor's spec.direction) | higher | lower"
    ),
    status: str = typer.Option(
        None, "--status",
        help="Filter to one status: green / yellow / red / unknown"
    ),
) -> None:
    """Cross-stock ranking — find best / worst stocks by any factor or overall score.

    Reads from research.factor_value (populated by scan-universe). Joins with
    company_identity for names and sw_member_monthly for industry context.

    Examples:
        ifa research rank                                         # top 20 by overall score
        ifa research rank --factor ROE --top 10                   # top 10 by ROE
        ifa research rank --factor ROE --l2 801101.SI             # within 计算机设备
        ifa research rank --factor FCF --status red --top 30      # worst FCF in RED status
        ifa research rank --family cash_quality --bottom          # worst cash_quality
    """
    from sqlalchemy import text

    engine = _engine()

    # Special case: overall_score is computed across all factors
    if factor == "overall_score":
        _rank_by_overall(engine, l2=l2, top=top, bottom=bottom)
        return

    # Direction: respect factor spec unless explicitly overridden
    if direction == "auto":
        direction = _lookup_factor_direction(engine, factor)

    sort_dir = "DESC" if direction in ("higher_better", "higher") else "ASC"
    if bottom:
        # Reverse for "worst first"
        sort_dir = "ASC" if sort_dir == "DESC" else "DESC"

    sql = f"""
        SELECT fv.ts_code, ci.name, sm.l2_code, sm.l2_name,
               fv.value, fv.unit, fv.status, fv.peer_percentile,
               fv.peer_rank, fv.peer_total
        FROM research.factor_value fv
        LEFT JOIN research.company_identity ci ON fv.ts_code = ci.ts_code
        LEFT JOIN smartmoney.sw_member_monthly sm
            ON fv.ts_code = sm.ts_code
            AND sm.snapshot_month = date_trunc('month', CURRENT_DATE)::date
        WHERE fv.factor_name = :factor
          AND fv.value IS NOT NULL
          {"AND sm.l2_code = :l2" if l2 else ""}
          {"AND fv.status = :status" if status else ""}
          {"AND fv.family = :family" if family else ""}
        ORDER BY fv.value {sort_dir} NULLS LAST
        LIMIT :n
    """
    params: dict = {"factor": factor, "n": top}
    if l2:
        params["l2"] = l2
    if status:
        params["status"] = status
    if family:
        params["family"] = family

    with engine.connect() as c:
        rows = c.execute(text(sql), params).fetchall()

    if not rows:
        console.print(f"[yellow]No rows for factor={factor!r}{' L2=' + l2 if l2 else ''}.[/yellow]")
        console.print("[dim]Tip: run `ifa research scan-universe` first to populate factor_value.[/dim]")
        return

    title = f"Top {top} by {factor}"
    if bottom:
        title = f"Bottom {top} by {factor}"
    if l2:
        title += f" — L2 {l2}"
    if status:
        title += f" — status={status}"

    t = Table(title=title)
    t.add_column("#", justify="right", style="dim")
    t.add_column("Code", style="cyan")
    t.add_column("Name")
    t.add_column("L2", style="dim")
    t.add_column("Value", justify="right")
    t.add_column("Status", justify="center")
    t.add_column("Peer", justify="center")

    icon = {"green": "🟢", "yellow": "🟡", "red": "🔴", "unknown": "⬜"}
    for i, r in enumerate(rows, start=1):
        ts_code, name, l2_code, l2_name, value, unit, st, p_pct, p_rank, p_total = r
        val_str = _fmt_factor_value(value, unit)
        peer_str = "—"
        if p_rank is not None and p_total is not None:
            pct = f" P{int(p_pct)}" if p_pct is not None else ""
            peer_str = f"{p_rank}/{p_total}{pct}"
        t.add_row(
            str(i), ts_code, (name or "—")[:14],
            (l2_name or "—")[:8],
            val_str,
            icon.get(st, "?"),
            peer_str,
        )
    console.print(t)


def _lookup_factor_direction(engine, factor_name: str) -> str:
    """Pull direction from factor_value (any row). Falls back to 'higher_better'."""
    from sqlalchemy import text
    with engine.connect() as c:
        row = c.execute(
            text("""
                SELECT direction FROM research.factor_value
                WHERE factor_name = :fn AND direction IS NOT NULL
                LIMIT 1
            """),
            {"fn": factor_name},
        ).fetchone()
    return row[0] if row else "higher_better"


def _rank_by_overall(engine, *, l2: str | None, top: int, bottom: bool) -> None:
    """Rank by overall score = simple average of family scores.

    Each family score = avg(blend(status_base, peer_pct)) for that family's
    factors, where status_base is GREEN=80/YELLOW=50/RED=20 and blend uses
    50/50 weighting when peer_pct is present (mirrors scoring.py logic).
    """
    from sqlalchemy import text

    sql = f"""
        WITH per_factor AS (
            SELECT fv.ts_code, fv.family,
                   CASE fv.status
                     WHEN 'green' THEN 80.0
                     WHEN 'yellow' THEN 50.0
                     WHEN 'red' THEN 20.0
                     ELSE NULL
                   END AS base_score,
                   fv.peer_percentile
            FROM research.factor_value fv
            WHERE fv.value IS NOT NULL
              {"AND fv.ts_code IN (SELECT ts_code FROM smartmoney.sw_member_monthly WHERE l2_code = :l2 AND snapshot_month = date_trunc('month', CURRENT_DATE)::date)" if l2 else ""}
        ),
        per_factor_blend AS (
            SELECT ts_code, family,
                   CASE
                     WHEN base_score IS NOT NULL AND peer_percentile IS NOT NULL
                       THEN 0.5 * base_score + 0.5 * peer_percentile
                     ELSE base_score
                   END AS blend
            FROM per_factor
            WHERE base_score IS NOT NULL OR peer_percentile IS NOT NULL
        ),
        per_family AS (
            SELECT ts_code, family, AVG(blend) AS family_score
            FROM per_factor_blend
            WHERE blend IS NOT NULL
            GROUP BY ts_code, family
        ),
        per_stock AS (
            SELECT ts_code, AVG(family_score) AS overall_score,
                   COUNT(*) AS n_families
            FROM per_family
            GROUP BY ts_code
            HAVING COUNT(*) >= 3   -- need at least 3 of 5 families
        )
        SELECT ps.ts_code, ci.name, sm.l2_code, sm.l2_name,
               ps.overall_score, ps.n_families
        FROM per_stock ps
        LEFT JOIN research.company_identity ci ON ps.ts_code = ci.ts_code
        LEFT JOIN smartmoney.sw_member_monthly sm
            ON ps.ts_code = sm.ts_code
            AND sm.snapshot_month = date_trunc('month', CURRENT_DATE)::date
        ORDER BY ps.overall_score {"ASC" if bottom else "DESC"} NULLS LAST
        LIMIT :n
    """
    params: dict = {"n": top}
    if l2:
        params["l2"] = l2

    with engine.connect() as c:
        rows = c.execute(text(sql), params).fetchall()

    if not rows:
        console.print("[yellow]No rows. Run `ifa research scan-universe` first.[/yellow]")
        return

    title = f"{'Bottom' if bottom else 'Top'} {top} by overall score"
    if l2:
        title += f" — L2 {l2}"

    t = Table(title=title)
    t.add_column("#", justify="right", style="dim")
    t.add_column("Code", style="cyan")
    t.add_column("Name")
    t.add_column("L2", style="dim")
    t.add_column("Score", justify="right")
    t.add_column("Verdict", justify="center")
    t.add_column("Coverage", justify="right")

    for i, (ts_code, name, l2_code, l2_name, score, n_fam) in enumerate(rows, start=1):
        score_f = float(score)
        if score_f >= 70:
            verdict = "🟢 健康"
        elif score_f >= 50:
            verdict = "🟡 谨慎"
        else:
            verdict = "🔴 高风险"
        t.add_row(
            str(i), ts_code, (name or "—")[:14],
            (l2_name or "—")[:8],
            f"{score_f:.1f}",
            verdict,
            f"{n_fam}/5",
        )
    console.print(t)


def _fmt_factor_value(value, unit: str | None) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit == "元":
        if abs(v) >= 1e8:
            return f"{v/1e8:.2f}亿"
        if abs(v) >= 1e4:
            return f"{v/1e4:.2f}万"
        return f"{v:.0f}"
    if unit in ("%", "pp"):
        return f"{v:+.2f}{unit}" if unit == "pp" else f"{v:.2f}%"
    if unit == "x":
        return f"{v:.2f}x"
    if unit == "天":
        return f"{v:.1f}天"
    if unit == "次":
        return f"{int(v)}次"
    return f"{v:.2f}"


# ─── industry-view (L2 cohort dashboard) ────────────────────────────────────

@app.command("industry-view")
def cmd_industry_view(
    l2: str = typer.Argument(..., help="SW L2 code (e.g. 801101.SI) or L2 name (计算机设备)"),
    top_bottom: int = typer.Option(5, "--top", "-n",
                                   help="Show top/bottom N by overall score"),
) -> None:
    """One-page summary of an SW L2 cohort.

    Shows: cohort size, family-score distribution, top/bottom N by overall,
    factor-level RED/YELLOW heatmap. Useful for "is this whole industry
    struggling, or is it just this one stock?"

    Example:
        ifa research industry-view 801101.SI
        ifa research industry-view 计算机设备
    """
    from sqlalchemy import text

    from ifa.core.report.timezones import bjt_now

    engine = _engine()
    snapshot_month = bjt_now().date().replace(day=1)

    # Resolve L2 name → code if needed
    if not l2.endswith(".SI"):
        with engine.connect() as c:
            row = c.execute(
                text("""
                    SELECT DISTINCT l2_code FROM smartmoney.sw_member_monthly
                    WHERE l2_name = :n AND snapshot_month = :sm LIMIT 1
                """),
                {"n": l2, "sm": snapshot_month},
            ).fetchone()
        if not row:
            console.print(f"[red]✗[/red] L2 not found: {l2!r}")
            raise typer.Exit(1)
        l2_code = row[0]
        l2_name = l2
    else:
        l2_code = l2
        with engine.connect() as c:
            row = c.execute(
                text("""
                    SELECT l2_name FROM smartmoney.sw_member_monthly
                    WHERE l2_code = :c AND snapshot_month = :sm LIMIT 1
                """),
                {"c": l2_code, "sm": snapshot_month},
            ).fetchone()
        l2_name = row[0] if row else l2_code

    console.print(f"\n[bold]{l2_name} ({l2_code})[/bold]\n")

    # 1. Cohort summary
    with engine.connect() as c:
        members = c.execute(
            text("""
                SELECT COUNT(DISTINCT ts_code) FROM smartmoney.sw_member_monthly
                WHERE l2_code = :c AND snapshot_month = :sm
            """),
            {"c": l2_code, "sm": snapshot_month},
        ).scalar()
        scanned = c.execute(
            text("""
                SELECT COUNT(DISTINCT fv.ts_code)
                FROM research.factor_value fv
                JOIN smartmoney.sw_member_monthly sm
                  ON fv.ts_code = sm.ts_code
                 AND sm.snapshot_month = :sm AND sm.l2_code = :c
            """),
            {"c": l2_code, "sm": snapshot_month},
        ).scalar()

    console.print(f"成员: {members} 只票 · factor_value 已覆盖: {scanned} ({scanned*100//max(members,1)}%)\n")

    # 2. Family score distribution
    family_sql = text("""
        WITH per_factor AS (
            SELECT fv.ts_code, fv.family,
                   CASE fv.status
                     WHEN 'green' THEN 80.0
                     WHEN 'yellow' THEN 50.0
                     WHEN 'red' THEN 20.0 END AS base_score,
                   fv.peer_percentile
            FROM research.factor_value fv
            JOIN smartmoney.sw_member_monthly sm
              ON fv.ts_code = sm.ts_code
             AND sm.snapshot_month = :sm AND sm.l2_code = :c
            WHERE fv.value IS NOT NULL
        ),
        per_factor_blend AS (
            SELECT ts_code, family,
                   CASE
                     WHEN base_score IS NOT NULL AND peer_percentile IS NOT NULL
                       THEN 0.5*base_score + 0.5*peer_percentile
                     ELSE base_score END AS blend
            FROM per_factor
        ),
        per_family AS (
            SELECT family, AVG(blend) AS avg_score, COUNT(DISTINCT ts_code) AS n
            FROM per_factor_blend WHERE blend IS NOT NULL
            GROUP BY family
        )
        SELECT family, avg_score, n FROM per_family
        ORDER BY family
    """)
    with engine.connect() as c:
        rows = c.execute(family_sql, {"c": l2_code, "sm": snapshot_month}).fetchall()

    fam_label = {"profitability": "盈利", "growth": "增长",
                 "cash_quality": "现金", "balance": "结构", "governance": "治理"}
    t = Table(title="行业 5 维平均分")
    t.add_column("维度", style="cyan")
    t.add_column("平均分", justify="right")
    t.add_column("覆盖", justify="right")
    t.add_column("行业判定", justify="center")
    for fam, score, n in rows:
        s = float(score)
        if s >= 65:
            verdict = "🟢 行业稳健"
        elif s >= 50:
            verdict = "🟡 中性"
        else:
            verdict = "🔴 整体承压"
        t.add_row(fam_label.get(fam, fam), f"{s:.1f}", str(n), verdict)
    console.print(t)

    # 3. Top + Bottom by overall
    overall_sql = text("""
        WITH per_factor AS (
            SELECT fv.ts_code, fv.family,
                   CASE fv.status WHEN 'green' THEN 80.0 WHEN 'yellow' THEN 50.0
                                  WHEN 'red' THEN 20.0 END AS base_score,
                   fv.peer_percentile
            FROM research.factor_value fv
            JOIN smartmoney.sw_member_monthly sm
              ON fv.ts_code = sm.ts_code
             AND sm.snapshot_month = :sm AND sm.l2_code = :c
            WHERE fv.value IS NOT NULL
        ),
        per_factor_blend AS (
            SELECT ts_code, family,
                   CASE WHEN base_score IS NOT NULL AND peer_percentile IS NOT NULL
                          THEN 0.5*base_score + 0.5*peer_percentile
                        ELSE base_score END AS blend
            FROM per_factor
        ),
        per_family AS (
            SELECT ts_code, family, AVG(blend) AS s FROM per_factor_blend
            WHERE blend IS NOT NULL GROUP BY ts_code, family
        ),
        per_stock AS (
            SELECT ts_code, AVG(s) AS overall FROM per_family
            GROUP BY ts_code HAVING COUNT(*) >= 3
        )
        SELECT ps.ts_code, ci.name, ps.overall
        FROM per_stock ps
        LEFT JOIN research.company_identity ci ON ps.ts_code = ci.ts_code
        ORDER BY ps.overall DESC
    """)
    with engine.connect() as c:
        all_stocks = c.execute(overall_sql, {"c": l2_code, "sm": snapshot_month}).fetchall()

    top_n = all_stocks[:top_bottom]
    bot_n = all_stocks[-top_bottom:][::-1]

    t = Table(title=f"Top {top_bottom} (best in cohort)")
    t.add_column("Code", style="cyan")
    t.add_column("Name")
    t.add_column("Score", justify="right")
    for tc, name, s in top_n:
        t.add_row(tc, (name or "—")[:14], f"{float(s):.1f}")
    console.print(t)

    t = Table(title=f"Bottom {top_bottom} (worst in cohort)")
    t.add_column("Code", style="cyan")
    t.add_column("Name")
    t.add_column("Score", justify="right")
    for tc, name, s in bot_n:
        t.add_row(tc, (name or "—")[:14], f"{float(s):.1f}")
    console.print(t)

    # 4. RED/YELLOW heatmap by factor
    heatmap_sql = text("""
        SELECT fv.factor_name, fv.family,
               SUM(CASE WHEN fv.status = 'red' THEN 1 ELSE 0 END) AS red,
               SUM(CASE WHEN fv.status = 'yellow' THEN 1 ELSE 0 END) AS yellow,
               SUM(CASE WHEN fv.status = 'green' THEN 1 ELSE 0 END) AS green,
               COUNT(*) AS total
        FROM research.factor_value fv
        JOIN smartmoney.sw_member_monthly sm
          ON fv.ts_code = sm.ts_code
         AND sm.snapshot_month = :sm AND sm.l2_code = :c
        WHERE fv.value IS NOT NULL OR fv.status IN ('green','yellow','red')
        GROUP BY fv.factor_name, fv.family
        ORDER BY (SUM(CASE WHEN fv.status = 'red' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0)) DESC
        LIMIT 10
    """)
    with engine.connect() as c:
        heat = c.execute(heatmap_sql, {"c": l2_code, "sm": snapshot_month}).fetchall()

    t = Table(title="Top 10 因子 RED 占比 (cohort 普遍承压的方向)")
    t.add_column("因子", style="cyan")
    t.add_column("族")
    t.add_column("R", justify="right")
    t.add_column("Y", justify="right")
    t.add_column("G", justify="right")
    t.add_column("RED %", justify="right")
    for factor_name, family, red, yellow, green, total in heat:
        red_pct = red * 100 // total if total else 0
        bar = "█" * (red_pct // 10) + "░" * (10 - red_pct // 10)
        t.add_row(
            factor_name, fam_label.get(family, family or "?"),
            str(red), str(yellow), str(green),
            f"{bar} {red_pct}%",
        )
    console.print(t)


# ─── batch ────────────────────────────────────────────────────────────────────

@app.command("batch")
def cmd_batch(
    queries: list[str] = typer.Argument(..., help="Multiple codes/names"),
    output: str = typer.Option(str(_DEFAULT_OUTPUT), "--output", "-o"),
    cutoff: str | None = typer.Option(None, "--cutoff"),
) -> None:
    """Render reports for multiple stocks. Failures don't stop the batch."""
    failed: list[tuple[str, str]] = []
    for q in queries:
        try:
            cmd_report(query=q, output=output, no_persist=False, cutoff=cutoff)
        except typer.Exit:
            failed.append((q, "see error above"))
        except Exception as e:
            failed.append((q, str(e)[:200]))
            console.print(f"[red]✗[/red] {q}: {e}")
    if failed:
        console.print(f"\n[yellow]Failed: {len(failed)}/{len(queries)}[/yellow]")
        for q, err in failed:
            console.print(f"  {q}: {err}")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _print_score_summary(company: CompanyRef, scoring) -> None:
    t = Table(title=f"5维评分 · {company.name}", show_lines=False)
    t.add_column("维度", style="cyan")
    t.add_column("得分", justify="right")
    t.add_column("状态", justify="center")
    t.add_column("覆盖", justify="right")
    icons = {"green": "🟢", "yellow": "🟡", "red": "🔴", "unknown": "⬜"}
    for fam in scoring.families.values():
        score = f"{fam.score:.1f}" if fam.score is not None else "—"
        t.add_row(fam.label_zh, score, icons.get(fam.status.value, "?"),
                  f"{fam.weight_coverage*100:.0f}%")
    console.print(t)
    overall = f"{scoring.overall_score:.1f}" if scoring.overall_score is not None else "—"
    console.print(f"[bold]总分 {overall} {icons.get(scoring.overall_status.value, '?')} "
                  f"{scoring.overall_label_zh}[/bold]")
