"""Persistence for ta.regime_daily."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.ta.regime.classifier import Regime, RegimeResult

log = logging.getLogger(__name__)


def upsert_regime_daily(
    engine: Engine,
    trade_date: date,
    result: RegimeResult,
    transitions_json: dict | None = None,
) -> None:
    sql = text("""
        INSERT INTO ta.regime_daily
            (trade_date, regime, confidence, evidence_json, transitions_json)
        VALUES
            (:trade_date, :regime, :confidence, :evidence, :transitions)
        ON CONFLICT (trade_date) DO UPDATE SET
            regime = EXCLUDED.regime,
            confidence = EXCLUDED.confidence,
            evidence_json = EXCLUDED.evidence_json,
            transitions_json = EXCLUDED.transitions_json
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "trade_date": trade_date,
            "regime": result.regime,
            "confidence": result.confidence,
            "evidence": json.dumps(result.evidence, ensure_ascii=False, default=str),
            "transitions": json.dumps(transitions_json, ensure_ascii=False) if transitions_json else None,
        })


def load_regime_sequence(
    engine: Engine,
    *,
    lookback_days: int = 120,
    on_date: date | None = None,
) -> list[Regime]:
    """Return the regime time-series for [on_date - lookback_days, on_date], ordered by date."""
    on_date = on_date or date.today()
    cutoff = on_date - timedelta(days=lookback_days)
    sql = text("""
        SELECT regime FROM ta.regime_daily
        WHERE trade_date >= :cutoff AND trade_date <= :on_date
        ORDER BY trade_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"cutoff": cutoff, "on_date": on_date}).fetchall()
    return [r[0] for r in rows]


def latest_regime(engine: Engine) -> tuple[date, Regime] | None:
    sql = text("SELECT trade_date, regime FROM ta.regime_daily ORDER BY trade_date DESC LIMIT 1")
    with engine.connect() as conn:
        row = conn.execute(sql).fetchone()
    return (row[0], row[1]) if row else None
