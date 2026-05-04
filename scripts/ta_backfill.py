"""TA pipeline historical backfill — candidates + tracking + metrics.

For a [start, end] date range:
  1. For each trade day: scan-candidates → ta.candidates_daily
  2. For each trade day in [start, end] track at h=1,3,10 against forward windows
     (skips days whose eval_date isn't in raw_daily yet)
  3. compute setup_metrics for `end` (and optionally for every day in range)

Run:
    uv run python scripts/ta_backfill.py --start 2026-01-02 --end 2026-04-30
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date

from sqlalchemy import text

from ifa.core.calendar import trading_days_between
from ifa.core.db import get_engine
from ifa.families.ta.metrics import compute_setup_metrics
from ifa.families.ta.regime.classifier import classify_regime
from ifa.families.ta.regime.loader import load_regime_context
from ifa.families.ta.regime.repo import upsert_regime_daily
from ifa.families.ta.setups.context_loader import build_contexts
from ifa.families.ta.setups.ranker import rank as rank_candidates
from ifa.families.ta.setups.repo import upsert_candidates, upsert_warnings
from ifa.families.ta.setups.scanner import scan as scan_setups
from ifa.families.ta.setups.tracking import evaluate_for_date

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def run_backfill(start: date, end: date, *, top_n: int = 20,
                 horizons=(1, 3, 5, 10, 30)) -> None:
    engine = get_engine()
    days = trading_days_between(engine, start, end)
    log.info("backfill %s → %s · %d trade days · top_n=%d", start, end, len(days), top_n)

    # Phase 1: candidates per day
    t0 = time.time()
    n_candidates_total = 0
    for i, d in enumerate(days, 1):
        # ensure regime exists for that day (re-classify if missing)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT regime FROM ta.regime_daily WHERE trade_date = :d"),
                {"d": d},
            ).fetchone()
        if row is None:
            ctx = load_regime_context(engine, d)
            if ctx.n_up is None:
                continue
            res = classify_regime(ctx)
            upsert_regime_daily(engine, d, res)
            regime = res.regime
        else:
            regime = row[0]

        contexts = build_contexts(engine, d, regime=regime)
        if not contexts:
            continue
        candidates, warnings = scan_setups(contexts.values())

        # Load most recent setup_metrics for M5.3 gating (uses prior-day metrics)
        with engine.connect() as conn:
            latest = conn.execute(
                text("SELECT MAX(trade_date) FROM ta.setup_metrics_daily WHERE trade_date < :d"),
                {"d": d},
            ).scalar()
            setup_metrics: dict = {}
            if latest:
                for row in conn.execute(
                    text("""SELECT setup_name, decay_score, suitable_regimes,
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

        ranked = rank_candidates(candidates, top_n=top_n,
                                 current_regime=regime, setup_metrics=setup_metrics)
        n = upsert_candidates(engine, d, ranked, regime_at_gen=regime)
        n_candidates_total += n
        if warnings:
            upsert_warnings(engine, d, warnings, regime_at_gen=regime)
        if i % 5 == 0 or i == len(days):
            elapsed = time.time() - t0
            log.info("  cand %d/%d  %s  +%d (cumulative %d, %.1fs)",
                     i, len(days), d, n, n_candidates_total, elapsed)

    # Phase 2: tracking
    log.info("phase 2: tracking horizons=%s", horizons)
    t1 = time.time()
    n_tracked = 0
    for d in days:
        for h in horizons:
            n = evaluate_for_date(engine, d, horizon_days=h)
            n_tracked += n
    log.info("  tracked %d rows in %.1fs", n_tracked, time.time() - t1)

    # Phase 3: metrics — for the last day only (rolling stats look back 60d/250d)
    log.info("phase 3: metrics @ %s", end)
    n_metrics = compute_setup_metrics(engine, end)
    log.info("  wrote %d setup_metrics_daily rows", n_metrics)

    log.info("DONE.  %d candidates · %d tracked · %d metrics · %.1f total minutes",
             n_candidates_total, n_tracked, n_metrics, (time.time() - t0) / 60)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--top-n", type=int, default=20)
    args = p.parse_args()
    run_backfill(date.fromisoformat(args.start), date.fromisoformat(args.end),
                 top_n=args.top_n)


if __name__ == "__main__":
    main()
