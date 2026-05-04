"""Research fetcher cache layer — reads/writes api_cache and computed_cache.

TTL table per API (hours):
  · stock_basic / stock_company: 24h (changes rarely)
  · income / balancesheet / cashflow / fina_indicator: 4h (may update intraday on filing day)
  · forecast / express: 2h (active during earnings season)
  · daily / daily_basic / stk_factor_pro: 1h (intraday)
  · anns_d / irm_qa: 1h (new announcements trickle in)
  · research_report / top10_holders / stk_holdertrade: 6h
  · dividend / disclosure_date / fina_audit: 12h

Cache hit: fetched_at + TTL > now()
Params hash: SHA256(sorted JSON of kwargs)
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.engine import Engine

# TTL in hours per API name
_TTL_HOURS: Final[dict[str, int]] = {
    "stock_basic": 24,
    "stock_company": 24,
    "income": 4,
    "balancesheet": 4,
    "cashflow": 4,
    "fina_indicator": 4,
    "forecast": 2,
    "express": 2,
    "daily": 1,
    "daily_basic": 1,
    "stk_factor_pro": 1,
    "anns_d": 1,
    "irm_qa_sh": 1,
    "irm_qa_sz": 1,
    "research_report": 6,
    "top10_holders": 6,
    "top10_floatholders": 6,
    "stk_holdertrade": 6,
    "stk_managers": 6,
    "stk_rewards": 6,
    "cyq_perf": 2,
    "dividend": 12,
    "disclosure_date": 12,
    "fina_audit": 12,
    "pledge_stat": 6,
}
_DEFAULT_TTL_HOURS = 6


def _params_hash(kwargs: dict[str, Any]) -> str:
    payload = json.dumps(kwargs, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:64]


def _ttl(api_name: str) -> int:
    return _TTL_HOURS.get(api_name, _DEFAULT_TTL_HOURS)


def cache_get(engine: Engine, ts_code: str, api_name: str, kwargs: dict[str, Any]) -> Any | None:
    """Return cached response JSON if still valid, else None."""
    ph = _params_hash(kwargs)
    now = datetime.now(tz=timezone.utc)
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT response_json
                FROM research.api_cache
                WHERE ts_code = :tc AND api_name = :an AND params_hash = :ph
                  AND expires_at > :now
            """),
            {"tc": ts_code, "an": api_name, "ph": ph, "now": now},
        ).fetchone()
    return row[0] if row else None


def cache_set(engine: Engine, ts_code: str, api_name: str, kwargs: dict[str, Any], response: Any) -> None:
    """Write (or overwrite) a cache entry with appropriate TTL."""
    ph = _params_hash(kwargs)
    now = datetime.now(tz=timezone.utc)
    expires = now + timedelta(hours=_ttl(api_name))
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO research.api_cache
                    (ts_code, api_name, params_hash, response_json, fetched_at, expires_at)
                VALUES (:tc, :an, :ph, :resp::jsonb, :now, :exp)
                ON CONFLICT (ts_code, api_name, params_hash) DO UPDATE SET
                    response_json = EXCLUDED.response_json,
                    fetched_at    = EXCLUDED.fetched_at,
                    expires_at    = EXCLUDED.expires_at
            """),
            {
                "tc": ts_code, "an": api_name, "ph": ph,
                "resp": json.dumps(response, default=str),
                "now": now, "exp": expires,
            },
        )


def computed_get(engine: Engine, ts_code: str, compute_key: str, inputs_hash: str) -> Any | None:
    """Return computed cache if inputs_hash matches (any-change invalidation)."""
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT result_json FROM research.computed_cache
                WHERE ts_code = :tc AND compute_key = :ck AND inputs_hash = :ih
            """),
            {"tc": ts_code, "ck": compute_key, "ih": inputs_hash},
        ).fetchone()
    return row[0] if row else None


def computed_set(
    engine: Engine, ts_code: str, compute_key: str, inputs_hash: str, result: Any
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO research.computed_cache
                    (ts_code, compute_key, inputs_hash, result_json, computed_at)
                VALUES (:tc, :ck, :ih, :res::jsonb, NOW())
                ON CONFLICT (ts_code, compute_key) DO UPDATE SET
                    inputs_hash = EXCLUDED.inputs_hash,
                    result_json = EXCLUDED.result_json,
                    computed_at = EXCLUDED.computed_at
            """),
            {
                "tc": ts_code, "ck": compute_key, "ih": inputs_hash,
                "res": json.dumps(result, default=str),
            },
        )


def invalidate_financial_cache(engine: Engine, ts_code: str) -> int:
    """Force-expire all financial API caches for a stock (call on new filing)."""
    financial_apis = ("income", "balancesheet", "cashflow", "fina_indicator", "forecast", "express")
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE research.api_cache
                SET expires_at = NOW() - INTERVAL '1 second'
                WHERE ts_code = :tc AND api_name = ANY(:apis)
            """),
            {"tc": ts_code, "apis": list(financial_apis)},
        )
    return result.rowcount
