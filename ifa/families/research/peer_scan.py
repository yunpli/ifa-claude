"""Peer universe scan — populate research.factor_value for SW L2 cohorts.

Why this module exists:
  · peer.py needs ≥`min_peer_count` (default 8) same-L2 stocks with persisted
    factor_value rows to compute a percentile. Without this scan, peer rank is
    always None.
  · We don't want to wedge the smoketest path on hundreds of API calls. This
    module is intentionally a separate, idempotent batch operation.

Pipeline per peer:
  1. stock_basic → upsert research.company_identity (so resolver can find them)
  2. fetch_all → research.api_cache (TTL'd; cache hits skip the network)
  3. load_company_snapshot → CompanyFinancialSnapshot
  4. compute_<family> for all 5 → list[FactorResult]
  5. persist_all_families → research.factor_value (upsert)

Skip rules (freshness):
  · If a stock already has ≥`min_factors_for_fresh` rows in factor_value
    written within `max_age_hours`, we treat it as fresh and skip.

Resilience:
  · Each peer wrapped in try/except — a Tushare hiccup on one stock does not
    kill the batch. Failures are collected and returned in ScanResult.
  · `max_peers` caps work for huge L2s (some have 100+ stocks).
"""
from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.report.timezones import bjt_now
from ifa.families.research.analyzer.balance import compute_balance
from ifa.families.research.analyzer.cash_quality import compute_cash_quality
from ifa.families.research.analyzer.data import load_company_snapshot
from ifa.families.research.analyzer.factors import load_params
from ifa.families.research.analyzer.governance import compute_governance
from ifa.families.research.analyzer.growth import compute_growth
from ifa.families.research.analyzer.persistence import persist_all_families
from ifa.families.research.analyzer.profitability import compute_profitability
from ifa.families.research.fetcher.client import fetch_all, fetch_stock_basic
from ifa.families.research.resolver import CompanyRef, upsert_company_identity

log = logging.getLogger(__name__)


class _DelistedError(Exception):
    """Sentinel: stock_basic returned empty — ticker is delisted/suspended.

    Distinguished from generic Exception so the orchestrator can categorize
    it as a clean skip rather than an audit-noise 'failed'.
    """


# Expected factor count across all 5 families (matches SPECS dicts):
#   profitability=6 + growth=4 + cash_quality=5 + balance=6 + governance=7 = 28
_EXPECTED_FACTOR_COUNT = 28


@dataclass
class ScanResult:
    target_ts_code: str
    sw_l2_code: str | None
    sw_l2_name: str | None
    members_total: int
    scanned: int = 0
    skipped_fresh: int = 0
    skipped_delisted: int = 0   # stock_basic returned empty → likely delisted
    failed: int = 0             # genuine errors (Tushare 5xx, DB issue, etc.)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (ts_code, error)
    delisted: list[str] = field(default_factory=list)  # ts_codes flagged as delisted

    def summary(self) -> str:
        delisted_str = f" delisted={self.skipped_delisted}" if self.skipped_delisted else ""
        return (
            f"L2 {self.sw_l2_code or '?'} ({self.sw_l2_name or '?'}): "
            f"members={self.members_total} scanned={self.scanned} "
            f"fresh_skipped={self.skipped_fresh}{delisted_str} failed={self.failed}"
        )


def scan_l2_universe(
    engine: Engine,
    target_ts_code: str,
    *,
    on_date: date | None = None,
    max_peers: int | None = 20,
    skip_fresh: bool = True,
    max_age_hours: int = 24,
    concurrency: int = 1,
    run_id: uuid.UUID | None = None,
) -> ScanResult:
    """Ensure factor_value rows exist for the target's SW L2 cohort.

    Args:
        target_ts_code: anchor stock; used to look up the SW L2 to scan.
        on_date: PIT month for SW membership; defaults to today.
        max_peers: cap how many stocks to process (None = all).
        skip_fresh: if True, skip stocks already computed within max_age_hours.
        max_age_hours: freshness window (default 24h).
        concurrency: number of worker threads for parallel scan (default 1 = serial).
            Tushare-friendly default. Going above 5 risks rate-limit pushback.
        run_id: optional UUID to record audit trail in research.scan_run.

    Returns:
        ScanResult with per-peer stats. Does not raise on individual failures.
    """
    # Use Beijing date for SW membership snapshot; running near midnight in
    # other timezones would otherwise pick the wrong snapshot_month.
    on_date = on_date or bjt_now().date()
    snapshot_month = on_date.replace(day=1)

    l2_code, l2_name = _lookup_target_l2(engine, target_ts_code, snapshot_month)
    if l2_code is None:
        return ScanResult(
            target_ts_code=target_ts_code,
            sw_l2_code=None, sw_l2_name=None,
            members_total=0,
        )

    members = _list_l2_members(engine, l2_code, snapshot_month)
    result = ScanResult(
        target_ts_code=target_ts_code,
        sw_l2_code=l2_code, sw_l2_name=l2_name,
        members_total=len(members),
    )

    # Make sure target is processed first/included in the budget.
    if target_ts_code in members:
        members.remove(target_ts_code)
        members.insert(0, target_ts_code)

    if max_peers is not None:
        members = members[:max_peers]

    if run_id is not None:
        _audit_start(engine, run_id, l2_code, l2_name, on_date, len(members),
                     {"concurrency": concurrency, "skip_fresh": skip_fresh,
                      "max_peers": max_peers, "anchor": target_ts_code})

    fresh_cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=max_age_hours)
    params = load_params()

    if concurrency <= 1:
        _run_serial(engine, members, on_date, params, fresh_cutoff,
                    skip_fresh, result)
    else:
        _run_concurrent(engine, members, on_date, params, fresh_cutoff,
                        skip_fresh, concurrency, result)

    if run_id is not None:
        _audit_finish(engine, run_id, l2_code, result)

    return result


def scan_universe(
    engine: Engine,
    *,
    anchor_ts_codes: list[str] | None = None,
    l2_codes: list[str] | None = None,
    on_date: date | None = None,
    max_peers: int | None = None,        # production default: NO cap
    skip_fresh: bool = True,
    max_age_hours: int = 24,
    concurrency: int = 3,
) -> "UniverseScanReport":
    """Multi-L2 production orchestrator.

    Picks L2s in this order:
      1. If `l2_codes` is given, use exactly those.
      2. Else if `anchor_ts_codes` is given, look up each anchor's L2.
      3. Else infer from research.company_identity (every distinct L2 of any
         stock we've ever resolved gets scanned).

    Each L2 gets its own scan_l2_universe call sharing one run_id so the
    audit trail can group them. Failures in one L2 don't affect others.
    """
    # Use Beijing date for SW membership snapshot; running near midnight in
    # other timezones would otherwise pick the wrong snapshot_month.
    on_date = on_date or bjt_now().date()
    run_id = uuid.uuid4()

    targeted_l2s = _resolve_targeted_l2s(
        engine, l2_codes=l2_codes, anchor_ts_codes=anchor_ts_codes,
        on_date=on_date,
    )

    log.info("scan_universe run_id=%s targeting %d L2 cohorts",
             run_id, len(targeted_l2s))

    started_at = datetime.now(tz=timezone.utc)
    l2_results: list[ScanResult] = []
    for i, (l2_code, anchor) in enumerate(targeted_l2s, start=1):
        log.info("══ [%d/%d] L2 %s (anchor=%s) ══", i, len(targeted_l2s),
                 l2_code, anchor)
        try:
            res = scan_l2_universe(
                engine, anchor,
                on_date=on_date,
                max_peers=max_peers,
                skip_fresh=skip_fresh,
                max_age_hours=max_age_hours,
                concurrency=concurrency,
                run_id=run_id,
            )
            l2_results.append(res)
            log.info("  → %s", res.summary())
        except Exception as e:
            log.error("L2 %s scan failed entirely: %s", l2_code, e)
            l2_results.append(ScanResult(
                target_ts_code=anchor, sw_l2_code=l2_code,
                sw_l2_name=None, members_total=0,
                failed=1, failures=[("__l2__", str(e)[:200])],
            ))

    return UniverseScanReport(
        run_id=run_id,
        started_at=started_at,
        completed_at=datetime.now(tz=timezone.utc),
        l2_results=l2_results,
    )


@dataclass
class UniverseScanReport:
    run_id: uuid.UUID
    started_at: datetime
    completed_at: datetime
    l2_results: list[ScanResult]

    @property
    def total_scanned(self) -> int:
        return sum(r.scanned for r in self.l2_results)

    @property
    def total_skipped(self) -> int:
        return sum(r.skipped_fresh for r in self.l2_results)

    @property
    def total_failed(self) -> int:
        return sum(r.failed for r in self.l2_results)

    def summary(self) -> str:
        elapsed = (self.completed_at - self.started_at).total_seconds()
        return (
            f"run_id={self.run_id} L2s={len(self.l2_results)} "
            f"scanned={self.total_scanned} fresh={self.total_skipped} "
            f"failed={self.total_failed} elapsed={elapsed:.0f}s"
        )


# ─── Internals ────────────────────────────────────────────────────────────────

def _lookup_target_l2(
    engine: Engine, ts_code: str, snapshot_month: date,
) -> tuple[str | None, str | None]:
    sql = text("""
        SELECT l2_code, l2_name
        FROM smartmoney.sw_member_monthly
        WHERE ts_code = :tc AND snapshot_month = :sm
        LIMIT 1
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"tc": ts_code, "sm": snapshot_month}).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _list_l2_members(
    engine: Engine, l2_code: str, snapshot_month: date,
) -> list[str]:
    sql = text("""
        SELECT DISTINCT ts_code
        FROM smartmoney.sw_member_monthly
        WHERE l2_code = :code AND snapshot_month = :sm
        ORDER BY ts_code
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"code": l2_code, "sm": snapshot_month}).fetchall()
    return [r[0] for r in rows]


def _is_fresh(engine: Engine, ts_code: str, cutoff: datetime) -> bool:
    """A stock is fresh if it has the full factor count computed after `cutoff`."""
    sql = text("""
        SELECT COUNT(*) FROM research.factor_value
        WHERE ts_code = :tc AND computed_at >= :cut
    """)
    with engine.connect() as conn:
        n = conn.execute(sql, {"tc": ts_code, "cut": cutoff}).scalar() or 0
    return n >= _EXPECTED_FACTOR_COUNT


def _process_peer(
    engine: Engine, ts_code: str, on_date: date, params: dict,
) -> None:
    # Step 1: identity (also tells us exchange for IRM routing)
    sb = fetch_stock_basic(engine, ts_code)
    if not sb:
        raise _DelistedError(f"stock_basic returned empty for {ts_code}")
    info = sb[0]
    name = str(info.get("name") or "")
    exchange = str(info.get("exchange") or "")
    list_status = info.get("list_status")

    upsert_company_identity(
        engine, ts_code=ts_code, name=name, exchange=exchange,
        market=info.get("market"), list_status=list_status,
        list_date=_parse_yyyymmdd(info.get("list_date")),
    )

    # Step 2: fetch (cached)
    fetch_all(engine, ts_code, exchange, verbose=False)

    # Step 3-5: compute + persist
    company = CompanyRef(ts_code=ts_code, name=name, exchange=exchange)
    snap = load_company_snapshot(engine, company, data_cutoff_date=on_date)
    results_by_family = {
        "profitability": compute_profitability(snap, params),
        "growth":        compute_growth(snap, params),
        "cash_quality":  compute_cash_quality(snap, params),
        "balance":       compute_balance(snap, params),
        "governance":    compute_governance(snap, params),
    }
    persist_all_families(engine, ts_code, results_by_family)


def _run_serial(
    engine, members, on_date, params, fresh_cutoff, skip_fresh, result,
) -> None:
    for i, ts_code in enumerate(members, start=1):
        try:
            if skip_fresh and _is_fresh(engine, ts_code, fresh_cutoff):
                result.skipped_fresh += 1
                log.debug("[%d/%d] %s — fresh, skip", i, len(members), ts_code)
                continue
            log.info("[%d/%d] scanning %s …", i, len(members), ts_code)
            _process_peer(engine, ts_code, on_date, params)
            result.scanned += 1
        except _DelistedError:
            result.skipped_delisted += 1
            result.delisted.append(ts_code)
            log.info("[%d/%d] %s — delisted, skip", i, len(members), ts_code)
        except Exception as exc:
            log.warning("peer scan failed for %s: %s", ts_code, exc)
            result.failed += 1
            result.failures.append((ts_code, str(exc)[:200]))


def _run_concurrent(
    engine, members, on_date, params, fresh_cutoff, skip_fresh,
    concurrency, result,
) -> None:
    """Process peers in parallel. Each worker uses its own DB connection from
    the engine pool; SQLAlchemy + psycopg connection pooling are thread-safe.
    """
    # First pass: cheaply filter out fresh stocks on the main thread (no API
    # calls needed). This avoids spawning workers for skipped stocks.
    pending: list[str] = []
    if skip_fresh:
        for ts_code in members:
            if _is_fresh(engine, ts_code, fresh_cutoff):
                result.skipped_fresh += 1
            else:
                pending.append(ts_code)
    else:
        pending = list(members)

    log.info("concurrent scan: %d pending, %d already fresh, workers=%d",
             len(pending), result.skipped_fresh, concurrency)

    if not pending:
        return

    def _work(ts_code: str) -> tuple[str, str | None, bool]:
        # Returns (ts_code, error_msg_or_None, is_delisted)
        try:
            _process_peer(engine, ts_code, on_date, params)
            return (ts_code, None, False)
        except _DelistedError:
            return (ts_code, None, True)
        except Exception as exc:
            return (ts_code, str(exc)[:200], False)

    completed = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(_work, tc): tc for tc in pending}
        for fut in as_completed(futures):
            ts_code, err, is_delisted = fut.result()
            completed += 1
            if is_delisted:
                result.skipped_delisted += 1
                result.delisted.append(ts_code)
                log.info("[%d/%d] ⊝ %s (delisted)", completed, len(pending), ts_code)
            elif err is None:
                result.scanned += 1
                log.info("[%d/%d] ✓ %s", completed, len(pending), ts_code)
            else:
                result.failed += 1
                result.failures.append((ts_code, err))
                log.warning("[%d/%d] ✗ %s: %s", completed, len(pending),
                            ts_code, err)


def _resolve_targeted_l2s(
    engine: Engine, *,
    l2_codes: list[str] | None,
    anchor_ts_codes: list[str] | None,
    on_date: date,
) -> list[tuple[str, str]]:
    """Decide which L2s to scan. Returns list of (l2_code, representative anchor)."""
    snapshot_month = on_date.replace(day=1)

    if l2_codes:
        # User explicitly named L2s; pick first member of each as anchor.
        out: list[tuple[str, str]] = []
        with engine.connect() as conn:
            for code in l2_codes:
                row = conn.execute(text("""
                    SELECT ts_code FROM smartmoney.sw_member_monthly
                    WHERE l2_code = :c AND snapshot_month = :sm
                    ORDER BY ts_code LIMIT 1
                """), {"c": code, "sm": snapshot_month}).fetchone()
                if row:
                    out.append((code, row[0]))
                else:
                    log.warning("L2 %s has no members at %s", code, snapshot_month)
        return out

    if anchor_ts_codes:
        out = []
        seen_l2: set[str] = set()
        for tc in anchor_ts_codes:
            l2_code, _ = _lookup_target_l2(engine, tc, snapshot_month)
            if l2_code and l2_code not in seen_l2:
                seen_l2.add(l2_code)
                out.append((l2_code, tc))
        return out

    # Infer from company_identity: every distinct L2 of any company we've ever
    # resolved gets scanned. Picks the first ts_code per L2 as anchor.
    sql = text("""
        SELECT DISTINCT ON (sm.l2_code) sm.l2_code, ci.ts_code
        FROM research.company_identity ci
        JOIN smartmoney.sw_member_monthly sm
          ON ci.ts_code = sm.ts_code AND sm.snapshot_month = :sm
        WHERE sm.l2_code IS NOT NULL
        ORDER BY sm.l2_code, ci.ts_code
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"sm": snapshot_month}).fetchall()
    return [(r[0], r[1]) for r in rows]


# ─── Audit (research.scan_run) ────────────────────────────────────────────────

def _audit_start(
    engine: Engine, run_id: uuid.UUID, l2_code: str, l2_name: str | None,
    on_date: date, members_total: int, config: dict,
) -> None:
    import json
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO research.scan_run
                (run_id, l2_code, l2_name, on_date, status,
                 members_total, config)
            VALUES (:rid, :l2, :ln, :od, 'running',
                    :mt, CAST(:cfg AS JSONB))
            ON CONFLICT (run_id, l2_code) DO NOTHING
        """), {
            "rid": str(run_id), "l2": l2_code, "ln": l2_name,
            "od": on_date, "mt": members_total,
            "cfg": json.dumps(config, ensure_ascii=False, default=str),
        })


def _audit_finish(
    engine: Engine, run_id: uuid.UUID, l2_code: str, result: ScanResult,
) -> None:
    import json
    # 'success' criterion: at least some stocks ended in a usable state
    # (scanned + fresh-skipped + delisted-skipped). Delisting is a known
    # condition, not a failure.
    completed_ok = result.scanned + result.skipped_fresh + result.skipped_delisted
    if result.failed and completed_ok == 0:
        status = "failed"
    elif result.failed:
        status = "partial"
    else:
        status = "succeeded"

    # Pack delisted info into the failures JSON for audit, prefixed for clarity.
    audit_failures: list[tuple[str, str]] = list(result.failures)
    for tc in result.delisted:
        audit_failures.append((tc, "delisted (stock_basic empty)"))

    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE research.scan_run
            SET completed_at = NOW(),
                status = :st,
                scanned = :sc,
                skipped_fresh = :sk,
                failed = :fl,
                failures = CAST(:fail AS JSONB)
            WHERE run_id = :rid AND l2_code = :l2
        """), {
            "st": status, "sc": result.scanned,
            "sk": result.skipped_fresh + result.skipped_delisted,
            "fl": result.failed,
            "fail": json.dumps(audit_failures, ensure_ascii=False) if audit_failures else None,
            "rid": str(run_id), "l2": l2_code,
        })


def _parse_yyyymmdd(raw: object) -> date | None:
    s = str(raw or "")
    if len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None
