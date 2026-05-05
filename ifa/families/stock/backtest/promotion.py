"""Global preset promotion helpers.

Global tuning artifacts are experiments until reviewed. This module emits a
reviewable YAML patch and can apply it only when explicitly requested.

Phase 4: auto-promotion gates. `evaluate_promotion_gates()` runs G1/G2/G6/G7
checks against baseline metrics (rank IC, drift, per-horizon non-degradation).
When all gates pass, `auto_promote_if_passing()` writes a YAML variant and
returns the path; on failure, writes a reject report and returns None.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from ifa.families.stock.params.overlay import apply_param_overlay

from .objectives import continuous_overlay_bounds
from .tuning_artifact import TuningArtifact, read_tuning_artifact

PROMOTION_ROOT = Path("/Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion")
ALLOWED_PROMOTION_PREFIXES = (
    "aggregate.",
    "smooth_scoring.",
    "cluster_weights.",
    "signal_weights.",
    "risk.",
    "decision_layer.",
    "ta_family_weights.",
)


@dataclass(frozen=True)
class PromotionPatch:
    artifact_path: str
    base_yaml: str
    generated_at: str
    objective_version: str
    objective_score: float
    candidate_count: int
    changes: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_promotion_patch(artifact_path: Path, base_yaml: Path) -> PromotionPatch:
    artifact = read_tuning_artifact(artifact_path)
    if artifact.kind != "global_preset":
        raise ValueError(f"Only global_preset artifacts can be promoted, got {artifact.kind!r}")
    base_params = _read_yaml(base_yaml)
    bounds = continuous_overlay_bounds()
    changes: list[dict[str, Any]] = []
    for key, new_value in sorted(artifact.overlay.items()):
        if not _is_promotable_key(key, bounds):
            continue
        old_value = _get_dotted(base_params, key)
        if old_value == new_value:
            continue
        changes.append({
            "parameter": key,
            "old_value": old_value,
            "new_value": new_value,
            "delta": _delta(old_value, new_value),
            "source_artifact": str(artifact_path),
            "objective_version": artifact.objective_version,
            "objective_score": artifact.objective_score,
            "candidate_count": artifact.candidate_count,
            "recommended_for_promotion": _within_bounds(key, new_value, bounds),
        })
    return PromotionPatch(
        artifact_path=str(artifact_path),
        base_yaml=str(base_yaml),
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        objective_version=artifact.objective_version,
        objective_score=artifact.objective_score,
        candidate_count=artifact.candidate_count,
        changes=changes,
    )


def emit_promotion_patch(patch: PromotionPatch, *, output_dir: Path | None = None) -> tuple[Path, Path]:
    out_dir = output_dir or (PROMOTION_ROOT / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = out_dir / "yaml_patch_candidate.yaml"
    md_path = out_dir / "yaml_patch_candidate.md"
    yaml_path.write_text(yaml.safe_dump(patch.to_dict(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    md_path.write_text(_patch_markdown(patch), encoding="utf-8")
    return yaml_path, md_path


def apply_promotion_patch(
    artifact_path: Path,
    base_yaml: Path,
    *,
    backup: bool = True,
    variant_output: Path | None = None,
) -> tuple[Path, Path | None]:
    """Apply a reviewed global patch to YAML, or write a variant YAML.

    This function is intentionally explicit; callers must choose an apply mode.
    Single-stock overlays must never be passed here because the artifact kind is
    checked in `build_promotion_patch`.
    """
    patch = build_promotion_patch(artifact_path, base_yaml)
    params = _read_yaml(base_yaml)
    overlay = {change["parameter"]: change["new_value"] for change in patch.changes if change.get("recommended_for_promotion")}
    promoted = apply_param_overlay(params, overlay)
    backup_path: Path | None = None
    target = variant_output or base_yaml
    if variant_output is None and backup:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = base_yaml.with_suffix(base_yaml.suffix + f".bak_{stamp}")
        shutil.copy2(base_yaml, backup_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(promoted, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target, backup_path


def _patch_markdown(patch: PromotionPatch) -> str:
    lines = [
        "# Stock Edge Global Preset YAML Patch Candidate",
        "",
        f"- Source artifact: `{patch.artifact_path}`",
        f"- Base YAML: `{patch.base_yaml}`",
        f"- Objective version: `{patch.objective_version}`",
        f"- Objective score: `{patch.objective_score}`",
        f"- Candidate count: `{patch.candidate_count}`",
        "",
        "| Parameter | Old | New | Delta | Promote |",
        "|---|---:|---:|---:|---|",
    ]
    for change in patch.changes:
        lines.append(
            f"| `{change['parameter']}` | `{change['old_value']}` | `{change['new_value']}` | "
            f"`{change['delta']}` | {change['recommended_for_promotion']} |"
        )
    lines.extend([
        "",
        "Rollback: if applied in-place with backup, restore the generated `.bak_*` file over the base YAML and rerun tests.",
    ])
    return "\n".join(lines) + "\n"


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML must be a mapping: {path}")
    return data


def _is_promotable_key(key: str, bounds: Mapping[str, tuple[float, float]]) -> bool:
    if key not in bounds:
        return False
    return any(key.startswith(prefix) for prefix in ALLOWED_PROMOTION_PREFIXES)


def _within_bounds(key: str, value: Any, bounds: Mapping[str, tuple[float, float]]) -> bool:
    try:
        low, high = bounds[key]
        return low <= float(value) <= high
    except Exception:
        return False


def _get_dotted(params: Mapping[str, Any], dotted_key: str) -> Any:
    root_prefixes = ("risk.", "t0.", "model.", "runtime.", "data.", "intraday.", "cache.", "report.", "tuning.", "decision_layer.", "ta_family_weights.", "position_sizing.")
    current: Any = params if dotted_key.startswith(root_prefixes) else params.get("strategy_matrix", {})
    for part in dotted_key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _delta(old: Any, new: Any) -> Any:
    try:
        return round(float(new) - float(old), 6)
    except Exception:
        return None


def patch_to_json(patch: PromotionPatch) -> str:
    return json.dumps(patch.to_dict(), ensure_ascii=False, default=str, indent=2)


# ──────────────────────────────────────────────────────────────────────────
# Phase 4: Auto-promotion gates
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    name: str
    passed: bool
    value: float
    threshold: float
    detail: str = ""
    per_horizon: dict[str, bool] = field(default_factory=dict)
    """Per-horizon pass/fail (used by G9 K-fold consistency, consumed by T1.4 horizon-selective promotion)."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromotionDecision:
    accepted: bool
    gates: list[GateResult]
    summary: str
    candidate_score: float
    baseline_score: float
    rank_ic_summary: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "summary": self.summary,
            "candidate_score": self.candidate_score,
            "baseline_score": self.baseline_score,
            "rank_ic_summary": self.rank_ic_summary,
            "gates": [g.to_dict() for g in self.gates],
        }

    @property
    def gates_passed(self) -> int:
        return sum(1 for g in self.gates if g.passed)


def evaluate_promotion_gates(
    candidate_metrics: Mapping[str, Any],
    baseline_metrics: Mapping[str, Any],
    candidate_overlay: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    kfold_results: list[dict[str, Any]] | None = None,
) -> PromotionDecision:
    """Run G1/G2/G6/G7 (and G9 if K-fold results provided) against candidate vs baseline.

    G3 (Sharpe), G4 (regime-bucketed), G5 (bootstrap CI), G8 (multi-iter convergence)
    are deferred — they need either richer panel infrastructure (regime split, more
    samples) or are tracked elsewhere (G8 is in artifact.metrics.search_history).

    G9 (K-fold consistency) activates when `kfold_results` is provided. Each fold dict
    must contain `val_baseline` and `val_tuned` metric blocks (same shape as
    candidate_metrics). G9 checks that ≥ min_positive_folds folds show positive lift
    on each horizon. Per-horizon pass/fail is exposed in `g9.per_horizon` so T1.4
    horizon-selective promotion can read it.

    Default thresholds chosen conservatively per the production-grade spec:
      G1: composite ≥ baseline × 1.005 OR rank-IC absolute lift ≥ +0.005 on any horizon
      G2: per-horizon rank IC non-degraded by more than 0.02 absolute
      G6: ‖overlay - 1.0‖₂/√K < 0.55 AND max single-param drift < 1.5
      G7: per-horizon rank IC ≥ baseline rank IC - 0.01 absolute (works for negative baselines)
      G9: each horizon ≥ ceil(0.75 × n_folds) folds with val rank IC lift > 0
    """
    cfg = dict(config or {})
    cs = float(candidate_metrics.get("composite_objective", {}).get("score", 0.0))
    bs = float(baseline_metrics.get("composite_objective", {}).get("score", 0.0))

    cand_per_horizon = {h: candidate_metrics.get(f"objective_{h}d", {}) for h in (5, 10, 20)}
    base_per_horizon = {h: baseline_metrics.get(f"objective_{h}d", {}) for h in (5, 10, 20)}

    # ── G1 ─────────────────────────────────────────────────────
    g1_relative_thresh = float(cfg.get("g1_relative_lift", 0.005))
    g1_ic_abs_thresh = float(cfg.get("g1_rank_ic_lift", 0.005))
    composite_lift = cs - bs * (1 + g1_relative_thresh)
    rank_ic_lifts = {
        h: float(cand_per_horizon[h].get("rank_ic", 0.0)) - float(base_per_horizon[h].get("rank_ic", 0.0))
        for h in (5, 10, 20)
    }
    max_ic_lift = max(rank_ic_lifts.values()) if rank_ic_lifts else 0.0
    g1_passed = bool(composite_lift >= 0 or max_ic_lift >= g1_ic_abs_thresh)
    g1 = GateResult(
        gate_id="G1", name="composite_or_rank_ic_lift", passed=g1_passed,
        value=max(composite_lift, max_ic_lift), threshold=max(0.0, g1_ic_abs_thresh),
        detail=f"composite Δ={cs-bs:+.4f}, rank_ic Δ {rank_ic_lifts}",
    )

    # ── G2: per-horizon rank IC non-degradation ───────────────
    g2_tolerance = float(cfg.get("g2_rank_ic_tolerance", 0.02))
    g2_failures: list[str] = []
    for h in (5, 10, 20):
        cand_ic = float(cand_per_horizon[h].get("rank_ic", 0.0))
        base_ic = float(base_per_horizon[h].get("rank_ic", 0.0))
        if cand_ic < base_ic - g2_tolerance:
            g2_failures.append(f"{h}d rank_ic {cand_ic:+.3f} < base {base_ic:+.3f} - {g2_tolerance}")
    g2 = GateResult(
        gate_id="G2", name="no_horizon_rank_ic_regression", passed=not g2_failures,
        value=min(rank_ic_lifts.values()), threshold=-g2_tolerance,
        detail="; ".join(g2_failures) or "all 3 horizons within tolerance",
    )

    # ── G6: parameter drift cap (regime-aware) ────────────────
    # When the search may produce negative weights (signal inversion is allowed),
    # the natural [-1.5, 1.8] band has max single drift = 2.5 vs default 1.0,
    # so the cap must scale with the bound regime, otherwise G6 over-rejects.
    has_negative = any(
        "weights." in k and float(v) < 0
        for k, v in candidate_overlay.items()
        if isinstance(v, (int, float))
    )
    g6_norm_cap = float(cfg.get("g6_l2_norm_cap", 1.20 if has_negative else 0.55))
    g6_single_cap = float(cfg.get("g6_single_param_cap", 2.6 if has_negative else 1.5))
    weight_deltas = []
    max_single = 0.0
    for k, v in candidate_overlay.items():
        if "weights." in k and k.split(".")[-1] != "risk_penalty_weight":
            try:
                d = float(v) - 1.0
                weight_deltas.append(d)
                if abs(d) > max_single:
                    max_single = abs(d)
            except (ValueError, TypeError):
                pass
    if weight_deltas:
        l2_norm = math.sqrt(sum(d * d for d in weight_deltas) / len(weight_deltas))
    else:
        l2_norm = 0.0
    g6_passed = bool(l2_norm < g6_norm_cap and max_single < g6_single_cap)
    g6 = GateResult(
        gate_id="G6", name="parameter_drift_cap", passed=g6_passed,
        value=l2_norm, threshold=g6_norm_cap,
        detail=f"L2/√K={l2_norm:.3f} max_single={max_single:.2f} (caps {g6_norm_cap}/{g6_single_cap}; "
               f"{'negative-weight regime' if has_negative else 'positive-only regime'})",
    )

    # ── G7: rank IC absolute floor (works for negative baselines) ─
    g7_abs_tolerance = float(cfg.get("g7_rank_ic_floor", 0.01))
    g7_failures: list[str] = []
    for h in (5, 10, 20):
        cand_ic = float(cand_per_horizon[h].get("rank_ic", 0.0))
        base_ic = float(base_per_horizon[h].get("rank_ic", 0.0))
        if cand_ic < base_ic - g7_abs_tolerance:
            g7_failures.append(f"{h}d rank_ic {cand_ic:+.3f} below base-{g7_abs_tolerance} {base_ic-g7_abs_tolerance:+.3f}")
    g7 = GateResult(
        gate_id="G7", name="rank_ic_absolute_floor", passed=not g7_failures,
        value=min(rank_ic_lifts.values()), threshold=-g7_abs_tolerance,
        detail="; ".join(g7_failures) or "all 3 horizons above floor",
    )

    gates = [g1, g2, g6, g7]

    # ── G9: K-fold consistency (per-horizon) ─────────────────
    if kfold_results:
        n_folds = len(kfold_results)
        min_pos = int(cfg.get("g9_min_positive_folds", math.ceil(n_folds * 0.75)))
        per_horizon_pass: dict[str, bool] = {}
        per_horizon_count: dict[str, int] = {}
        per_horizon_lifts: dict[str, list[float]] = {}
        for h in (5, 10, 20):
            lifts = []
            for fold in kfold_results:
                vb = fold.get("val_baseline", {}).get(f"objective_{h}d", {}).get("rank_ic", 0.0)
                vt = fold.get("val_tuned", {}).get(f"objective_{h}d", {}).get("rank_ic", 0.0)
                lifts.append(float(vt) - float(vb))
            n_positive = sum(1 for l in lifts if l > 0)
            per_horizon_count[f"{h}d"] = n_positive
            per_horizon_pass[f"{h}d"] = n_positive >= min_pos
            per_horizon_lifts[f"{h}d"] = lifts
        all_pass = all(per_horizon_pass.values())
        detail_parts = [
            f"{h}: {per_horizon_count[h]}/{n_folds} positive folds (lifts {[f'{l:+.3f}' for l in per_horizon_lifts[h]]})"
            for h in ("5d", "10d", "20d")
        ]
        g9 = GateResult(
            gate_id="G9",
            name="kfold_consistency",
            passed=all_pass,
            value=float(min(per_horizon_count.values())),
            threshold=float(min_pos),
            detail=f"min_positive_folds={min_pos}/{n_folds} required; " + "; ".join(detail_parts),
            per_horizon=per_horizon_pass,
        )
        gates.append(g9)

    passed = all(g.passed for g in gates)
    summary = (
        f"{'ACCEPTED' if passed else 'REJECTED'} {sum(1 for g in gates if g.passed)}/{len(gates)} gates · "
        f"composite Δ={cs-bs:+.4f} · rank_ic 5d/10d/20d Δ={rank_ic_lifts[5]:+.3f}/{rank_ic_lifts[10]:+.3f}/{rank_ic_lifts[20]:+.3f}"
    )
    return PromotionDecision(
        accepted=passed,
        gates=gates,
        summary=summary,
        candidate_score=cs,
        baseline_score=bs,
        rank_ic_summary={
            f"{h}d": {
                "candidate": float(cand_per_horizon[h].get("rank_ic", 0.0)),
                "baseline": float(base_per_horizon[h].get("rank_ic", 0.0)),
                "lift": rank_ic_lifts[h],
            }
            for h in (5, 10, 20)
        },
    )


def auto_promote_if_passing(
    decision: PromotionDecision,
    *,
    candidate_overlay: Mapping[str, Any],
    base_yaml: Path,
    variant_output: Path,
    reject_dir: Path | None = None,
    backup: bool = True,
) -> dict[str, Any]:
    """If gates pass, write a YAML variant from base + overlay.

    Default: writes to `variant_output` path; does NOT overwrite the base YAML.
    To replace baseline, caller must explicitly point variant_output at base_yaml.
    On failure, writes a reject report to reject_dir if provided.
    """
    out: dict[str, Any] = {
        "accepted": decision.accepted,
        "decision": decision.to_dict(),
        "variant_path": None,
        "reject_path": None,
        "backup_path": None,
    }
    if not decision.accepted:
        if reject_dir is not None:
            reject_dir.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
            reject_path = reject_dir / f"rejected_{stamp}.json"
            reject_path.write_text(json.dumps(out["decision"], ensure_ascii=False, indent=2), encoding="utf-8")
            out["reject_path"] = str(reject_path)
        return out

    base_dict = _read_yaml(base_yaml)
    promoted = apply_param_overlay(base_dict, dict(candidate_overlay))
    backup_path: Path | None = None
    if variant_output == base_yaml and backup:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = base_yaml.with_suffix(base_yaml.suffix + f".bak_{stamp}")
        shutil.copy2(base_yaml, backup_path)
    variant_output.parent.mkdir(parents=True, exist_ok=True)
    variant_output.write_text(yaml.safe_dump(promoted, allow_unicode=True, sort_keys=False), encoding="utf-8")
    out["variant_path"] = str(variant_output)
    out["backup_path"] = str(backup_path) if backup_path else None
    return out
