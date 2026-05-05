"""Run a single yaml-variant param tuning experiment over 360d.

Idempotent + safe: backs up yaml, applies patches, runs 360d re-scan +
position-track + tier-perf, restores yaml at end (or on Ctrl+C).

Usage:
    uv run python scripts/ta_tune_experiment.py --experiment iter6_atr_tighter
    uv run python scripts/ta_tune_experiment.py --experiment iter7_tier_a_strict
    ...

Available experiments listed in EXPERIMENTS dict below. Each writes
results to /tmp/ta_<label>_<timestamp>.txt for later comparison.

Time per experiment: ~25-30 min on M1.

Chain multiple back-to-back:
    for exp in iter6_atr_tighter iter7_tier_a_strict iter8_trend_smallcap; do
      uv run python scripts/ta_tune_experiment.py --experiment $exp
    done
"""
from __future__ import annotations

import argparse
import logging
import shutil
import time
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import yaml
from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.families.ta.backtest.runner import _scan_and_persist_one_day
from ifa.families.ta.params import reload_params
from ifa.families.ta.setups.position_tracker import evaluate_for_date

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
log = logging.getLogger("ta_tune_experiment")

YAML_PATH = Path("ifa/families/ta/params/ta_v2.2.yaml")
BACKUP_DIR = Path("tmp/ta_yaml_backups")

# ─── Experiment definitions ────────────────────────────────────────────────

EXPERIMENTS = {
    "iter6_atr_tighter": {
        "hypothesis": "Tighter ATR R:R 2:1 preserved (k_stop 1.5→1.2, k_target 3.0→2.5) — faster in/out, more fills",
        "patches": [
            ("recommended_price.k_stop", 1.2),
            ("recommended_price.k_target", 2.5),
        ],
    },
    "iter7_tier_a_strict": {
        "hypothesis": "Top 5 only conviction picks — A_size 10→5, B_size 20→15",
        "patches": [
            ("ranker.tiers.a_size", 5),
            ("ranker.tiers.b_size", 15),
            # Also tighten regime sizes proportionally
            ("ranker.regime_tier_sizes.trend_continuation.a", 5),
            ("ranker.regime_tier_sizes.trend_continuation.b", 15),
            ("ranker.regime_tier_sizes.early_risk_on.a", 5),
            ("ranker.regime_tier_sizes.early_risk_on.b", 15),
            ("ranker.regime_tier_sizes.range_bound.a", 3),
            ("ranker.regime_tier_sizes.range_bound.b", 10),
        ],
    },
    "iter8_trend_smallcap": {
        "hypothesis": "Allow small-caps in trend regime — mv門 30亿→20亿 (only when current_regime is trending)",
        "patches": [
            # Note: requires fundamental_filter to support by_regime override.
            # For now, just lower global threshold and rely on regime-aware ranker.
            ("fundamental_filter.min_total_mv_yi", 20),
        ],
        "warning": "Lowers global mv threshold to 20亿 — affects ALL regimes, not just trend. To make trend-specific, need code change in context_loader.",
    },
    "iter9_q3_off": {
        "hypothesis": "Disable mild Q3 entirely — diagnostic: does combined_score weighting actually help?",
        "patches": [],  # no yaml change, code change in ranker
        "code_patch": "_disable_q3",  # special marker
        "warning": "Requires manual code edit in ranker.py to neutralize Q3.",
    },
    "iter10_high_winrate_floor": {
        "hypothesis": "Heavier weight on high-winrate setups — floor_ratio 0.4→0.55",
        "patches": [
            ("ranker.winrate.floor_ratio", 0.55),
        ],
    },
    "iter11_aggressive_q3": {
        "hypothesis": "Stronger combined-score weighting — factor [0.80, 1.20] → [0.6, 1.4]",
        "patches": [],  # code change needed
        "code_patch": "q3_factor_06_14",
        "warning": "Requires manual code edit in ranker.py for factor range.",
    },
    "iter12_ranker_fully_relaxed_concentration": {
        "hypothesis": "Concentration cap relaxed across ALL regimes (range_bound 3→4 too)",
        "patches": [
            ("concentration.tier_a_per_l2_max", 4),
            ("concentration.tier_b_per_l2_max", 8),
            ("concentration.tier_ab_per_l2_max", 9),
            ("concentration.by_regime.trend_continuation.a", 6),
            ("concentration.by_regime.early_risk_on.a", 6),
        ],
    },
}

# ─── Helpers ───────────────────────────────────────────────────────────────


def _set_nested(d: dict, path: str, value):
    parts = path.split(".")
    cur = d
    for k in parts[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[parts[-1]] = value


def _apply_patches(patches: list[tuple]) -> Path:
    """Backup current yaml, apply patches, return backup path."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"ta_v2.2_before_{stamp}.yaml"
    shutil.copy(YAML_PATH, backup)

    if not patches:
        return backup

    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    for path, value in patches:
        _set_nested(data, path, value)
    YAML_PATH.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    reload_params()
    return backup


def _restore_yaml(backup: Path):
    if backup.exists():
        shutil.copy(backup, YAML_PATH)
        reload_params()
        print(f"[restored yaml from {backup}]")


def _run_360d_pipeline(eng, label: str) -> None:
    """Re-scan + position-track + metrics + final re-scan over 360d."""
    with eng.connect() as c:
        dates = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT DISTINCT trade_date FROM smartmoney.raw_daily "
                    "WHERE trade_date BETWEEN '2024-12-15' AND '2026-04-14' "
                    "ORDER BY trade_date"
                )
            )
        ]
    print(f"[{label}] re-scan + track over {len(dates)} dates...")
    t0 = time.time()
    for i, d in enumerate(dates):
        _scan_and_persist_one_day(eng, d)
        evaluate_for_date(eng, d, horizon=15, top_watchlist_only=False)
        if (i + 1) % 60 == 0:
            print(f"  {i+1}/{len(dates)} ({time.time() - t0:.0f}s)")
    print(f"[{label}] done in {time.time() - t0:.0f}s")


def _measure_and_save(eng, label: str, hypothesis: str) -> str:
    """Run multi-window tier-perf, write report to /tmp/ta_<label>_<ts>.txt."""
    from ifa.families.ta.backtest import analyze_tier_perf

    windows = [
        ("60d", date(2026, 1, 15), date(2026, 4, 14)),
        ("90d", date(2025, 12, 15), date(2026, 4, 14)),
        ("180d", date(2025, 9, 1), date(2026, 4, 14)),
        ("360d", date(2024, 12, 15), date(2026, 4, 14)),
    ]
    lines = [
        f"=== Experiment: {label} ===",
        f"Hypothesis: {hypothesis}",
        f"Generated: {datetime.now().isoformat()}",
        "",
        f"{'Window':<6} {'Tier':<5} {'picks':>6} {'fill%':>6} "
        f"{'success%':>9} {'realized':>9} {'mkt':>9} {'vs_market':>10}",
        "-" * 80,
    ]
    for name, s, e in windows:
        with eng.connect() as c:
            r = c.execute(
                text(
                    "SELECT AVG(return_t15_pct) FROM ta.position_events_daily "
                    "WHERE generation_date BETWEEN :s AND :e AND fill_status='filled'"
                ),
                {"s": s, "e": e},
            ).first()
        mkt = float(r[0]) if r[0] is not None else 0
        for tier in ("A", "B"):
            perf = analyze_tier_perf(eng, start=s, end=e, tier=tier)
            delta = perf.avg_realized_return - mkt
            flag = "OK" if delta > 0 else "WORSE"
            lines.append(
                f"{name:<6} {tier:<5} {perf.n_positions:>6} "
                f"{perf.fill_rate*100:>5.1f}% "
                f"{perf.success_rate*100:>8.1f}% "
                f"{perf.avg_realized_return:>+8.2f}% "
                f"{mkt:>+8.2f}% "
                f"{delta:>+8.2f}pp {flag}"
            )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(f"/tmp/ta_{label}_{stamp}.txt")
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[saved to {out}]")
    return str(out)


# ─── Main ───────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True, choices=list(EXPERIMENTS.keys()))
    ap.add_argument(
        "--keep-yaml",
        action="store_true",
        help="Keep modified yaml after run (default: restore from backup)",
    )
    ap.add_argument("--skip-scan", action="store_true",
                    help="Skip re-scan and only re-measure (uses existing data)")
    args = ap.parse_args()

    exp = EXPERIMENTS[args.experiment]
    print(f"\n=== {args.experiment} ===")
    print(f"Hypothesis: {exp['hypothesis']}")
    if exp.get("warning"):
        print(f"⚠ Warning: {exp['warning']}")
    if exp.get("code_patch"):
        print(
            f"\n⚠ This experiment requires manual code edit "
            f"({exp['code_patch']}). Skipping yaml patch step."
        )
        print("Edit ranker.py manually, then re-run with --skip-scan to measure.")
        return

    print(f"Patches: {len(exp['patches'])} yaml changes")
    for path, val in exp["patches"]:
        print(f"  {path} = {val}")
    print()

    eng = get_engine()
    backup = _apply_patches(exp["patches"])
    print(f"[backup → {backup}]\n")

    try:
        if not args.skip_scan:
            _run_360d_pipeline(eng, args.experiment)
        _measure_and_save(eng, args.experiment, exp["hypothesis"])
    finally:
        if not args.keep_yaml:
            _restore_yaml(backup)


if __name__ == "__main__":
    main()
