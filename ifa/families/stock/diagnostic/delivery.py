"""Dry-run delivery payloads for Stock Edge diagnostic artifacts.

The module deliberately does not send Telegram messages.  It shapes the
contract a sender can later consume: concise text, attachment paths, latency,
and failure context from an already generated diagnostic manifest.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from ifa.core.report.timezones import bjt_now


DELIVERY_SCHEMA_VERSION = 1


def load_diagnostic_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("artifact_type") != "stock_edge_diagnostic_run":
        raise ValueError(f"{path} is not a stock_edge_diagnostic_run manifest")
    return payload


def build_telegram_delivery_payload(
    manifest: dict[str, Any],
    *,
    manifest_path: Path | None = None,
    recipient_placeholder: str = "telegram:<chat_id>",
    dry_run: bool = True,
    generated_at: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build a no-send Telegram delivery contract from a diagnostic manifest."""
    ts_code = str(manifest.get("ts_code") or "")
    name = manifest.get("name") or ts_code
    as_of = str(manifest.get("as_of_trade_date") or "")
    conclusion = str(manifest.get("conclusion") or "unknown")
    confidence = str(manifest.get("confidence") or "unknown")
    synthesis = manifest.get("synthesis") or {}
    horizons = synthesis.get("horizon_suitability") or {}
    conflict = _first_conflict(synthesis)
    stale_or_missing = _stale_or_missing(manifest)
    latency = _latency_summary(manifest)
    attachments = _attachment_paths(manifest, manifest_path=manifest_path)
    failure_context = _failure_context(manifest)
    title = f"Stock Edge 单股诊断 · {name} ({ts_code}) · {as_of}"
    short_lines = [
        f"结论: {conclusion}，置信度: {confidence}",
        f"周期: 5d={horizons.get('5d', 'n/a')} / 10d={horizons.get('10d', 'n/a')} / 20d={horizons.get('20d', 'n/a')}",
        f"触发: {synthesis.get('trigger') or '等待结构化触发条件'}",
        f"失效: {synthesis.get('invalidation') or '跌破关键支撑或硬风险命中'}",
        f"证据: {stale_or_missing or '核心视角未标记 stale/unavailable'}；耗时: {latency['total_latency_ms']}ms",
    ]
    if conflict:
        short_lines.append(f"冲突: {conflict}")
    return {
        "artifact_type": "stock_edge_diagnostic_telegram_delivery_payload",
        "schema_version": DELIVERY_SCHEMA_VERSION,
        "channel": "telegram",
        "delivery_mode": "ifa_direct_send_preferred_if_enabled",
        "dry_run": dry_run,
        "external_send_performed": False,
        "recipient_placeholder": recipient_placeholder,
        "title": title,
        "short_text": "\n".join(short_lines[:6]),
        "attachments": attachments,
        "latency": latency,
        "failure_context": failure_context,
        "source_manifest_path": str(manifest_path) if manifest_path else None,
        "generated_at": (generated_at or bjt_now()).isoformat(),
    }


def write_delivery_payload(
    manifest_path: Path,
    *,
    output_dir: Path | None = None,
    recipient_placeholder: str = "telegram:<chat_id>",
    dry_run: bool = True,
) -> Path:
    manifest = load_diagnostic_manifest(manifest_path)
    payload = build_telegram_delivery_payload(
        manifest,
        manifest_path=manifest_path,
        recipient_placeholder=recipient_placeholder,
        dry_run=dry_run,
    )
    directory = output_dir or manifest_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    stem = manifest_path.stem.replace("_manifest", "_telegram_delivery")
    path = _dedupe_path(directory / f"{stem}.json")
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2) + "\n", encoding="utf-8")
    return path


def _attachment_paths(manifest: dict[str, Any], *, manifest_path: Path | None) -> list[dict[str, Any]]:
    output_paths = manifest.get("output_paths") or {}
    attachments: list[dict[str, Any]] = []
    for kind in ("html", "markdown", "md", "json"):
        path = output_paths.get(kind)
        if path:
            attachments.append({"kind": kind, "path": str(path), "required": kind == "html"})
    if manifest_path:
        attachments.append({"kind": "manifest", "path": str(manifest_path), "required": False})
    return attachments


def _latency_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    statuses = manifest.get("perspective_statuses") or {}
    by_perspective = {}
    total = 0.0
    for key, row in statuses.items():
        latency = row.get("latency_ms")
        if latency is None:
            by_perspective[key] = None
            continue
        value = round(float(latency), 2)
        by_perspective[key] = value
        total += value
    return {"total_latency_ms": round(total, 2), "by_perspective_ms": by_perspective}


def _failure_context(manifest: dict[str, Any]) -> dict[str, Any]:
    statuses = manifest.get("perspective_statuses") or {}
    return {
        "unavailable": [key for key, row in statuses.items() if row.get("status") == "unavailable"],
        "errors": [key for key, row in statuses.items() if row.get("status") == "error"],
        "stale": [key for key, row in statuses.items() if row.get("freshness_status") == "stale"],
        "missing_required": {
            key: row.get("missing_required") or []
            for key, row in statuses.items()
            if row.get("missing_required")
        },
        "missing_evidence": {
            key: row.get("missing_evidence") or []
            for key, row in statuses.items()
            if row.get("missing_evidence")
        },
    }


def _stale_or_missing(manifest: dict[str, Any]) -> str:
    context = _failure_context(manifest)
    names = [*context["stale"], *context["unavailable"], *context["errors"]]
    return ", ".join(dict.fromkeys(names))


def _first_conflict(synthesis: dict[str, Any]) -> str | None:
    conflicts = synthesis.get("conflicts") or []
    if conflicts:
        return str(conflicts[0])
    taxonomy = synthesis.get("conflict_taxonomy") or []
    if taxonomy:
        return str(taxonomy[0])
    return None


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    for idx in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{idx}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find unused delivery payload path for {path}")
