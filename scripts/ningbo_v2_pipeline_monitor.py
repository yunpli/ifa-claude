#!/usr/bin/env python3
"""Monitor the Phase 3.B candidate backfill, then auto-run outcomes + train-v2.

Polls ningbo.candidates_daily every 60s.  When all three year-process
ranges are complete (2024 ends ≥ Dec 30, 2025 ends ≥ Dec 30, 2026
ends ≥ Mar 28), kicks off:
    1. ifa ningbo backfill-candidates (just to compute outcomes pass)
       → actually we call compute_candidate_outcomes() directly
    2. ifa ningbo train-v2 --activate

Usage:
    uv run python scripts/ningbo_v2_pipeline_monitor.py

Prereqs:
    - 3 backfill processes running for 2024/2025/2026 (Jan-Mar)
      via `ifa ningbo backfill-candidates --skip-outcomes ...`
    - 2026-04 already done by earlier smoke test
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

# Flush prints immediately so users see progress in real time / logs
import functools
print = functools.partial(print, flush=True)  # type: ignore

ROOT = Path(__file__).parent.parent

# Expected end-dates per year process. We accept "close enough" — the last
# trading day of each range, allowing 2 calendar-day slack for holidays.
EXPECTED_RANGES = {
    2024: dt.date(2024, 12, 31),  # 2024 process: --end 2024-12-31
    2025: dt.date(2025, 12, 31),  # 2025 process: --end 2025-12-31
    2026: dt.date(2026, 4, 30),   # 2026 process + smoke: --end 2026-04-30
}
# Slack: trade_cal might end the year a few days before Dec 31
TOLERANCE_DAYS = 5


def fetch_progress() -> dict[int, tuple[int, dt.date | None]]:
    """Return {year: (count, max_rec_date)} from DB."""
    from ifa.core.db import get_engine
    from ifa.config import get_settings
    from sqlalchemy import text

    e = get_engine(get_settings())
    out: dict[int, tuple[int, dt.date | None]] = {}
    with e.connect() as c:
        rows = c.execute(text("""
            SELECT EXTRACT(YEAR FROM rec_date)::int AS yr,
                   COUNT(*) AS n,
                   MAX(rec_date)
            FROM ningbo.candidates_daily
            GROUP BY 1
        """)).fetchall()
        for r in rows:
            out[r[0]] = (int(r[1]), r[2])
    return out


def all_years_done(progress: dict[int, tuple[int, dt.date | None]]) -> bool:
    """True iff every expected year has reached close to its expected end."""
    for yr, expected_end in EXPECTED_RANGES.items():
        cnt, last = progress.get(yr, (0, None))
        if last is None:
            return False
        if (expected_end - last).days > TOLERANCE_DAYS:
            return False
    return True


def processes_alive() -> int:
    """Count of running `ifa.cli ningbo backfill-candidates` processes."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", "ningbo backfill-candidates"],
            capture_output=True, text=True,
        )
        if out.returncode == 0:
            return len([p for p in out.stdout.split() if p.strip()])
        return 0
    except FileNotFoundError:
        return -1  # pgrep unavailable


def fmt_progress(progress: dict[int, tuple[int, dt.date | None]]) -> str:
    parts = []
    total_n = 0
    for yr in sorted(EXPECTED_RANGES):
        cnt, last = progress.get(yr, (0, None))
        total_n += cnt
        parts.append(f"{yr}={cnt:,}@{last or '—'}")
    return f"total={total_n:,}  " + "  ".join(parts)


def main() -> None:
    poll_sec = 60
    started = dt.datetime.now()
    print(f"╔══════════════════════════════════════════════════════════╗")
    print(f"║ Ningbo V2 pipeline monitor — started {started:%Y-%m-%d %H:%M}  ║")
    print(f"╠══════════════════════════════════════════════════════════╣")
    print(f"║ Polling every {poll_sec}s                                          ║")
    print(f"║ Expected: 2024 → Dec, 2025 → Dec, 2026 → Apr             ║")
    print(f"╚══════════════════════════════════════════════════════════╝\n")

    last_total = -1
    while True:
        progress = fetch_progress()
        elapsed_min = (dt.datetime.now() - started).total_seconds() / 60
        n_alive = processes_alive()
        total = sum(c for c, _ in progress.values())
        delta = (total - last_total) if last_total >= 0 else 0
        last_total = total
        print(
            f"[{dt.datetime.now():%H:%M:%S}] "
            f"elapsed={elapsed_min:5.1f}m  procs={n_alive}  Δ={delta:+,}  "
            f"{fmt_progress(progress)}"
        )

        if all_years_done(progress):
            print("\n✅ All three year ranges complete. Proceeding to next steps.\n")
            break

        if n_alive == 0 and total > 0:
            print(f"\n⚠️  No backfill processes running but ranges incomplete:")
            for yr, expected_end in EXPECTED_RANGES.items():
                cnt, last = progress.get(yr, (0, None))
                done = last and (expected_end - last).days <= TOLERANCE_DAYS
                flag = "✓" if done else "✗"
                print(f"     {flag} {yr}: {cnt:,} candidates, last={last}, expected={expected_end}")
            print("\n   Aborting auto-pipeline. Restart processes manually if needed.")
            sys.exit(1)

        time.sleep(poll_sec)

    # ── Step 1: Compute candidate outcomes (bulk SQL only, ~2 min) ──────────
    print("\n" + "─" * 60)
    print("STEP 1/2: Computing candidate outcomes (bulk SQL)…")
    print("─" * 60)
    rc = subprocess.run(
        ["uv", "run", "python", "-m", "ifa.cli", "ningbo", "candidate-outcomes",
         "--start", "2024-01-02", "--end", "2026-04-30", "--mode", "manual"],
        cwd=ROOT,
    ).returncode
    if rc != 0:
        print(f"❌ Step 1 failed (exit {rc})")
        sys.exit(rc)

    # ── Step 2: Train v2 models ─────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("STEP 2/2: Training v2 models (LR + RF + XGB-clf + XGB-ranker)…")
    print("─" * 60)
    rc = subprocess.run(
        ["uv", "run", "python", "-m", "ifa.cli", "ningbo", "train-v2",
         "--in-sample-end", "2025-09-30",
         "--oos-end",       "2026-04-30",
         "--activate",
         "--mode", "manual"],
        cwd=ROOT,
    ).returncode
    if rc != 0:
        print(f"❌ Step 2 failed (exit {rc})")
        sys.exit(rc)

    elapsed = (dt.datetime.now() - started).total_seconds() / 60
    print(f"\n🎉 Pipeline complete  total elapsed = {elapsed:.1f} min")


if __name__ == "__main__":
    main()
