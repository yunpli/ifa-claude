"""Company identifier resolver: name / code (any format) → canonical ts_code.

Lookup order:
1. Exact ts_code match (e.g. "001339.SZ")
2. 6-digit code → try both .SZ and .SH (BJ last)
3. Exact Chinese name match against stock_basic
4. Fuzzy name match (Levenshtein / longest-common-subsequence ratio ≥ FUZZY_THRESHOLD)

Raises:
    CompanyNotFoundError   — nothing found above threshold
    AmbiguousCompanyError  — fuzzy match returns multiple candidates with similar scores
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Engine

FUZZY_THRESHOLD: Final[float] = 0.85
_SUFFIX_PATTERN = re.compile(r"^\d{6}\.(SZ|SH|BJ)$", re.IGNORECASE)
_BARE_CODE_PATTERN = re.compile(r"^\d{6}$")

# Exchange suffix inference for bare 6-digit codes:
# SH: 6xxxxx (mostly), SZ: 0/3xxxxxx, BJ: 8/4/920xxx
_EXCHANGE_MAP: Final[dict[str, str]] = {
    "0": "SZ", "3": "SZ",
    "6": "SH",
    "8": "BJ", "4": "BJ",
    "9": "BJ",  # 920xxx
}


class CompanyNotFoundError(ValueError):
    pass


class AmbiguousCompanyError(ValueError):
    pass


@dataclass(frozen=True)
class CompanyRef:
    ts_code: str
    name: str
    exchange: str


def resolve(query: str, engine: Engine) -> CompanyRef:
    """Resolve *query* to a unique CompanyRef.

    *query* may be: "001339.SZ", "001339", "智微智能", "智微" (fuzzy).
    """
    q = query.strip()

    # 1. Exact ts_code with suffix
    if _SUFFIX_PATTERN.match(q):
        ref = _lookup_by_tscode(q.upper(), engine)
        if ref:
            return ref
        raise CompanyNotFoundError(f"ts_code not found: {q!r}")

    # 2. Bare 6-digit code
    if _BARE_CODE_PATTERN.match(q):
        candidates = _try_bare_code(q, engine)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            codes = [c.ts_code for c in candidates]
            raise AmbiguousCompanyError(
                f"Bare code {q!r} matched multiple listings: {codes}. "
                "Please supply full ts_code with exchange suffix."
            )
        raise CompanyNotFoundError(f"6-digit code not found in any exchange: {q!r}")

    # 3. Exact name
    ref = _lookup_by_name_exact(q, engine)
    if ref:
        return ref

    # 4. Fuzzy name
    candidates = _fuzzy_name_search(q, engine)
    if not candidates:
        raise CompanyNotFoundError(f"No company matched {q!r} (fuzzy threshold {FUZZY_THRESHOLD})")
    if len(candidates) == 1:
        return candidates[0]
    # Multiple fuzzy hits — check if top score is clearly better
    scores = [_similarity(q, c.name) for c in candidates]
    best = max(scores)
    top_hits = [c for c, s in zip(candidates, scores) if s >= best - 0.05]
    if len(top_hits) == 1:
        return top_hits[0]
    names = [(c.ts_code, c.name) for c in top_hits[:5]]
    raise AmbiguousCompanyError(
        f"Fuzzy match for {q!r} returned multiple candidates: {names}. "
        "Please be more specific."
    )


# ── internal helpers ──────────────────────────────────────────────────────────

def _lookup_by_tscode(ts_code: str, engine: Engine) -> CompanyRef | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT ts_code, name, exchange FROM research.company_identity WHERE ts_code = :c"),
            {"c": ts_code},
        ).fetchone()
        if row:
            return CompanyRef(ts_code=row[0], name=row[1], exchange=row[2])
        # Fall back to raw tushare stock_basic (smartmoney or any schema that has it)
        row = conn.execute(
            text("""
                SELECT ts_code, name, exchange
                FROM smartmoney.raw_daily
                WHERE ts_code = :c
                LIMIT 1
            """),
            {"c": ts_code},
        ).fetchone()
        if row:
            return CompanyRef(ts_code=row[0], name="", exchange="")
    return None


def _try_bare_code(code: str, engine: Engine) -> list[CompanyRef]:
    first = code[0]
    if first in _EXCHANGE_MAP:
        primary = f"{code}.{_EXCHANGE_MAP[first]}"
        result = []
        with engine.connect() as conn:
            for ts_code in [primary]:
                row = conn.execute(
                    text("""
                        SELECT ts_code, name, exchange
                        FROM research.company_identity
                        WHERE ts_code = :c
                    """),
                    {"c": ts_code},
                ).fetchone()
                if row:
                    result.append(CompanyRef(ts_code=row[0], name=row[1], exchange=row[2]))
        return result
    # Unknown prefix — try all three
    candidates = []
    with engine.connect() as conn:
        for suffix in ("SZ", "SH", "BJ"):
            row = conn.execute(
                text("SELECT ts_code, name, exchange FROM research.company_identity WHERE ts_code = :c"),
                {"c": f"{code}.{suffix}"},
            ).fetchone()
            if row:
                candidates.append(CompanyRef(ts_code=row[0], name=row[1], exchange=row[2]))
    return candidates


def _lookup_by_name_exact(name: str, engine: Engine) -> CompanyRef | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT ts_code, name, exchange FROM research.company_identity WHERE name = :n"),
            {"n": name},
        ).fetchone()
        if row:
            return CompanyRef(ts_code=row[0], name=row[1], exchange=row[2])
    return None


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _fuzzy_name_search(query: str, engine: Engine) -> list[CompanyRef]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ts_code, name, exchange FROM research.company_identity"),
        ).fetchall()
    matches = []
    for ts_code, name, exchange in rows:
        if name and _similarity(query, name) >= FUZZY_THRESHOLD:
            matches.append(CompanyRef(ts_code=ts_code, name=name, exchange=exchange or ""))
    return matches


def upsert_company_identity(
    engine: Engine,
    ts_code: str,
    name: str,
    exchange: str,
    *,
    market: str | None = None,
    list_date: object | None = None,
    list_status: str | None = None,
    sw_l1_code: str | None = None,
    sw_l1_name: str | None = None,
    sw_l2_code: str | None = None,
    sw_l2_name: str | None = None,
) -> None:
    """Insert or refresh a company_identity row."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO research.company_identity
                    (ts_code, name, exchange, market, list_date, list_status,
                     sw_l1_code, sw_l1_name, sw_l2_code, sw_l2_name, last_refreshed)
                VALUES
                    (:ts_code, :name, :exchange, :market, :list_date, :list_status,
                     :sw_l1_code, :sw_l1_name, :sw_l2_code, :sw_l2_name, NOW())
                ON CONFLICT (ts_code) DO UPDATE SET
                    name            = EXCLUDED.name,
                    exchange        = EXCLUDED.exchange,
                    market          = EXCLUDED.market,
                    list_date       = COALESCE(EXCLUDED.list_date, research.company_identity.list_date),
                    list_status     = COALESCE(EXCLUDED.list_status, research.company_identity.list_status),
                    sw_l1_code      = COALESCE(EXCLUDED.sw_l1_code, research.company_identity.sw_l1_code),
                    sw_l1_name      = COALESCE(EXCLUDED.sw_l1_name, research.company_identity.sw_l1_name),
                    sw_l2_code      = COALESCE(EXCLUDED.sw_l2_code, research.company_identity.sw_l2_code),
                    sw_l2_name      = COALESCE(EXCLUDED.sw_l2_name, research.company_identity.sw_l2_name),
                    last_refreshed  = NOW()
            """),
            {
                "ts_code": ts_code, "name": name, "exchange": exchange,
                "market": market, "list_date": list_date, "list_status": list_status,
                "sw_l1_code": sw_l1_code, "sw_l1_name": sw_l1_name,
                "sw_l2_code": sw_l2_code, "sw_l2_name": sw_l2_name,
            },
        )
