"""Local artifact IO for Stock Edge tuning results."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TuningArtifact:
    ts_code: str
    as_of_trade_date: dt.date
    kind: str
    base_param_hash: str
    overlay: dict[str, Any]
    objective_score: float
    metrics: dict[str, Any]
    candidate_count: int
    history_start: dt.date | None
    history_end: dt.date | None
    history_rows: int
    created_at: dt.datetime
    namespace: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_tuning_root() -> Path:
    return Path("/Users/neoclaw/claude/ifaenv/models/stock/tuning")


def artifact_path(artifact: TuningArtifact, *, root: Path | None = None) -> Path:
    base = root or default_tuning_root()
    safe_code = artifact.ts_code.replace(".", "_")
    return base / artifact.kind / safe_code / f"{artifact.as_of_trade_date:%Y%m%d}.json"


def write_tuning_artifact(artifact: TuningArtifact, *, root: Path | None = None) -> Path:
    path = artifact_path(artifact, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact.to_dict(), ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return path


def read_tuning_artifact(path: Path) -> TuningArtifact:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _artifact_from_dict(raw)


def find_latest_tuning_artifact(
    *,
    ts_code: str,
    kind: str = "pre_report_overlay",
    root: Path | None = None,
) -> TuningArtifact | None:
    base = root or default_tuning_root()
    safe_code = ts_code.replace(".", "_")
    directory = base / kind / safe_code
    if not directory.exists():
        return None
    files = sorted(directory.glob("*.json"), reverse=True)
    for path in files:
        try:
            return read_tuning_artifact(path)
        except Exception:
            continue
    return None


def _artifact_from_dict(raw: dict[str, Any]) -> TuningArtifact:
    return TuningArtifact(
        ts_code=str(raw["ts_code"]),
        as_of_trade_date=dt.date.fromisoformat(str(raw["as_of_trade_date"])),
        kind=str(raw["kind"]),
        base_param_hash=str(raw["base_param_hash"]),
        overlay=dict(raw.get("overlay") or {}),
        objective_score=float(raw.get("objective_score", 0.0)),
        metrics=dict(raw.get("metrics") or {}),
        candidate_count=int(raw.get("candidate_count", 0)),
        history_start=dt.date.fromisoformat(str(raw["history_start"])) if raw.get("history_start") else None,
        history_end=dt.date.fromisoformat(str(raw["history_end"])) if raw.get("history_end") else None,
        history_rows=int(raw.get("history_rows", 0)),
        created_at=dt.datetime.fromisoformat(str(raw["created_at"])),
        namespace=str(raw.get("namespace") or ""),
    )
