"""Global preset promotion helpers.

Global tuning artifacts are experiments until reviewed. This module emits a
reviewable YAML patch and can apply it only when explicitly requested.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from dataclasses import asdict, dataclass
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
    current: Any = params if dotted_key.startswith(("risk.", "t0.", "model.", "runtime.", "data.", "intraday.", "cache.", "report.", "tuning.")) else params.get("strategy_matrix", {})
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
