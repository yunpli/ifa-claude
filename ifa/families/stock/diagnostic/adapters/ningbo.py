"""Ningbo strategy perspective adapter for Stock Edge diagnostic reports."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.engine import Engine

from ifa.families.stock.diagnostic.models import EvidencePoint, PerspectiveEvidence

from .common import days_between, freshness_from_points, query_dicts, timed, to_float


def collect(*, engine: Engine, ts_code: str, as_of: dt.date) -> PerspectiveEvidence:
    return timed("ningbo", lambda: _collect(engine, ts_code, as_of))


def _collect(engine: Engine, ts_code: str, as_of: dt.date) -> PerspectiveEvidence:
    rows = query_dicts(engine, """
        SELECT rec_date, ts_code, strategy, scoring_mode, param_version,
               rec_price, confidence_score, rec_signal_meta
        FROM ningbo.recommendations_daily
        WHERE ts_code = :ts_code AND rec_date <= :as_of
        ORDER BY rec_date DESC, confidence_score DESC NULLS LAST
        LIMIT 10
    """, {"ts_code": ts_code, "as_of": as_of})
    if not rows:
        rows = query_dicts(engine, """
            SELECT rec_date, ts_code, strategy, confidence_score, rec_price, signal_meta
            FROM ningbo.candidates_daily
            WHERE ts_code = :ts_code AND rec_date <= :as_of
            ORDER BY rec_date DESC, confidence_score DESC NULLS LAST
            LIMIT 10
        """, {"ts_code": ts_code, "as_of": as_of})
    if not rows:
        return PerspectiveEvidence("ningbo", "Ningbo", "unavailable", "unknown", "宁波短线策略近期未命中目标股。", missing=["ningbo.recommendations_daily", "ningbo.candidates_daily"])
    rank_context = _load_ningbo_rank_context(engine, ts_code, rows[0].get("rec_date"), rows[0].get("strategy"))
    points = [
        EvidencePoint(
            f"{row.get('strategy')} {row.get('scoring_mode') or 'heuristic'}",
            to_float(row.get("confidence_score")),
            "ningbo.recommendations_daily/candidates_daily",
            str(row.get("rec_date")),
            note=f"rec_price={row.get('rec_price')} recency_days={days_between(row.get('rec_date'), as_of)}",
        )
        for row in rows[:5]
    ]
    if rank_context:
        points.append(EvidencePoint(
            "Ningbo same-day rank context",
            rank_context,
            "ningbo.recommendations_daily/candidates_daily",
            str(rows[0].get("rec_date")),
            note=f"strategy={rows[0].get('strategy')}",
        ))
    return PerspectiveEvidence("ningbo", "Ningbo", "available", "positive", "宁波独立短线策略近期命中目标股；可作为独立参考，不强制与其他视角一致。", points=points, freshness=freshness_from_points(points), raw={"rows": rows, "rank_context": rank_context})


def _load_ningbo_rank_context(engine: Engine, ts_code: str, rec_date: Any, strategy: Any) -> dict[str, Any] | None:
    if rec_date is None:
        return None
    params = {"ts_code": ts_code, "rec_date": rec_date, "strategy": strategy}
    rows = query_dicts(engine, """
        WITH ranked AS (
            SELECT ts_code, strategy, confidence_score,
                   rank() OVER (PARTITION BY rec_date, strategy ORDER BY confidence_score DESC NULLS LAST) AS rank_in_strategy,
                   count(*) OVER (PARTITION BY rec_date, strategy) AS strategy_count
            FROM ningbo.recommendations_daily
            WHERE rec_date=:rec_date
              AND (:strategy IS NULL OR strategy=:strategy)
        )
        SELECT rank_in_strategy, strategy_count
        FROM ranked
        WHERE ts_code=:ts_code
        LIMIT 1
    """, params)
    if not rows:
        rows = query_dicts(engine, """
            WITH ranked AS (
                SELECT ts_code, strategy, confidence_score,
                       rank() OVER (PARTITION BY rec_date, strategy ORDER BY confidence_score DESC NULLS LAST) AS rank_in_strategy,
                       count(*) OVER (PARTITION BY rec_date, strategy) AS strategy_count
                FROM ningbo.candidates_daily
                WHERE rec_date=:rec_date
                  AND (:strategy IS NULL OR strategy=:strategy)
            )
            SELECT rank_in_strategy, strategy_count
            FROM ranked
            WHERE ts_code=:ts_code
            LIMIT 1
        """, params)
    return rows[0] if rows else None
