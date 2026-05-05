"""TA 360-day backfill — extend lookback from 180d (2025-06-03) to 360d (2024-12-01).

What this script does (idempotent — safe to re-run):

  1. factor_pro_daily        — Tushare stk_factor_pro per trade_date
  2. cyq_perf_daily          — Tushare cyq_perf per trade_date (full market)
  3. event_signal_daily      — forecast / express / disclosure_pre per date
  4. blacklist_daily         — anns_d title-keyword scan + forecast losses
  5. candidates_daily        — _scan_and_persist_one_day for each date
  6. position_events_daily   — evaluate_for_date with horizon=15
  7. setup_metrics_daily     — compute_setup_metrics_v2 (combined_score_60d)
  8. Final re-scan           — once more so combined_score takes effect on Tier ranking

Window: 2024-12-01 → 2025-06-02 (extends the 180d coverage to 360d).
Existing 2025-06-03 → 2026-04-30 data is preserved via UPSERT.

Time estimate (M1 Mac):
  · factor_pro:  ~1.5 sec/day × 120 days ≈  3 min
  · cyq_perf:    ~1.5 sec/day × 120 days ≈  3 min
  · events:      ~1.5 sec/day × 120 days ≈  3 min
  · blacklist:   ~1.5 sec/day × 120 days ≈  3 min
  · scan+track:    ~5 sec/day × 120 days ≈ 10 min
  · metrics_v2:    ~1 sec/day × 240 days ≈  4 min
  · final re-scan: ~5 sec/day × 360 days ≈ 30 min
  · TOTAL                                   ≈ 55-60 min

Usage:
    cd /Users/neoclaw/claude/ifa-claude
    uv run python scripts/ta_backfill_360d.py

Logs printed to stdout. Safe to Ctrl+C and resume — every step UPSERTs.

After completion, validate with:
    uv run python -m ifa.cli ta tier-perf --start 2024-12-01 --end 2026-04-14
    uv run python -m ifa.cli ta tier-perf --start 2025-09-01 --end 2026-04-14   # 180d for comparison
"""
from __future__ import annotations

import logging
import time
from datetime import date

from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.core.tushare.client import TuShareClient

# Minimal logging — info-level shows phase progress, warnings still surface.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ta_backfill_360d")

START = date(2024, 12, 1)
END_NEW = date(2025, 6, 2)        # new backfill ends here; 2025-06-03 onwards is existing
END_TOTAL = date(2026, 4, 14)     # final re-scan + metrics span 2024-12-01 → 2026-04-14


def main() -> None:
    eng = get_engine()
    cli = TuShareClient()
    pipeline_t0 = time.time()

    # ── 1. Load list of new trade dates ─────────────────────────────────────
    with eng.connect() as c:
        new_dates = [
            r[0] for r in c.execute(text(
                "SELECT DISTINCT trade_date FROM smartmoney.raw_daily "
                "WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"
            ), {"s": START, "e": END_NEW})
        ]
        full_dates = [
            r[0] for r in c.execute(text(
                "SELECT DISTINCT trade_date FROM smartmoney.raw_daily "
                "WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"
            ), {"s": START, "e": END_TOTAL})
        ]
    log.info("new dates to backfill: %d (%s → %s)", len(new_dates), START, END_NEW)
    log.info("full pipeline window: %d (%s → %s)", len(full_dates), START, END_TOTAL)

    # ── 2. factor_pro_daily ─────────────────────────────────────────────────
    from ifa.families.ta.etl.factor_pro import fetch_and_store_factor_pro
    log.info("[1/8] factor_pro_daily backfill — %d dates", len(new_dates))
    t0 = time.time(); rows = 0
    for i, d in enumerate(new_dates, 1):
        try:
            rows += fetch_and_store_factor_pro(eng, d)
        except Exception as e:
            log.warning("  factor_pro %s failed: %s", d, e)
        if i % 30 == 0:
            log.info("  factor_pro: %d/%d (%ds, %d rows)", i, len(new_dates), time.time() - t0, rows)
    log.info("[1/8] factor_pro done: %d rows in %ds", rows, time.time() - t0)

    # ── 3. cyq_perf_daily ───────────────────────────────────────────────────
    from ifa.families.ta.etl.cyq import fetch_cyq_perf_full_market
    log.info("[2/8] cyq_perf_daily backfill — %d dates", len(new_dates))
    t0 = time.time(); rows = 0
    for i, d in enumerate(new_dates, 1):
        try:
            rows += fetch_cyq_perf_full_market(eng, d)
        except Exception as e:
            log.warning("  cyq %s failed: %s", d, e)
        if i % 30 == 0:
            log.info("  cyq: %d/%d (%ds, %d rows)", i, len(new_dates), time.time() - t0, rows)
    log.info("[2/8] cyq done: %d rows in %ds", rows, time.time() - t0)

    # ── 4. event_signal_daily ───────────────────────────────────────────────
    from ifa.families.ta.etl.event_etl import fetch_event_signals
    log.info("[3/8] event_signal_daily backfill — %d dates", len(new_dates))
    t0 = time.time(); rows = 0
    for i, d in enumerate(new_dates, 1):
        try:
            rows += fetch_event_signals(cli, eng, trade_date=d)
        except Exception as e:
            log.warning("  events %s failed: %s", d, e)
        if i % 30 == 0:
            log.info("  events: %d/%d (%ds, %d rows)", i, len(new_dates), time.time() - t0, rows)
    log.info("[3/8] events done: %d rows in %ds", rows, time.time() - t0)

    # ── 5. blacklist_daily ──────────────────────────────────────────────────
    from ifa.families.ta.etl.blacklist_etl import fetch_blacklist
    log.info("[4/8] blacklist_daily backfill — %d dates", len(new_dates))
    t0 = time.time(); rows = 0
    for i, d in enumerate(new_dates, 1):
        try:
            rows += fetch_blacklist(cli, eng, trade_date=d)
        except Exception as e:
            log.warning("  blacklist %s failed: %s", d, e)
        if i % 30 == 0:
            log.info("  blacklist: %d/%d (%ds, %d rows)", i, len(new_dates), time.time() - t0, rows)
    log.info("[4/8] blacklist done: %d rows in %ds", rows, time.time() - t0)

    # ── 6. Initial candidates_daily + position_events scan over new dates ──
    from ifa.families.ta.backtest.runner import _scan_and_persist_one_day
    from ifa.families.ta.setups.position_tracker import evaluate_for_date
    log.info("[5/8] scan + position-track new dates — %d", len(new_dates))
    t0 = time.time()
    for i, d in enumerate(new_dates, 1):
        _scan_and_persist_one_day(eng, d)
        evaluate_for_date(eng, d, horizon=15, top_watchlist_only=False)
        if i % 20 == 0:
            log.info("  scan+track: %d/%d (%ds)", i, len(new_dates), time.time() - t0)
    log.info("[5/8] scan+track done in %ds", time.time() - t0)

    # ── 7. metrics_v2 over full window so combined_score has lookback ───────
    from ifa.families.ta.metrics_v2 import compute_setup_metrics_v2
    log.info("[6/8] metrics_v2 over full window — %d dates", len(full_dates))
    t0 = time.time(); rows = 0
    for i, d in enumerate(full_dates, 1):
        rows += compute_setup_metrics_v2(eng, d)
        if i % 60 == 0:
            log.info("  metrics: %d/%d (%ds, %d rows)", i, len(full_dates), time.time() - t0, rows)
    log.info("[6/8] metrics done: %d rows in %ds", rows, time.time() - t0)

    # ── 8. Final re-scan over FULL window so new combined_score affects Tier ranking ──
    log.info("[7/8] final re-scan over full window with new metrics — %d dates", len(full_dates))
    t0 = time.time()
    for i, d in enumerate(full_dates, 1):
        _scan_and_persist_one_day(eng, d)
        evaluate_for_date(eng, d, horizon=15, top_watchlist_only=False)
        if i % 30 == 0:
            log.info("  final-scan: %d/%d (%ds)", i, len(full_dates), time.time() - t0)
    log.info("[7/8] final re-scan done in %ds", time.time() - t0)

    # ── 9. Coverage summary ─────────────────────────────────────────────────
    log.info("[8/8] coverage check")
    with eng.connect() as c:
        for tbl, where_col in [
            ("ta.factor_pro_daily", "trade_date"),
            ("ta.cyq_perf_daily", "trade_date"),
            ("ta.event_signal_daily", "trade_date"),
            ("ta.blacklist_daily", "trade_date"),
            ("ta.candidates_daily", "trade_date"),
            ("ta.position_events_daily", "generation_date"),
            ("ta.setup_metrics_daily", "trade_date"),
        ]:
            r = c.execute(text(
                f"SELECT MIN({where_col}), MAX({where_col}), COUNT(DISTINCT {where_col}) "
                f"FROM {tbl} WHERE {where_col} >= :s"
            ), {"s": START}).first()
            log.info("  %-32s %s → %s (%d days)", tbl, r[0], r[1], r[2] or 0)

    elapsed = time.time() - pipeline_t0
    log.info("=" * 60)
    log.info("ALL DONE in %d minutes %d seconds", elapsed // 60, elapsed % 60)
    log.info("Run measurement:")
    log.info("  uv run python -m ifa.cli ta tier-perf --start 2024-12-01 --end 2026-04-14")
    log.info("  uv run python -m ifa.cli ta tier-perf --start 2025-09-01 --end 2026-04-14")


if __name__ == "__main__":
    main()
