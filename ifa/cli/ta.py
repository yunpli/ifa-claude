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
from ifa.families.ta.setups.repo import upsert_candidates
from ifa.families.ta.setups.scanner import scan as scan_setups
from ifa.families.ta.setups.tracking import evaluate_for_date

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
    """Scan all 19 setups across the full market for a date; rank + persist candidates."""
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

    candidates = scan_setups(contexts.values())
    console.print(f"  raw candidates: {len(candidates)}")

    ranked = rank_candidates(candidates, top_n=top_n)
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


@app.command("track-candidates")
def track_candidates(
    start: str = typer.Option(..., "--start", help="Candidate generation date YYYY-MM-DD"),
    horizons: list[int] = typer.Option([1, 3, 10], "--horizon",
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
