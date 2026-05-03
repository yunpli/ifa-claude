"""Model artifact persistence + version management — Phase 3.5/3.7.

Layout on disk:
    {ifa_data_root}/ningbo/models/
        {model_version}/
            stacking.joblib       # fitted CalibratedClassifierCV(StackingClassifier)
            base_lr.joblib        # fitted base models (kept for diagnostics)
            base_rf.joblib
            base_xgb.joblib
            metadata.json         # feature_columns, metrics, train/OOS ranges
        active.json               # {"model_version": "v2026.05.02_a"}

Active model is the one used by MLScorer at inference time.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import joblib

from ifa.config import get_settings
from ifa.families.ningbo.ml.trainer import TrainingArtifacts


def _models_root() -> Path:
    """Where ningbo ML artifacts live.

    Sits as a sibling of `output_root` so reports + models share an env root,
    e.g. `/Users/neoclaw/claude/ifaenv/{out, logs, models/ningbo}`.
    """
    settings = get_settings()
    out = Path(settings.output_root)
    root = out.parent / "models" / "ningbo"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_artifacts(art: TrainingArtifacts) -> Path:
    """Persist training artifacts to disk. Returns the version dir path."""
    version_dir = _models_root() / art.model_version
    version_dir.mkdir(parents=True, exist_ok=True)

    # Save fitted models
    joblib.dump(art.stacking_model, version_dir / "stacking.joblib")
    for name, model in art.base_models.items():
        joblib.dump(model, version_dir / f"base_{name}.joblib")

    # Save metadata + metrics (drop unpicklable model objects)
    metadata = {
        "model_version":  art.model_version,
        "feature_columns": art.feature_columns,
        "train_range":    [art.train_range[0].isoformat(), art.train_range[1].isoformat()],
        "oos_range":      [art.oos_range[0].isoformat(),   art.oos_range[1].isoformat()],
        "n_train":        art.n_train,
        "n_oos":          art.n_oos,
        "pos_rate_train": art.pos_rate_train,
        "pos_rate_oos":   art.pos_rate_oos,
        "saved_at":       dt.datetime.now().isoformat(),
        "metrics": {
            name: {
                "train_auc":           m.train_auc,
                "oos_auc":             m.oos_auc,
                "oos_avg_precision":   m.oos_avg_precision,
                "oos_brier":           m.oos_brier,
                "oos_log_loss":        m.oos_log_loss,
                "oos_top5_precision":  m.oos_top5_precision,
                "oos_top5_avg_return": m.oos_top5_avg_return,
                "feature_importances": m.feature_importances,
            }
            for name, m in art.metrics.items()
        },
    }
    (version_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))

    return version_dir


def set_active(model_version: str) -> None:
    """Mark a saved version as active (used by next report runs)."""
    if not (_models_root() / model_version).exists():
        raise FileNotFoundError(f"Model version {model_version} not found in {_models_root()}")
    (_models_root() / "active.json").write_text(
        json.dumps({"model_version": model_version, "set_at": dt.datetime.now().isoformat()}, indent=2)
    )


def get_active_version() -> str | None:
    """Return the currently active model version tag, or None if none set."""
    p = _models_root() / "active.json"
    if not p.exists():
        return None
    return json.loads(p.read_text()).get("model_version")


def load_active_model() -> tuple[Any, dict[str, Any]] | None:
    """Load the active stacking model + metadata.

    Returns (model, metadata) or None if no active version is set.
    """
    version = get_active_version()
    if version is None:
        return None
    return load_model(version)


def load_model(model_version: str) -> tuple[Any, dict[str, Any]]:
    """Load a specific saved version. Returns (stacking_model, metadata)."""
    version_dir = _models_root() / model_version
    if not version_dir.exists():
        raise FileNotFoundError(f"Model version {model_version} not found at {version_dir}")
    model = joblib.load(version_dir / "stacking.joblib")
    metadata = json.loads((version_dir / "metadata.json").read_text())
    return model, metadata


def list_versions() -> list[dict[str, Any]]:
    """List all saved model versions with summary metadata."""
    out = []
    active = get_active_version()
    for d in sorted(_models_root().iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "metadata.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        out.append({
            "version":    meta["model_version"],
            "active":     meta["model_version"] == active,
            "saved_at":   meta.get("saved_at"),
            "n_train":    meta["n_train"],
            "n_oos":      meta["n_oos"],
            "stacking_oos_auc":      meta["metrics"].get("stacking", {}).get("oos_auc"),
            "stacking_top5_return":  meta["metrics"].get("stacking", {}).get("oos_top5_avg_return"),
        })
    return out
