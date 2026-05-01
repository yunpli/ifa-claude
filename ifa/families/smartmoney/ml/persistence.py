"""Model persistence — pickle/load/versioning to ~/claude/ifaenv/models/.

Storage layout:
  ~/claude/ifaenv/models/
    smartmoney/
      logistic_v2026_04.pkl
      random_forest_v2026_04.pkl
      xgboost_v2026_04.pkl
      manifest.json            ← tracks all saved versions + metrics

Versioning:
  version_tag is typically the param_version name (e.g. 'v2026_04').
  Callers are responsible for choosing meaningful tags.

Safety:
  - Saving overwrites any existing file with the same name.
  - Loading raises FileNotFoundError if the file doesn't exist.
  - manifest.json is updated atomically (write-then-rename).
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Default base directory — can be overridden by IFA_MODEL_DIR env var
_DEFAULT_BASE = Path.home() / "claude" / "ifaenv" / "models" / "smartmoney"


def _model_dir() -> Path:
    base = Path(os.environ.get("IFA_MODEL_DIR", str(_DEFAULT_BASE)))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _model_path(model_name: str, version_tag: str) -> Path:
    return _model_dir() / f"{model_name}_{version_tag}.pkl"


def _manifest_path() -> Path:
    return _model_dir() / "manifest.json"


def _load_manifest() -> dict[str, Any]:
    mp = _manifest_path()
    if not mp.exists():
        return {"models": []}
    try:
        with open(mp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"models": []}


def _save_manifest(manifest: dict[str, Any]) -> None:
    mp = _manifest_path()
    # Write atomically via temp file in the same directory
    fd, tmp = tempfile.mkstemp(dir=mp.parent, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp, mp)
    except Exception:
        os.unlink(tmp)
        raise


# ── Public API ────────────────────────────────────────────────────────────────

def save_model(
    model: Any,
    *,
    model_name: str,
    version_tag: str,
    metrics: dict[str, float] | None = None,
    notes: str | None = None,
) -> Path:
    """Pickle ``model`` to disk and update the manifest.

    Args:
        model:       Any sklearn/XGBoost/custom model with to_dict() method.
        model_name:  'logistic' / 'random_forest' / 'xgboost'.
        version_tag: e.g. 'v2026_04'.
        metrics:     Evaluation metrics to record in manifest.
        notes:       Free-form notes.

    Returns:
        Path to the saved .pkl file.
    """
    path = _model_path(model_name, version_tag)
    with open(path, "wb") as f:
        pickle.dump(model, f, protocol=pickle.HIGHEST_PROTOCOL)

    manifest = _load_manifest()
    # Remove any existing entry for the same (model_name, version_tag)
    manifest["models"] = [
        m for m in manifest["models"]
        if not (m["model_name"] == model_name and m["version_tag"] == version_tag)
    ]
    entry: dict[str, Any] = {
        "model_name": model_name,
        "version_tag": version_tag,
        "saved_at": datetime.now(tz=timezone.utc).isoformat(),
        "path": str(path.name),
        "metrics": metrics or {},
        "notes": notes or "",
        "meta": model.to_dict() if hasattr(model, "to_dict") else {},
    }
    manifest["models"].append(entry)
    manifest["models"].sort(key=lambda x: x["saved_at"], reverse=True)
    _save_manifest(manifest)

    log.info("[persistence] saved %s/%s → %s (metrics=%s)",
             model_name, version_tag, path.name, metrics)
    return path


def load_model(model_name: str, version_tag: str) -> Any:
    """Load and return a pickled model.

    Raises:
        FileNotFoundError if the model file does not exist.
    """
    path = _model_path(model_name, version_tag)
    if not path.exists():
        raise FileNotFoundError(
            f"Model not found: {path}\n"
            f"Available: {list_models()}"
        )
    with open(path, "rb") as f:
        model = pickle.load(f)
    log.info("[persistence] loaded %s/%s from %s", model_name, version_tag, path.name)
    return model


def load_latest(model_name: str) -> Any:
    """Load the most recently saved version of ``model_name``.

    Raises:
        FileNotFoundError if no version exists.
    """
    manifest = _load_manifest()
    candidates = [
        m for m in manifest["models"]
        if m["model_name"] == model_name
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No saved versions of '{model_name}' found in {_model_dir()}"
        )
    latest = candidates[0]  # manifest is sorted newest-first
    return load_model(model_name, latest["version_tag"])


def list_models() -> list[dict[str, Any]]:
    """Return the manifest entries for all saved models."""
    return _load_manifest().get("models", [])


def delete_model(model_name: str, version_tag: str) -> bool:
    """Delete a saved model file and remove its manifest entry.

    Returns True if deleted, False if not found.
    """
    path = _model_path(model_name, version_tag)
    found = False
    if path.exists():
        path.unlink()
        found = True
    manifest = _load_manifest()
    before = len(manifest["models"])
    manifest["models"] = [
        m for m in manifest["models"]
        if not (m["model_name"] == model_name and m["version_tag"] == version_tag)
    ]
    if len(manifest["models"]) < before:
        found = True
        _save_manifest(manifest)
    if found:
        log.info("[persistence] deleted %s/%s", model_name, version_tag)
    return found
