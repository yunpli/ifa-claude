"""YAML promotion gates for SME tuned profiles."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

from ifa.families.sme.params.store import DEFAULT_MARKET_STRUCTURE_YAML
from ifa.families.sme.tuning.bucket_review import build_bucket_review


def _profiles_seen(engine, *, start: dt.date, end: dt.date) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT split_part(logic_version, '/', 2) AS profile
            FROM sme.sme_market_structure_daily
            WHERE trade_date BETWEEN :start AND :end
              AND logic_version LIKE 'market_structure_%/%/%'
        """), {"start": start, "end": end}).fetchall()
    return {r[0] for r in rows if r[0]}


def build_promotion_decision(
    engine,
    *,
    candidate_profile: str,
    start: dt.date,
    end: dt.date,
    min_sample_days: int = 60,
    min_ready_horizons: int = 3,
) -> dict[str, Any]:
    review = build_bucket_review(engine, start=start, end=end, min_sample_days=min_sample_days)
    profiles_seen = _profiles_seen(engine, start=start, end=end)
    positive = [b for b in review["bucket_scores"] if b["avg_signal_score"] > 0]
    positive_long = [
        b for b in review["bucket_scores"]
        if b.get("direction") == "long" and b["avg_signal_score"] > 0.05 and b["avg_success_rate"] >= 0.48
    ]
    positive_avoid = [
        b for b in review["bucket_scores"]
        if b.get("direction") == "avoid" and b["avg_signal_score"] > 0.05 and b["avg_success_rate"] >= 0.50
    ]
    gates = {
        "candidate_profile_evaluated": candidate_profile in profiles_seen,
        "ready_horizon_count": len(review["ready_horizons"]) >= min_ready_horizons,
        "has_positive_buckets": len(positive) >= 2,
        "has_positive_long_bucket": len(positive_long) >= 1,
        "has_positive_avoid_bucket": len(positive_avoid) >= 1,
        "review_status_ok": review["status"] == "ok",
    }
    should_promote = all(gates.values())
    return {
        "status": "promote" if should_promote else "hold",
        "candidate_profile": candidate_profile,
        "start": start,
        "end": end,
        "min_sample_days": min_sample_days,
        "profiles_seen": sorted(profiles_seen),
        "gates": gates,
        "review": review,
        "reason": "all gates passed" if should_promote else "one or more gates failed",
    }


def apply_active_profile(*, candidate_profile: str, path: str | Path | None = None) -> dict[str, Any]:
    yaml_path = Path(path) if path else DEFAULT_MARKET_STRUCTURE_YAML
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    profiles = raw.get("profiles") or {}
    if candidate_profile not in profiles:
        raise KeyError(f"profile not found in YAML: {candidate_profile}")
    previous = raw.get("active_profile")
    raw["active_profile"] = candidate_profile
    yaml_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"path": str(yaml_path), "previous_active_profile": previous, "active_profile": candidate_profile}
