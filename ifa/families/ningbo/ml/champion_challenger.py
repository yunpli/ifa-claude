"""Champion-Challenger production ML governance — Phase 3.D.

Two production "slots" run independently:
    - aggressive    optimized for Top5_Mean (high return, accept volatility)
    - conservative  optimized for Sharpe ratio (stable, low drawdown)

Each weekly retrain:
    1. Train all candidate models (LR, RF, XGB, LGBM, Cat, rankers, ensemble)
    2. Evaluate on rolling OOS window (last N months)
    3. For each slot, pick winner by slot-specific metric
    4. Apply promotion rules (lift threshold + statistical significance + risk floor)
    5. Update active model in registry; log decision

Promotion rules per slot:

    AGGRESSIVE (maximize return):
      - new.T5_Mean >= active.T5_Mean + 0.5pp
      - new.T5_Med  >= -3.0%                 (risk floor)
      - bootstrap p-value < 0.10 vs active
      - active has been live >= 14 days       (cool-down)

    CONSERVATIVE (maximize stability):
      - new.Sharpe >= active.Sharpe + 0.05
      - new.T5_Mean >= +1.0%                  (return floor)
      - new.MaxDD  >= -65%                    (DD floor)
      - bootstrap p-value < 0.10 vs active
      - active has been live >= 14 days

    HEURISTIC (baseline, never promotes/demotes):
      - Always 'confidence_score' as the rank (slot is fixed)

Emergency rollback:
    If active.recent_30d_T5_Mean < 0.0% → emit warning
    If active.recent_60d_T5_Mean < 0.0% → force rollback to heuristic
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import Engine, text


# ── Slot-specific config ─────────────────────────────────────────────────────

SLOT_AGGRESSIVE   = "aggressive"
SLOT_CONSERVATIVE = "conservative"
SLOT_HEURISTIC    = "heuristic"

PROMOTION_THRESHOLDS = {
    SLOT_AGGRESSIVE: {
        "primary_metric":   "oos_top5_avg_return",
        "min_lift":         0.005,    # +0.5pp
        "risk_floor":       {"oos_top5_med_return": -0.030},  # T5_Med >= -3%
        "p_value_max":      0.10,
        "min_active_days":  14,
    },
    SLOT_CONSERVATIVE: {
        "primary_metric":   "oos_top5_sharpe",
        "min_lift":         0.05,
        "risk_floor":       {
            "oos_top5_avg_return": 0.010,    # T5_Mean >= +1%
            "oos_max_drawdown":    -0.65,    # MaxDD >= -65%
        },
        "p_value_max":      0.10,
        "min_active_days":  14,
    },
}


def _models_root() -> Path:
    from ifa.config import get_settings
    s = get_settings()
    root = Path(s.output_root).parent / "models" / "ningbo"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── Registry CRUD ────────────────────────────────────────────────────────────

def get_active_for_slot(engine: Engine, slot: str) -> dict | None:
    """Return active model row for a slot, or None."""
    sql = text("""
        SELECT model_version, model_name, objective, feature_set_id, feature_columns,
               train_range_start, train_range_end, oos_range_start, oos_range_end,
               n_train, n_oos, metrics, artifact_path, activated_at
        FROM ningbo.model_registry
        WHERE slot = :slot AND is_active = TRUE
        LIMIT 1
    """)
    with engine.connect() as c:
        row = c.execute(sql, {"slot": slot}).mappings().fetchone()
    return dict(row) if row else None


def list_versions_for_slot(engine: Engine, slot: str, limit: int = 20) -> list[dict]:
    sql = text("""
        SELECT model_version, model_name, objective, is_active,
               activated_at, deactivated_at, created_at,
               metrics->>'oos_top5_avg_return' AS top5_mean,
               metrics->>'oos_top5_sharpe' AS sharpe,
               metrics->>'oos_top5_med_return' AS top5_med
        FROM ningbo.model_registry
        WHERE slot = :slot
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    with engine.connect() as c:
        rows = c.execute(sql, {"slot": slot, "limit": limit}).mappings().fetchall()
    return [dict(r) for r in rows]


def insert_model_version(
    engine: Engine,
    *,
    model_version: str,
    slot: str,
    model_name: str,
    objective: str,
    feature_set_id: str,
    feature_columns: list[str],
    train_range: tuple[dt.date, dt.date],
    oos_range: tuple[dt.date, dt.date],
    n_train: int,
    n_oos: int,
    metrics: dict,
    artifact_path: str,
) -> None:
    sql = text("""
        INSERT INTO ningbo.model_registry
            (model_version, slot, model_name, objective, feature_set_id, feature_columns,
             train_range_start, train_range_end, oos_range_start, oos_range_end,
             n_train, n_oos, metrics, artifact_path)
        VALUES
            (:v, :slot, :name, :obj, :fsid, :fcols,
             :trs, :tre, :oos, :ooe, :nt, :no, :metrics, :art)
        ON CONFLICT (model_version, slot) DO UPDATE SET
            metrics = EXCLUDED.metrics,
            artifact_path = EXCLUDED.artifact_path,
            n_train = EXCLUDED.n_train,
            n_oos = EXCLUDED.n_oos
    """)
    with engine.begin() as c:
        c.execute(sql, {
            "v": model_version, "slot": slot, "name": model_name, "obj": objective,
            "fsid": feature_set_id, "fcols": json.dumps(feature_columns),
            "trs": train_range[0], "tre": train_range[1],
            "oos": oos_range[0], "ooe": oos_range[1],
            "nt": n_train, "no": n_oos,
            "metrics": json.dumps(metrics, default=str),
            "art": artifact_path,
        })


def activate_version(
    engine: Engine, slot: str, version: str,
    *, reason: str = "promoted", event_type: str = "promoted",
    decision_data: dict | None = None,
) -> str | None:
    """Mark `version` as active for `slot`. Deactivates current active.

    Returns the previous active version (if any) for logging.
    """
    with engine.begin() as c:
        prev = c.execute(text("""
            SELECT model_version FROM ningbo.model_registry
            WHERE slot = :slot AND is_active = TRUE
        """), {"slot": slot}).scalar()

        if prev:
            c.execute(text("""
                UPDATE ningbo.model_registry
                SET is_active = FALSE, deactivated_at = NOW()
                WHERE slot = :slot AND is_active = TRUE
            """), {"slot": slot})

        c.execute(text("""
            UPDATE ningbo.model_registry
            SET is_active = TRUE, activated_at = NOW(), deactivated_at = NULL
            WHERE slot = :slot AND model_version = :v
        """), {"slot": slot, "v": version})

        c.execute(text("""
            INSERT INTO ningbo.promotion_log
                (slot, new_version, old_version, event_type, reason, decision_data)
            VALUES
                (:slot, :new, :old, :evt, :reason, :data)
        """), {
            "slot": slot, "new": version, "old": prev,
            "evt": event_type, "reason": reason,
            "data": json.dumps(decision_data or {}, default=str),
        })

    return prev


def log_no_change(
    engine: Engine, slot: str, candidate_version: str | None, reason: str,
    decision_data: dict | None = None,
) -> None:
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO ningbo.promotion_log
                (slot, new_version, old_version, event_type, reason, decision_data)
            VALUES
                (:slot, :cand, NULL, 'no_change', :reason, :data)
        """), {
            "slot": slot, "cand": candidate_version, "reason": reason,
            "data": json.dumps(decision_data or {}, default=str),
        })


# ── Promotion rule evaluation ────────────────────────────────────────────────

@dataclass
class PromotionDecision:
    slot: str
    candidate_name: str
    candidate_version: str
    candidate_metrics: dict
    active_name: str | None = None
    active_metrics: dict | None = None
    will_promote: bool = False
    rule_results: dict = field(default_factory=dict)
    reason: str = ""


def _meets_risk_floor(metrics: dict, floor: dict) -> tuple[bool, str]:
    failures = []
    for key, threshold in floor.items():
        v = metrics.get(key)
        if v is None:
            failures.append(f"{key}=NA")
            continue
        if v < threshold:
            failures.append(f"{key}={v:.4f}<{threshold:.4f}")
    if failures:
        return False, f"risk_floor failed: {'; '.join(failures)}"
    return True, "risk_floor OK"


def evaluate_promotion(
    *, slot: str,
    candidate_name: str, candidate_version: str, candidate_metrics: dict,
    active: dict | None,
    bootstrap_p_value: float = float("nan"),
) -> PromotionDecision:
    """Apply slot-specific rules to decide whether candidate replaces active."""
    cfg = PROMOTION_THRESHOLDS[slot]
    primary = cfg["primary_metric"]
    decision = PromotionDecision(
        slot=slot,
        candidate_name=candidate_name, candidate_version=candidate_version,
        candidate_metrics=candidate_metrics,
        active_name=active["model_name"] if active else None,
        active_metrics=active["metrics"] if active else None,
    )

    # 1. Risk floor
    floor_ok, floor_msg = _meets_risk_floor(candidate_metrics, cfg["risk_floor"])
    decision.rule_results["risk_floor"] = floor_msg
    if not floor_ok:
        decision.reason = f"REJECT: {floor_msg}"
        return decision

    # 2. If no active yet, candidate auto-promotes (cold start)
    if active is None:
        decision.will_promote = True
        decision.reason = "PROMOTE: cold start (no active model yet)"
        decision.rule_results["cold_start"] = True
        return decision

    # 3. Cool-down check
    activated_at = active.get("activated_at")
    if activated_at is not None:
        days_active = (dt.datetime.now(activated_at.tzinfo) - activated_at).days
        decision.rule_results["days_active"] = days_active
        if days_active < cfg["min_active_days"]:
            decision.reason = (
                f"NO CHANGE: active still in cool-down ({days_active} < {cfg['min_active_days']} days)"
            )
            return decision

    # 4. Lift threshold
    active_metrics = active["metrics"] or {}
    if isinstance(active_metrics, str):
        active_metrics = json.loads(active_metrics)
    cand_val = candidate_metrics.get(primary, float("-inf"))
    act_val  = active_metrics.get(primary, float("-inf"))
    lift = cand_val - act_val
    decision.rule_results["primary_metric"] = primary
    decision.rule_results["candidate_value"] = cand_val
    decision.rule_results["active_value"]    = act_val
    decision.rule_results["lift"]            = lift
    decision.rule_results["min_lift_required"] = cfg["min_lift"]
    if lift < cfg["min_lift"]:
        decision.reason = (
            f"NO CHANGE: lift {lift:.4f} < min_lift {cfg['min_lift']:.4f} on {primary}"
        )
        return decision

    # 5. Statistical significance
    decision.rule_results["bootstrap_p_value"] = bootstrap_p_value
    if not (bootstrap_p_value < cfg["p_value_max"]):
        decision.reason = (
            f"NO CHANGE: bootstrap p={bootstrap_p_value:.3f} not < {cfg['p_value_max']}"
        )
        return decision

    decision.will_promote = True
    decision.reason = (
        f"PROMOTE: lift +{lift:.4f} on {primary}, p={bootstrap_p_value:.3f}, "
        f"risk_floor OK, cool-down OK"
    )
    return decision


# ── Persist trained model artifact ──────────────────────────────────────────

def save_model_artifact(
    *, slot: str, model_version: str, model_name: str, model_object: Any,
) -> str:
    """Save model to disk; return path."""
    version_dir = _models_root() / model_version
    version_dir.mkdir(parents=True, exist_ok=True)
    path = version_dir / f"{slot}_{model_name}.joblib"
    joblib.dump(model_object, path)
    return str(path)


def load_model_artifact(artifact_path: str) -> Any:
    return joblib.load(artifact_path)


# ── Recent rolling performance (for emergency rollback check) ───────────────

def recent_rolling_performance(
    engine: Engine, slot: str, days: int = 30,
) -> dict | None:
    """Compute recent T5_Mean for the active model based on its actual recommendations.

    Reads from ningbo.recommendations_daily + recommendation_outcomes filtered
    by scoring_mode = 'ml_aggressive' or 'ml_conservative'.
    """
    scoring_mode_map = {
        SLOT_AGGRESSIVE:   "ml_aggressive",
        SLOT_CONSERVATIVE: "ml_conservative",
        SLOT_HEURISTIC:    "heuristic",
    }
    scoring_mode = scoring_mode_map.get(slot)
    if scoring_mode is None:
        return None

    # Last `days` calendar days of completed picks (drop in_progress)
    cutoff = dt.date.today() - dt.timedelta(days=days)
    sql = text("""
        SELECT
            COUNT(*)                              AS n_picks,
            AVG(o.final_cum_return)               AS mean_ret,
            COUNT(DISTINCT r.rec_date)            AS n_days,
            AVG(CASE WHEN o.outcome_status='take_profit' THEN 1.0 ELSE 0.0 END) AS win_rate
        FROM ningbo.recommendations_daily r
        JOIN ningbo.recommendation_outcomes o USING (rec_date, ts_code, strategy, scoring_mode)
        WHERE r.scoring_mode = :sm
          AND r.rec_date >= :cutoff
          AND o.outcome_status != 'in_progress'
    """)
    with engine.connect() as c:
        row = c.execute(sql, {"sm": scoring_mode, "cutoff": cutoff}).mappings().fetchone()
    return dict(row) if row else None
