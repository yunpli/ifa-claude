"""SmartMoney parameter version store — CRUD for smartmoney.param_versions.

Workflow:
  1. During development / backtest: code uses load_default_params() from default.yaml.
  2. After backtest optimization: freeze best params with freeze_params().
  3. In production: get_active_params(engine) → active DB version or default.yaml fallback.
  4. Old versions: archive_params(engine, version_name) marks them 'archived'.

Table: smartmoney.param_versions
  version_id   UUID PK
  version_name TEXT UNIQUE  (e.g. 'v2026_04', 'default')
  params_json  JSONB
  frozen_at    TIMESTAMPTZ
  frozen_from_backtest_run_id  UUID (FK to backtest_runs)
  status       TEXT  CHECK ('active', 'archived', 'draft')
  notes        TEXT
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"
_DEFAULT_YAML = Path(__file__).with_name("default.yaml")

# Module-level cache so we don't re-read the file on every call
_default_cache: dict[str, Any] | None = None


# ── Load default.yaml ─────────────────────────────────────────────────────────

def load_default_params() -> dict[str, Any]:
    """Return the default parameter dict loaded from default.yaml.

    Result is cached in-process; safe to call frequently.
    """
    global _default_cache
    if _default_cache is None:
        with open(_DEFAULT_YAML, encoding="utf-8") as fh:
            _default_cache = yaml.safe_load(fh)
    return dict(_default_cache)  # shallow copy; don't mutate the cache


# ── Read from DB ──────────────────────────────────────────────────────────────

def get_active_params(engine: Engine) -> dict[str, Any]:
    """Return the active param version from the DB, or default.yaml if none.

    Prefers the most recently frozen 'active' version.
    Falls back to default.yaml if the table is empty or the DB is unreachable.
    """
    sql = text(f"""
        SELECT params_json
        FROM {SCHEMA}.param_versions
        WHERE status = 'active'
        ORDER BY frozen_at DESC
        LIMIT 1
    """)
    try:
        with engine.connect() as conn:
            row = conn.execute(sql).fetchone()
        if row and row[0]:
            params = dict(row[0])  # JSONB → dict
            log.debug("[params] loaded active params from DB")
            return params
    except Exception as exc:  # noqa: BLE001
        log.warning("[params] could not load from DB (%s); using default.yaml", exc)

    log.debug("[params] no active DB params; using default.yaml")
    return load_default_params()


def get_params_by_name(engine: Engine, version_name: str) -> dict[str, Any] | None:
    """Return params for a specific version_name, or None if not found."""
    sql = text(f"""
        SELECT params_json
        FROM {SCHEMA}.param_versions
        WHERE version_name = :name
        LIMIT 1
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"name": version_name}).fetchone()
    if row and row[0]:
        return dict(row[0])
    return None


def list_param_versions(engine: Engine) -> list[dict[str, Any]]:
    """Return all param versions (excluding params_json blob) as a list of dicts."""
    sql = text(f"""
        SELECT version_id::text, version_name, status,
               frozen_at, frozen_from_backtest_run_id::text, notes
        FROM {SCHEMA}.param_versions
        ORDER BY frozen_at DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [
        {
            "version_id": r[0],
            "version_name": r[1],
            "status": r[2],
            "frozen_at": r[3],
            "backtest_run_id": r[4],
            "notes": r[5],
        }
        for r in rows
    ]


# ── Write to DB ───────────────────────────────────────────────────────────────

def freeze_params(
    engine: Engine,
    *,
    version_name: str,
    params: dict[str, Any] | None = None,
    backtest_run_id: str | None = None,
    notes: str | None = None,
    make_active: bool = True,
) -> str:
    """Freeze a parameter set into the DB as a new version.

    Args:
        engine:          SQLAlchemy engine.
        version_name:    Unique name, e.g. 'v2026_04'.
        params:          Params dict; if None, uses load_default_params().
        backtest_run_id: UUID of the backtest run that produced these params.
        notes:           Human-readable notes.
        make_active:     If True, archive the current active version first.

    Returns:
        The UUID of the new param_versions row (as str).
    """
    if params is None:
        params = load_default_params()

    if make_active:
        _archive_current_active(engine)

    new_id = str(uuid.uuid4())
    sql = text(f"""
        INSERT INTO {SCHEMA}.param_versions
            (version_id, version_name, params_json,
             frozen_from_backtest_run_id, status, notes)
        VALUES
            (:vid, :name, cast(:params_json AS jsonb),
             :bt_id, :status, :notes)
        ON CONFLICT (version_name) DO UPDATE SET
            params_json                  = EXCLUDED.params_json,
            frozen_from_backtest_run_id  = EXCLUDED.frozen_from_backtest_run_id,
            frozen_at                    = now(),
            status                       = EXCLUDED.status,
            notes                        = EXCLUDED.notes
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "vid": new_id,
            "name": version_name,
            "params_json": json.dumps(params, ensure_ascii=False),
            "bt_id": backtest_run_id,
            "status": "active" if make_active else "draft",
            "notes": notes,
        })

    log.info("[params] frozen version '%s' (id=%s, active=%s)", version_name, new_id, make_active)
    return new_id


def _archive_current_active(engine: Engine) -> None:
    """Set all currently 'active' versions to 'archived'."""
    sql = text(f"""
        UPDATE {SCHEMA}.param_versions
        SET status = 'archived'
        WHERE status = 'active'
    """)
    with engine.begin() as conn:
        conn.execute(sql)


def archive_params(engine: Engine, version_name: str) -> bool:
    """Manually archive a specific param version.

    Returns True if a row was updated.
    """
    sql = text(f"""
        UPDATE {SCHEMA}.param_versions
        SET status = 'archived'
        WHERE version_name = :name AND status != 'archived'
        RETURNING version_id
    """)
    with engine.begin() as conn:
        row = conn.execute(sql, {"name": version_name}).fetchone()
    if row:
        log.info("[params] archived version '%s'", version_name)
        return True
    log.warning("[params] version '%s' not found or already archived", version_name)
    return False


def delete_draft(engine: Engine, version_name: str) -> bool:
    """Delete a draft version (safety: only drafts can be deleted this way).

    Returns True if deleted.
    """
    sql = text(f"""
        DELETE FROM {SCHEMA}.param_versions
        WHERE version_name = :name AND status = 'draft'
        RETURNING version_id
    """)
    with engine.begin() as conn:
        row = conn.execute(sql, {"name": version_name}).fetchone()
    if row:
        log.info("[params] deleted draft '%s'", version_name)
        return True
    log.warning("[params] draft '%s' not found (or not a draft)", version_name)
    return False
