"""SmartMoney backtest runner — DB persistence + orchestration entry point.

Responsibilities:
  1. Load active params (or named version) from DB / default.yaml.
  2. Call engine.run_backtest() with those params.
  3. Insert a row into smartmoney.backtest_runs.
  4. Batch-insert metric rows into smartmoney.backtest_metrics.
  5. Update backtest_runs.status to 'succeeded' / 'failed'.
  6. Return (BacktestResult, backtest_run_id) for downstream use
     (e.g. `ifa smartmoney params freeze --from-backtest <id>`).

All DB writes are wrapped in explicit transactions; failures set
backtest_runs.status = 'failed' without re-raising so the CLI can
still print a human-readable summary.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .engine import BacktestResult, FactorMetricsResult, MLWalkforwardResult, run_backtest
from ..params.store import get_active_params, get_params_by_name, load_default_params

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _insert_backtest_run(
    engine: Engine,
    *,
    run_id: uuid.UUID,
    start: dt.date,
    end: dt.date,
    params: dict[str, Any],
    param_version: str | None,
    notes: str | None,
) -> None:
    sql = text(f"""
        INSERT INTO {SCHEMA}.backtest_runs
            (backtest_run_id, start_date, end_date,
             params_json, param_version_used, status, notes)
        VALUES
            (:rid, :start, :end,
             cast(:pjson AS jsonb), :pver, 'running', :notes)
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "rid": str(run_id),
            "start": start,
            "end": end,
            "pjson": json.dumps(params, ensure_ascii=False, default=str),
            "pver": param_version,
            "notes": notes,
        })


def _update_backtest_status(
    engine: Engine,
    *,
    run_id: uuid.UUID,
    status: str,
) -> None:
    sql = text(f"""
        UPDATE {SCHEMA}.backtest_runs
        SET status = :status, completed_at = now()
        WHERE backtest_run_id = :rid
    """)
    with engine.begin() as conn:
        conn.execute(sql, {"status": status, "rid": str(run_id)})


def _insert_factor_metrics(
    engine: Engine,
    *,
    run_id: uuid.UUID,
    result: FactorMetricsResult,
) -> None:
    """Insert IC / RankIC / TopN / group return rows for one factor+window."""
    rows: list[dict[str, Any]] = []

    def _row(metric_name: str, value: float, group_label: str = "") -> dict:
        return {
            "run_id": str(run_id),
            "factor": result.factor_name,
            "metric": metric_name,
            "window": result.window_days,
            "group": group_label,
            "value": None if value != value else float(value),  # nan → None
            "n": result.n_samples,
        }

    rows.extend([
        _row("ic", result.ic_mean),
        _row("ic_std", result.ic_std),
        _row("ic_ir", result.ic_ir),
        _row("ic_positive_rate", result.ic_positive_rate),
        _row("rank_ic", result.rank_ic_mean),
        _row("rank_ic_std", result.rank_ic_std),
        _row("rank_ic_ir", result.rank_ic_ir),
        _row("topn_hit", result.topn_hit_rate),
    ])
    for grp_lbl, grp_val in result.group_returns.items():
        rows.append(_row("group_return", grp_val, group_label=grp_lbl))

    sql = text(f"""
        INSERT INTO {SCHEMA}.backtest_metrics
            (backtest_run_id, factor_name, metric_name,
             window_days, group_label, metric_value, n_samples)
        VALUES
            (:run_id, :factor, :metric,
             :window, :group, :value, :n)
        ON CONFLICT (backtest_run_id, factor_name, metric_name, window_days, group_label)
        DO UPDATE SET metric_value = EXCLUDED.metric_value, n_samples = EXCLUDED.n_samples
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)


def _insert_ml_metrics(
    engine: Engine,
    *,
    run_id: uuid.UUID,
    ml: MLWalkforwardResult,
) -> None:
    rows = [
        {
            "run_id": str(run_id), "factor": f"ml_{ml.model_name}",
            "metric": "auc_mean", "window": 1, "group": "",
            "value": None if ml.mean_auc != ml.mean_auc else ml.mean_auc,
            "n": ml.n_pred_rows,
        },
        {
            "run_id": str(run_id), "factor": f"ml_{ml.model_name}",
            "metric": "auc_std", "window": 1, "group": "",
            "value": None if ml.std_auc != ml.std_auc else ml.std_auc,
            "n": ml.n_steps,
        },
    ]
    sql = text(f"""
        INSERT INTO {SCHEMA}.backtest_metrics
            (backtest_run_id, factor_name, metric_name,
             window_days, group_label, metric_value, n_samples)
        VALUES
            (:run_id, :factor, :metric, :window, :group, :value, :n)
        ON CONFLICT (backtest_run_id, factor_name, metric_name, window_days, group_label)
        DO UPDATE SET metric_value = EXCLUDED.metric_value, n_samples = EXCLUDED.n_samples
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)


# ── Public entry point ────────────────────────────────────────────────────────

def run_smartmoney_backtest(
    engine: Engine,
    *,
    start: dt.date,
    end: dt.date,
    param_version: str | None = None,
    run_ml_walkforward: bool = True,
    forward_windows: tuple[int, ...] = (1, 5),
    topn: int = 5,
    notes: str | None = None,
    on_log: Any = None,
) -> tuple[BacktestResult, str]:
    """Run backtest and persist results to DB.

    Args:
        engine:            SQLAlchemy engine.
        start:             Start date (inclusive).
        end:               End date (inclusive).
        param_version:     Named param version (loaded from DB). None → active or default.yaml.
        run_ml_walkforward: Whether to include walk-forward ML AUC evaluation.
        forward_windows:   Forward return windows in trading days to evaluate.
        topn:              N for top-N hit rate.
        notes:             Human-readable notes attached to the backtest_runs row.
        on_log:            Optional callable(str) for progress logging.

    Returns:
        (BacktestResult, backtest_run_id_str)

    Raises:
        RuntimeError: If the engine fails to load data or the DB insert fails.
    """
    def _emit(msg: str) -> None:
        if on_log:
            on_log(msg)
        log.info(msg)

    # ── Load params ─────────────────────────────────────────────────────────
    if param_version:
        params = get_params_by_name(engine, param_version)
        if params is None:
            _emit(f"[runner] param version '{param_version}' not found; using default.yaml")
            params = load_default_params()
            param_version = None
    else:
        params = get_active_params(engine)

    _emit(f"[runner] param version: {param_version or '(active/default)'}")

    # ── Create backtest_runs row ────────────────────────────────────────────
    run_id = uuid.uuid4()
    _emit(f"[runner] backtest_run_id = {run_id}")

    try:
        _insert_backtest_run(
            engine,
            run_id=run_id,
            start=start,
            end=end,
            params=params,
            param_version=param_version,
            notes=notes,
        )
    except Exception as exc:
        log.error("[runner] failed to insert backtest_runs row: %s", exc)
        raise RuntimeError(f"DB insert failed: {exc}") from exc

    # ── Run backtest engine ─────────────────────────────────────────────────
    try:
        result = run_backtest(
            engine,
            start=start,
            end=end,
            params=params,
            forward_windows=forward_windows,
            topn=topn,
            run_ml_walkforward=run_ml_walkforward,
            on_log=on_log,
        )
    except Exception as exc:
        log.error("[runner] backtest engine failed: %s", exc)
        _update_backtest_status(engine, run_id=run_id, status="failed")
        raise

    # ── Persist metrics ─────────────────────────────────────────────────────
    persist_errors = 0
    for fr in result.factor_results:
        try:
            _insert_factor_metrics(engine, run_id=run_id, result=fr)
        except Exception as exc:  # noqa: BLE001
            log.warning("[runner] metric insert failed for %s/%dd: %s", fr.factor_name, fr.window_days, exc)
            persist_errors += 1

    for ml in result.ml_results:
        try:
            _insert_ml_metrics(engine, run_id=run_id, ml=ml)
        except Exception as exc:  # noqa: BLE001
            log.warning("[runner] ML metric insert failed for %s: %s", ml.model_name, exc)
            persist_errors += 1

    final_status = "succeeded" if persist_errors == 0 else "partial"
    _update_backtest_status(engine, run_id=run_id, status=final_status)
    _emit(f"[runner] completed: status={final_status}, run_id={run_id}")

    return result, str(run_id)


# ── Query helpers ─────────────────────────────────────────────────────────────

def list_backtest_runs(engine: Engine, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent backtest runs as list of dicts (no metrics blob)."""
    sql = text(f"""
        SELECT backtest_run_id::text, started_at, completed_at,
               start_date, end_date, param_version_used, status, notes
        FROM {SCHEMA}.backtest_runs
        ORDER BY started_at DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"limit": limit}).fetchall()
    return [
        {
            "run_id": r[0], "started_at": r[1], "completed_at": r[2],
            "start_date": r[3], "end_date": r[4],
            "param_version": r[5], "status": r[6], "notes": r[7],
        }
        for r in rows
    ]


def get_backtest_metrics(
    engine: Engine,
    backtest_run_id: str,
) -> list[dict[str, Any]]:
    """Return all metric rows for a given backtest run."""
    sql = text(f"""
        SELECT factor_name, metric_name, window_days, group_label, metric_value, n_samples
        FROM {SCHEMA}.backtest_metrics
        WHERE backtest_run_id = :rid
        ORDER BY factor_name, metric_name, window_days, group_label
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"rid": backtest_run_id}).fetchall()
    return [
        {
            "factor_name": r[0], "metric_name": r[1],
            "window_days": r[2], "group_label": r[3],
            "metric_value": float(r[4]) if r[4] is not None else None,
            "n_samples": r[5],
        }
        for r in rows
    ]
