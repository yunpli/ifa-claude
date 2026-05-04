"""TA setup-level param tuning — greedy 1-axis search using combined_score.

Distinct from `ta_param_tune.py` which tunes the regime classifier against
an oracle. This script tunes RANKER + SECTOR_FLOW + CONCENTRATION parameters
(yaml-driven, no setup-code changes needed) against the real backtest
objective: weighted T+15 / T+10 / T+5 win-rate × avg-return per
ta_v2.3.yaml.backtest_objective.weights.

Workflow:
  1. Snapshot current ta_v2.3.yaml to tmp/ta_v2.3_before_<ts>.yaml
  2. Compute baseline combined_score over [start, end] window from existing
     candidates_daily + position_events_daily (no re-scan).
  3. For each tunable axis, try ±20% / ±10% deltas, re-aggregate, keep best.
  4. If total improvement ≥ MIN_DELTA: write tuned YAML and print diff.
  5. Otherwise leave YAML untouched.

Usage:
  uv run python scripts/ta_setup_param_tune.py \\
      --start 2026-01-15 --end 2026-04-14 \\
      [--dry-run] [--min-delta 0.01]
"""
from __future__ import annotations

import argparse
import logging
import shutil
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import yaml
from sqlalchemy import text

from ifa.core.calendar import trading_days_between
from ifa.core.db import get_engine
from ifa.families.ta.params import load_params, reload_params

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

YAML_PATH = Path("ifa/families/ta/params/ta_v2.3.yaml")

# Tunable yaml-driven axes (P2 fully dynamic).
# Three groups:
#   (a) Top-level ranker/flow/concentration knobs (affect ALL setups via Tier ranking).
#   (b) Per-setup gate thresholds (affect that setup's selectivity).
#   (c) Backtest objective weights.
DEFAULT_MULTS = [0.7, 0.85, 1.0, 1.15, 1.3]
WIDE_MULTS = [0.5, 0.75, 1.0, 1.25, 1.5]

TUNABLE: list[tuple[str, type, list[float]]] = [
    # ── (a) Ranker / flow / concentration ──────────────────────────────────
    ("ranker.regime_boost", float,           WIDE_MULTS),
    ("ranker.winrate.target_pct", float,     DEFAULT_MULTS),
    ("ranker.winrate.floor_ratio", float,    DEFAULT_MULTS),
    ("ranker.diversity.top_cap_per_setup", int, [0.7, 1.0, 1.3, 1.7]),
    ("sector_flow.rank_weight", float,       DEFAULT_MULTS),
    ("sector_flow.phase_weight", float,      DEFAULT_MULTS),
    ("sector_flow.confidence_weight", float, WIDE_MULTS),
    ("sector_flow.quality_weight_min", float,DEFAULT_MULTS),
    ("concentration.tier_a_per_l2_max", int, [0.66, 1.0, 1.33, 1.66]),
    ("concentration.tier_b_per_l2_max", int, [0.66, 1.0, 1.33, 1.66]),

    # ── (b) Per-setup gate thresholds ─────────────────────────────────────
    ("setups.T2_PULLBACK_RESUME.ma20_touch_max_x", float, [0.985, 1.0, 1.015, 1.03]),
    ("setups.T3_ACCELERATION.ret_5d_min", float, DEFAULT_MULTS),
    ("setups.P1_MA20_PULLBACK.ma20_touch_max_x", float, [0.99, 1.0, 1.01, 1.02, 1.03]),
    ("setups.P1_MA20_PULLBACK.panic_volume_ratio_max", float, DEFAULT_MULTS),
    ("setups.P1_MA20_PULLBACK.rsi_max", float, [0.7, 0.85, 1.0, 1.15]),
    ("setups.P3_TIGHT_CONSOLIDATION.prior_gain_min", float, WIDE_MULTS),
    ("setups.P3_TIGHT_CONSOLIDATION.box_range_max", float, DEFAULT_MULTS),
    ("setups.R1_DOUBLE_BOTTOM.bottom_diff_max", float, DEFAULT_MULTS),
    ("setups.R1_DOUBLE_BOTTOM.bottom_to_peak_min", float, DEFAULT_MULTS),
    ("setups.R2_HS_BOTTOM.shoulder_above_head_min", float, WIDE_MULTS),
    ("setups.R2_HS_BOTTOM.shoulder_diff_max", float, DEFAULT_MULTS),
    ("setups.R3_HAMMER.body_pct_max", float, DEFAULT_MULTS),
    ("setups.R3_HAMMER.shadow_to_body_min", float, DEFAULT_MULTS),
    ("setups.R3_HAMMER.downtrend_max", float, [0.5, 0.75, 1.0, 1.25, 1.5]),
    ("setups.F1_FLAG.pole_min", float, DEFAULT_MULTS),
    ("setups.F1_FLAG.flag_range_max", float, DEFAULT_MULTS),
    ("setups.F2_TRIANGLE.contraction_max", float, DEFAULT_MULTS),
    ("setups.F3_RECTANGLE.box_range_max", float, DEFAULT_MULTS),
    ("setups.V1_VOL_PRICE_UP.ret_5d_min", float, DEFAULT_MULTS),
    ("setups.V1_VOL_PRICE_UP.vol_ratio_min", float, DEFAULT_MULTS),
    ("setups.V2_QUIET_COIL.vol_ratio_max", float, DEFAULT_MULTS),
    ("setups.V2_QUIET_COIL.box_range_max", float, DEFAULT_MULTS),
    ("setups.S1_SECTOR_RESONANCE.l1_pct_min", float, DEFAULT_MULTS),
    ("setups.S1_SECTOR_RESONANCE.l2_pct_min", float, DEFAULT_MULTS),
    ("setups.S1_SECTOR_RESONANCE.stock_ret_min_pct", float, DEFAULT_MULTS),
    ("setups.S2_LEADER_FOLLOWTHROUGH.l2_pct_min", float, DEFAULT_MULTS),
    ("setups.S2_LEADER_FOLLOWTHROUGH.outperform_l2_min_pp", float, DEFAULT_MULTS),
    ("setups.S3_LAGGARD_CATCHUP.l2_pct_min", float, DEFAULT_MULTS),
    ("setups.S3_LAGGARD_CATCHUP.stock_20d_max_pct", float, DEFAULT_MULTS),
    ("setups.S3_LAGGARD_CATCHUP.today_ret_min_pct", float, DEFAULT_MULTS),
    ("setups.C1_CHIP_CONCENTRATED.concentration_pct_max", float, DEFAULT_MULTS),
    ("setups.C2_CHIP_LOOSE.concentration_pct_min", float, DEFAULT_MULTS),
    ("setups.C2_CHIP_LOOSE.winner_rate_min", float, DEFAULT_MULTS),
    ("setups.C2_CHIP_LOOSE.ret_20d_min", float, DEFAULT_MULTS),
    ("setups.O1_INST_PERSISTENT_BUY.flow_5d_pct_min", float, WIDE_MULTS),
    ("setups.O1_INST_PERSISTENT_BUY.pct_chg_min", float, [0.5, 0.75, 1.0, 1.25]),
    ("setups.O2_LHB_INST_BUY.pct_float_min", float, WIDE_MULTS),
    ("setups.O2_LHB_INST_BUY.inst_days_min", int, [1, 2, 3]),
    ("setups.O3_LIMIT_SEAL_STRENGTH.seal_ratio_min", float, WIDE_MULTS),
    ("setups.D1_DOUBLE_TOP.peak_diff_max", float, DEFAULT_MULTS),
    ("setups.D1_DOUBLE_TOP.trough_drop_min", float, DEFAULT_MULTS),
    ("setups.D1_DOUBLE_TOP.ret_20d_min_pct", float, DEFAULT_MULTS),
    ("setups.D2_HS_TOP.shoulder_diff_max", float, DEFAULT_MULTS),
    ("setups.D2_HS_TOP.ret_30d_min_pct", float, DEFAULT_MULTS),
    ("setups.D3_SHOOTING_STAR.upper_ratio_min", float, DEFAULT_MULTS),
    ("setups.D3_SHOOTING_STAR.body_ratio_max", float, DEFAULT_MULTS),
    ("setups.D3_SHOOTING_STAR.ret_20d_min_pct", float, DEFAULT_MULTS),
    ("setups.Z1_ZSCORE_EXTREME.z_abs_min", float, [0.75, 0.9, 1.0, 1.1, 1.25]),
    ("setups.Z2_OVERSOLD_REBOUND.rsi_max", float, DEFAULT_MULTS),
    ("setups.Z2_OVERSOLD_REBOUND.ret_5d_max_pct", float, [0.7, 0.85, 1.0, 1.15, 1.3]),
    ("setups.E1_EVENT_CATALYST.days_to_disclosure_max", int, [0.5, 1.0, 1.5, 2.0]),

    # ── (c) Backtest objective ────────────────────────────────────────────
    ("backtest_objective.weights.t15", float, [0.85, 1.0, 1.15]),
    ("backtest_objective.weights.t5",  float, [0.5, 1.0, 1.5]),
]


def _get(d: dict, path: str):
    cur = d
    for k in path.split("."):
        cur = cur[k]
    return cur


def _set(d: dict, path: str, v):
    parts = path.split(".")
    cur = d
    for k in parts[:-1]:
        cur = cur[k]
    cur[parts[-1]] = v


def _candidates(value, t: type, mults: list[float]):
    out = []
    for m in mults:
        v = int(round(value * m)) if t is int else round(value * m, 4)
        if v <= 0:
            continue
        if v not in out:
            out.append(v)
    return out


def aggregate_combined(engine, start: date, end: date, weights: dict) -> tuple[float, int]:
    """Aggregate combined_score over window, weighting setups by n.

    Returns (weighted_combined, n_total).
    """
    w_t15 = float(weights.get("t15", 0.7))
    w_t5 = float(weights.get("t5", 0.2))
    w_t10 = float(weights.get("t10", 0.1))
    sql = text("""
        WITH per_setup AS (
            SELECT c.setup_name,
                   COUNT(*) AS n,
                   AVG(p.return_t15_pct) AS avg_t15,
                   AVG(p.return_t5_pct)  AS avg_t5,
                   AVG(p.return_t10_pct) AS avg_t10,
                   100.0 * COUNT(*) FILTER (WHERE p.return_t15_pct >= 5.0) / NULLIF(COUNT(*),0) AS wr_t15,
                   100.0 * COUNT(*) FILTER (WHERE p.return_t5_pct  >= 3.0) / NULLIF(COUNT(*),0) AS wr_t5,
                   100.0 * COUNT(*) FILTER (WHERE p.return_t10_pct >= 4.0) / NULLIF(COUNT(*),0) AS wr_t10
            FROM ta.candidates_daily c
            JOIN ta.position_events_daily p ON p.candidate_id = c.candidate_id
            WHERE c.trade_date >= :s AND c.trade_date <= :e
              AND p.fill_status = 'filled'
            GROUP BY c.setup_name
        )
        SELECT setup_name, n,
               COALESCE(wr_t15,0)/100.0 * COALESCE(avg_t15,0) AS comp_t15,
               COALESCE(wr_t5,0)/100.0  * COALESCE(avg_t5,0)  AS comp_t5,
               COALESCE(wr_t10,0)/100.0 * COALESCE(avg_t10,0) AS comp_t10
        FROM per_setup
    """)
    total_n = 0
    weighted = 0.0
    with engine.connect() as conn:
        for r in conn.execute(sql, {"s": start, "e": end}):
            n = int(r[1])
            comb = w_t15 * float(r[2]) + w_t5 * float(r[3]) + w_t10 * float(r[4])
            total_n += n
            weighted += comb * n
    if total_n == 0:
        return 0.0, 0
    return weighted / total_n, total_n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-delta", type=float, default=0.01,
                    help="Min weighted-combined Δ required to auto-apply")
    args = ap.parse_args()

    engine = get_engine()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    days = trading_days_between(engine, start, end)
    log.info("tuning over %d trade days [%s..%s]", len(days), start, end)

    params = load_params()
    backup = deepcopy(params)

    base_combined, base_n = aggregate_combined(
        engine, start, end, params["backtest_objective"]["weights"])
    log.info("baseline: combined=%.4f over %d positions", base_combined, base_n)

    cur_combined = base_combined
    proposals = []
    for path, ptype, mults in TUNABLE:
        try:
            cur_value = _get(params, path)
        except (KeyError, TypeError):
            log.warning("path %s missing — skip", path)
            continue
        best_v = cur_value
        best_combined = cur_combined
        for v in _candidates(cur_value, ptype, mults):
            _set(params, path, v)
            new_combined, _ = aggregate_combined(
                engine, start, end, params["backtest_objective"]["weights"])
            if new_combined > best_combined + 1e-6:
                best_combined = new_combined
                best_v = v
        if best_v != cur_value:
            log.info("  %s: %s → %s  (Δcombined=%+.4f)",
                     path, cur_value, best_v, best_combined - cur_combined)
            proposals.append((path, cur_value, best_v, best_combined - cur_combined))
            _set(params, path, best_v)
            cur_combined = best_combined
        else:
            _set(params, path, cur_value)   # restore

    total_delta = cur_combined - base_combined
    log.info("=== summary: %d proposals, total Δcombined=%+.4f (baseline=%.4f → tuned=%.4f) ===",
             len(proposals), total_delta, base_combined, cur_combined)

    if total_delta < args.min_delta:
        log.info("Δ=%.4f below threshold %.4f — leaving YAML untouched",
                 total_delta, args.min_delta)
        # Restore params dict in-memory
        for path, old, _, _ in proposals:
            _set(params, path, old)
        return

    if args.dry_run:
        log.info("dry-run mode: %d changes proposed but not written", len(proposals))
        return

    # Backup + write
    Path("tmp").mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = Path(f"tmp/ta_v2.3_before_{stamp}.yaml")
    shutil.copy(YAML_PATH, backup_path)
    log.info("backed up YAML to %s", backup_path)

    YAML_PATH.write_text(yaml.safe_dump(params, sort_keys=False, allow_unicode=True),
                         encoding="utf-8")
    reload_params()
    log.info("YAML updated. Proposals applied:")
    for path, old, new, delta in proposals:
        log.info("  %s: %s → %s (Δ=%+.4f)", path, old, new, delta)


if __name__ == "__main__":
    main()
