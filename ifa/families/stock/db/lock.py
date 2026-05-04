"""Distributed analysis lock for Stock Intel — prevents duplicate concurrent runs.

Lock key: "{analysis_type}:{ts_code}:{data_cutoff_date}"
Lock TTL: 5 minutes (stale cleanup applies if record stays 'running' >5 min)

Usage:
    lock = acquire_or_wait(engine, ts_code='001339.SZ',
                           analysis_type='fast',
                           data_cutoff_date=date.today())
    if lock.is_holder:
        try:
            ... run analysis ...
            release_lock(engine, lock.lock_key)
        except Exception:
            release_lock(engine, lock.lock_key)
            raise
    else:
        # Another runner completed this analysis — caller can fetch cached result
        pass
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

log = logging.getLogger(__name__)

_LOCK_TTL_MINUTES = 5
_STALE_RUNNING_MINUTES = 5
_POLL_INTERVAL_SEC = 2


@dataclass
class LockResult:
    is_holder: bool
    lock_key: str
    record_id: uuid.UUID | None


def acquire_or_wait(
    engine: Engine,
    ts_code: str,
    analysis_type: str,
    data_cutoff_date: date,
    *,
    max_wait_sec: int = 300,
) -> LockResult:
    """Try to acquire the analysis lock, waiting up to *max_wait_sec* for an ongoing run.

    Returns LockResult(is_holder=True) if we acquired the lock.
    Returns LockResult(is_holder=False) if another holder finished (caller should use cache).
    Raises TimeoutError if max_wait_sec elapses without resolution.
    """
    lock_key = f"{analysis_type}:{ts_code}:{data_cutoff_date}"
    new_record_id = uuid.uuid4()
    waited_since = datetime.now(tz=timezone.utc)
    expires_at = datetime.now(tz=timezone.utc) + timedelta(minutes=_LOCK_TTL_MINUTES)

    while True:
        # ── Attempt to acquire ────────────────────────────────────────────
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO stock.analysis_lock
                            (lock_key, holder_record_id, acquired_at, expires_at)
                        VALUES (:k, :rid, NOW(), :exp)
                    """),
                    {"k": lock_key, "rid": str(new_record_id), "exp": expires_at},
                )
            log.debug("lock acquired: %s → %s", lock_key, new_record_id)
            return LockResult(is_holder=True, lock_key=lock_key, record_id=new_record_id)
        except IntegrityError:
            pass  # lock held by someone else

        # ── Inspect existing lock ─────────────────────────────────────────
        now = datetime.now(tz=timezone.utc)
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT l.holder_record_id, l.acquired_at, l.expires_at,
                           r.status
                    FROM stock.analysis_lock l
                    LEFT JOIN stock.analysis_record r ON l.holder_record_id = r.record_id
                    WHERE l.lock_key = :k
                """),
                {"k": lock_key},
            ).fetchone()

        if row is None:
            # Lock was released between our INSERT attempt and this check — retry
            continue

        holder_record_id, acquired_at, lock_expires_at, holder_status = row

        # Make timezone-aware
        if acquired_at and acquired_at.tzinfo is None:
            acquired_at = acquired_at.replace(tzinfo=timezone.utc)
        if lock_expires_at and lock_expires_at.tzinfo is None:
            lock_expires_at = lock_expires_at.replace(tzinfo=timezone.utc)

        # ── Stale detection ───────────────────────────────────────────────
        is_expired = lock_expires_at is not None and lock_expires_at < now
        is_running_too_long = (
            holder_status == "running"
            and acquired_at is not None
            and (now - acquired_at).total_seconds() > _STALE_RUNNING_MINUTES * 60
        )

        if is_expired or is_running_too_long:
            _cleanup_stale(engine, holder_record_id, lock_key)
            continue  # retry acquire

        # ── Check if holder finished (we can use cache) ───────────────────
        if holder_status in ("succeeded", "partial", "cached"):
            log.debug("lock: holder finished with %s — using cache for %s", holder_status, lock_key)
            return LockResult(is_holder=False, lock_key=lock_key, record_id=None)

        # ── Wait ──────────────────────────────────────────────────────────
        elapsed = (datetime.now(tz=timezone.utc) - waited_since).total_seconds()
        if elapsed >= max_wait_sec:
            raise TimeoutError(
                f"Waited {max_wait_sec}s for lock {lock_key!r} — still running. "
                "The holder may be slow or stuck. Try again later."
            )
        time.sleep(_POLL_INTERVAL_SEC)


def release_lock(engine: Engine, lock_key: str) -> None:
    """Delete the lock row (call after analysis completes or fails)."""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM stock.analysis_lock WHERE lock_key = :k"),
            {"k": lock_key},
        )
    log.debug("lock released: %s", lock_key)


def _cleanup_stale(engine: Engine, holder_record_id: str | None, lock_key: str) -> None:
    """Mark stale analysis_record as failed and delete the lock."""
    log.warning("cleaning up stale run: record=%s lock=%s", holder_record_id, lock_key)
    with engine.begin() as conn:
        if holder_record_id:
            conn.execute(
                text("""
                    UPDATE stock.analysis_record
                    SET status = 'failed',
                        error_summary = 'stale_run_cleanup'
                    WHERE record_id = :rid AND status = 'running'
                """),
                {"rid": holder_record_id},
            )
        conn.execute(
            text("DELETE FROM stock.analysis_lock WHERE lock_key = :k"),
            {"k": lock_key},
        )


def cleanup_all_stale(engine: Engine) -> int:
    """Cleanup all expired/stale locks (call on startup or periodically)."""
    now = datetime.now(tz=timezone.utc)
    stale_cutoff = now - timedelta(minutes=_STALE_RUNNING_MINUTES)

    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT l.lock_key, l.holder_record_id, r.status, l.expires_at
                FROM stock.analysis_lock l
                LEFT JOIN stock.analysis_record r ON l.holder_record_id = r.record_id
                WHERE l.expires_at < :now
                   OR (r.status = 'running' AND l.acquired_at < :cutoff)
            """),
            {"now": now, "cutoff": stale_cutoff},
        ).fetchall()

    for lock_key, holder_record_id, _status, _expires in rows:
        _cleanup_stale(engine, holder_record_id, lock_key)

    return len(rows)
