#!/usr/bin/env python3
"""Parallel ningbo backfill — Phase 2.

Two-stage pipeline optimized for the full 2021-2026 history:

  Stage 1 — Parallel signal pass (skip_tracking):
    Runs 6 independent subprocesses (one per year).
    Each subprocess: loads raw data, computes heuristic signals,
    inserts recommendations into DB.
    No cross-year dependencies → safe to parallelize.
    Expected time: ~60-90 min (depends on hardware/DB I/O).

  Stage 2 — Bulk SQL tracking pass (sequential, fast):
    After all recommendations inserted, a single SQL batch computes
    all 15-day tracking rows and terminal outcomes.
    Expected time: ~5-10 min (pure PostgreSQL window functions + CTEs).

Usage:
    # Full 2021-04-30 backfill (both stages)
    uv run python scripts/ningbo_backfill_parallel.py

    # Stage 1 only (run stage 2 separately later)
    uv run python scripts/ningbo_backfill_parallel.py --signals-only

    # Stage 2 only (if stage 1 already done)
    uv run python scripts/ningbo_backfill_parallel.py --tracking-only

    # Specific date range (sequential, useful for debugging)
    uv run python scripts/ningbo_backfill_parallel.py --start 2026-01-01 --end 2026-04-30

    # Use different scoring mode (default: heuristic)
    uv run python scripts/ningbo_backfill_parallel.py --scoring heuristic

Hardware requirements:
    - RAM: ~1 GB (6 processes × ~80 MB each + Python overhead)
    - CPU: 6+ cores ideal (1 core per year process)
    - DB: PostgreSQL at localhost:55432
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

# One date range per year. Adjust start/end to match your raw_daily coverage.
# All ranges are inclusive. 2021 starts from the first A-share trading day.
YEAR_RANGES = [
    ("2021-01-04", "2021-12-31", "2021"),
    ("2022-01-01", "2022-12-31", "2022"),
    ("2023-01-01", "2023-12-31", "2023"),
    ("2024-01-01", "2024-12-31", "2024"),
    ("2025-01-01", "2025-12-31", "2025"),
    ("2026-01-01", "2026-04-30", "2026"),
]

# Number of parallel workers for stage 1 (match to your CPU core count).
# 6 is ideal (one per year range). Reduce if you're memory-constrained.
MAX_WORKERS = 6

# ── Helpers ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent  # project root


def _banner(msg: str) -> None:
    width = 72
    print(f"\n{'─' * width}")
    print(f"  {msg}")
    print(f"{'─' * width}")


def run_year_backfill(start: str, end: str, label: str, scoring: str) -> tuple[str, int, str, str]:
    """Run backfill for one year as a subprocess.  Returns (label, returncode, stdout, stderr)."""
    cmd = [
        "uv", "run", "python", "-m", "ifa.cli",
        "ningbo", "backfill",
        "--start", start,
        "--end", end,
        "--scoring", scoring,
        "--skip-tracking",
        "--mode", "manual",
        "--quiet",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    return label, result.returncode, result.stdout, result.stderr


def run_bulk_tracking(start: str, end: str, scoring: str) -> int:
    """Run bulk SQL tracking pass via CLI."""
    cmd = [
        "uv", "run", "python", "-m", "ifa.cli",
        "ningbo", "tracking",
        "--start", start,
        "--end", end,
        "--scoring", scoring,
        "--mode", "manual",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default=None, help="Override start date (YYYY-MM-DD)")
    parser.add_argument("--end",   default=None, help="Override end date (YYYY-MM-DD)")
    parser.add_argument("--scoring", default="heuristic", help="Scoring mode [heuristic]")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help=f"Parallel workers for stage 1 [default: {MAX_WORKERS}]")
    parser.add_argument("--signals-only",  action="store_true", help="Run stage 1 only (skip tracking pass)")
    parser.add_argument("--tracking-only", action="store_true", help="Run stage 2 only (skip signal pass)")
    args = parser.parse_args()

    if args.start and args.end:
        # Split custom range into yearly sub-ranges for parallelism.
        # Splitting by year ensures no intra-day overlap between workers.
        import datetime as dt
        s = dt.date.fromisoformat(args.start)
        e = dt.date.fromisoformat(args.end)
        year_ranges = []
        for yr in range(s.year, e.year + 1):
            yr_start = max(s, dt.date(yr, 1, 1))
            yr_end   = min(e, dt.date(yr, 12, 31))
            if yr_start <= yr_end:
                year_ranges.append((yr_start.isoformat(), yr_end.isoformat(), str(yr)))
    else:
        year_ranges = YEAR_RANGES

    overall_start = year_ranges[0][0]
    overall_end   = year_ranges[-1][1]

    _banner(f"Ningbo backfill  {overall_start} → {overall_end}  scoring={args.scoring}")
    print(f"Stage 1 workers: {args.workers}  |  ranges: {len(year_ranges)}")
    print()

    t_global = time.time()
    errors = []

    # ── Stage 1: Parallel signal computation + rec inserts ────────────────────
    if not args.tracking_only:
        _banner("Stage 1: Parallel signal pass (skip_tracking=True)")
        t1 = time.time()

        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(run_year_backfill, start, end, label, args.scoring): label
                for start, end, label in year_ranges
            }
            for future in as_completed(futures):
                label, rc, stdout, stderr = future.result()
                if rc == 0:
                    # Extract the summary line from stdout
                    summary = next(
                        (line for line in stdout.splitlines() if "Backfill complete" in line),
                        f"[{label}] done (no summary line)"
                    )
                    print(f"  ✓ {label:6s}  {summary.strip()}")
                else:
                    print(f"  ❌ {label:6s}  FAILED (exit {rc})")
                    if stderr:
                        for line in stderr.splitlines()[:5]:
                            print(f"    {line}")
                    errors.append(label)

        elapsed1 = time.time() - t1
        print(f"\nStage 1 done: {elapsed1:.0f}s  ({elapsed1/60:.1f} min)  errors={len(errors)}")

        if errors:
            print(f"\n⚠️  Failed years: {errors}")
            print("    Fix errors and re-run with --signals-only (safe to re-run — all writes are UPSERT).")
            if not args.signals_only:
                print("    Aborting before tracking pass due to errors.")
                sys.exit(1)

        if args.signals_only:
            _banner("Stage 1 complete. Run with --tracking-only to compute tracking + outcomes.")
            sys.exit(0)

    # ── Stage 2: Bulk SQL tracking pass ──────────────────────────────────────
    if not args.signals_only:
        _banner(f"Stage 2: Bulk SQL tracking pass ({overall_start} → {overall_end})")
        t2 = time.time()
        rc2 = run_bulk_tracking(overall_start, overall_end, args.scoring)
        elapsed2 = time.time() - t2
        print(f"\nStage 2 done: {elapsed2:.0f}s  ({elapsed2/60:.1f} min)")
        if rc2 != 0:
            print(f"❌ Tracking pass failed (exit {rc2})")
            sys.exit(1)

    elapsed_total = time.time() - t_global
    _banner(f"All done  {elapsed_total:.0f}s  ({elapsed_total/60:.1f} min)")

    # ── Quick stats ──────────────────────────────────────────────────────────
    print("\nTo view performance statistics, run:")
    print("  uv run python -m ifa.cli ningbo stats --mode manual")


if __name__ == "__main__":
    main()
