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


@app.command("backfill-regime")
def backfill_regime(
    start: str = typer.Option(..., "--start", help="Start date YYYY-MM-DD inclusive"),
    end: str = typer.Option(None, "--end", help="End date YYYY-MM-DD inclusive (default: today BJT)"),
) -> None:
    """Backfill ta.regime_daily over a date range. Skips dates without index data."""
    engine = get_engine()
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end) if end else bjt_now().date()

    cur = start_d
    n_done = 0
    n_skipped = 0
    while cur <= end_d:
        ctx = load_regime_context(engine, cur)
        # market_state_daily uses exact-date match → None on non-trade days
        if ctx.n_up is None:
            n_skipped += 1
        else:
            result = classify_regime(ctx)
            upsert_regime_daily(engine, cur, result)
            console.print(f"  {cur} → {result.regime:25} ({result.confidence:.2f})")
            n_done += 1
        cur += timedelta(days=1)

    console.print(f"\n[bold green]Done.[/] {n_done} classified, {n_skipped} non-trade days skipped.")
