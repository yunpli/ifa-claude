"""TA param tuning — greedy 1-axis search against the oracle.

Auto-applies any change that improves oracle agreement by ≥ MIN_DELTA_PP
percentage points. Backs up the prior YAML to tmp/ first so revert is easy.

Workflow:
  1. Snapshots current `ta_v2.3.yaml` to `tmp/ta_v2.3_before_<ts>.yaml`
  2. Greedy 1-axis search across tunable thresholds (in-memory)
  3. If total improvement ≥ MIN_DELTA_PP: writes tuned values back to
     `ta_v2.3.yaml`, prints the diff
  4. If improvement < MIN_DELTA_PP: leaves YAML untouched, says so

Run weekly via cron; the only side effect is the YAML file (which is
git-tracked, so changes show up in `git diff`).

    uv run python scripts/ta_param_tune.py [--start 2025-09-01] [--end 2026-04-30]
                                            [--dry-run]  [--min-delta 1.0]
"""
from __future__ import annotations

import argparse
import logging
import shutil
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import yaml

from ifa.core.calendar import trading_days_between
from ifa.core.db import get_engine
from ifa.families.ta.params import load_params, reload_params
from ifa.families.ta.params.loader import _PARAMS_PATH
from ifa.families.ta.regime.classifier import classify_regime
from ifa.families.ta.regime.loader import load_regime_context
from scripts.ta_regime_oracle_check import oracle_regime

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Tunable thresholds (yaml dotted path, type for rounding)
TUNABLE: list[tuple[str, type]] = [
    ("regime.vetos.trend_continuation.udr_min", float),
    ("regime.vetos.trend_continuation.n_limit_down_max", int),
    ("regime.vetos.trend_continuation.n_down_max", int),
    ("regime.vetos.trend_continuation.defer_to_early_lu_min", int),
    ("regime.vetos.trend_continuation.defer_to_early_up_min", int),
    ("regime.vetos.range_bound.n_up_max", int),
    ("regime.vetos.range_bound.n_limit_up_max", int),
    ("regime.vetos.range_bound.n_down_max", int),
    ("regime.vetos.range_bound.n_limit_down_max", int),
    ("regime.vetos.range_bound.cooldown_path_udr_max", float),
    ("regime.vetos.range_bound.cooldown_path_n_down_min", int),
    ("regime.thresholds.early_risk_on.absolute_lu_min", int),
    ("regime.thresholds.early_risk_on.absolute_up_min", int),
    ("regime.thresholds.early_risk_on.udr_strong_min", float),
    ("regime.thresholds.cooldown.n_limit_down_strong", int),
    ("regime.thresholds.cooldown.udr_strong", float),
    ("regime.thresholds.cooldown.udr_med", float),
    ("regime.thresholds.cooldown.n_down_min", int),
    ("regime.thresholds.distribution_risk.n_limit_down_strong", int),
    ("regime.thresholds.distribution_risk.n_down_strong", int),
    ("regime.thresholds.distribution_risk.ld_vs_lu_ratio", float),
    ("regime.thresholds.range_bound.vol_pct_max", float),
]


def _get(d: dict, path: str):
    cur = d
    for k in path.split("."):
        cur = cur[k]
    return cur


def _set(d: dict, path: str, value):
    parts = path.split(".")
    cur = d
    for k in parts[:-1]:
        cur = cur[k]
    cur[parts[-1]] = value


def evaluate_agreement(engine, days: list[date], contexts_cache: dict) -> tuple[int, int]:
    """Returns (matches, judged) using the in-memory cached params."""
    matches = 0
    total = 0
    for d in days:
        ctx = contexts_cache[d]
        if ctx.n_up is None:
            continue
        oracle = oracle_regime(ctx)
        if oracle is None:
            continue
        total += 1
        if classify_regime(ctx).regime == oracle:
            matches += 1
    return matches, total


def _candidates(value, t: type) -> list:
    deltas = [0.8, 0.9, 1.0, 1.1, 1.2]
    out = []
    for d in deltas:
        v = int(round(value * d)) if t is int else round(value * d, 4)
        if v not in out:
            out.append(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-09-01")
    ap.add_argument("--end", default="2026-04-30")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run search but do NOT write YAML changes")
    ap.add_argument("--min-delta", type=float, default=1.0,
                    help="Min agreement Δpp required to auto-apply")
    args = ap.parse_args()

    engine = get_engine()
    days = trading_days_between(engine, date.fromisoformat(args.start),
                                date.fromisoformat(args.end))
    log.info("tuning over %d trade days · auto-apply threshold = +%.1fpp",
             len(days), args.min_delta)

    # Pre-load all RegimeContexts so search is in-memory only.
    log.info("preloading contexts...")
    contexts = {d: load_regime_context(engine, d) for d in days}
    log.info("contexts ready: %d days", len(contexts))

    params = load_params()        # mutating this dict mutates the cached object
    backup_dict = deepcopy(params)

    base_match, base_total = evaluate_agreement(engine, days, contexts)
    base_pct = base_match / max(base_total, 1) * 100
    log.info("baseline agreement: %d/%d = %.2f%%", base_match, base_total, base_pct)

    proposals: list[dict] = []
    cur_match = base_match

    for path, ptype in TUNABLE:
        cur_value = _get(params, path)
        best_v = cur_value
        best_match = cur_match
        for v in _candidates(cur_value, ptype):
            _set(params, path, v)
            m, _ = evaluate_agreement(engine, days, contexts)
            if m > best_match:
                best_match = m
                best_v = v
        if best_v != cur_value:
            proposals.append({"path": path, "from": cur_value,
                              "to": best_v, "delta": best_match - cur_match})
            _set(params, path, best_v)
            cur_match = best_match
        else:
            _set(params, path, cur_value)

    final_pct = cur_match / max(base_total, 1) * 100
    delta_pp = final_pct - base_pct
    log.info("=" * 60)
    log.info("baseline %.2f%% → tuned %.2f%%  (Δ +%.2fpp, %d params changed)",
             base_pct, final_pct, delta_pp, len(proposals))

    if proposals:
        log.info("\nProposed changes:")
        for p in proposals:
            log.info(f"  {p['path']}: {p['from']} → {p['to']}  (+{p['delta']} matches)")

    # Write report regardless
    tmp_dir = Path("tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    backup_path = tmp_dir / f"ta_v2.3_before_{stamp}.yaml"
    backup_path.write_text(yaml.safe_dump(backup_dict), encoding="utf-8")

    if args.dry_run:
        log.info("[dry-run] not writing YAML; backup at %s", backup_path)
        # Restore in-memory cache to baseline
        for k in list(params.keys()):
            params[k] = backup_dict[k]
        reload_params()
        return 0

    if delta_pp < args.min_delta:
        log.info("Improvement %.2fpp < threshold %.1fpp — not applying. "
                 "Pre-search backup at %s", delta_pp, args.min_delta, backup_path)
        # Restore baseline
        for k in list(params.keys()):
            params[k] = backup_dict[k]
        reload_params()
        return 0

    # Apply: write the mutated params to the YAML file
    _PARAMS_PATH.write_text(yaml.safe_dump(params), encoding="utf-8")
    reload_params()
    log.info("✓ APPLIED to %s", _PARAMS_PATH)
    log.info("  pre-tune backup: %s", backup_path)
    log.info("  revert: cp %s %s", backup_path, _PARAMS_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
