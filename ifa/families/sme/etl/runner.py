"""SME MVP-1 orchestration."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text

from ifa.families.sme.data.calendar import latest_trade_date, trading_dates
from ifa.families.sme.analysis.market_structure import build_market_structure_snapshot, persist_market_structure_snapshot
from ifa.families.sme.analysis.strategy_eval import compute_strategy_eval
from ifa.families.sme.db.audit import audit_sources, audit_storage, finish_run, new_run_id, start_run
from ifa.families.sme.features.diffusion import compute_diffusion_range
from ifa.families.sme.features.membership import compute_membership
from ifa.families.sme.features.sector_orderflow import compute_sector_orderflow
from ifa.families.sme.features.state_machine import compute_state_range
from ifa.families.sme.features.stock_orderflow import compute_stock_orderflow
from ifa.families.sme.labels.forward import compute_labels
from ifa.families.sme.versions import logic_versions


def _latest_successful_versions_covering(engine, trade_date: dt.date) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT quality_summary_json
            FROM sme.sme_etl_runs
            WHERE status = 'success'
              AND start_date <= :d
              AND end_date >= :d
              AND quality_summary_json ? 'logic_versions'
            ORDER BY finished_at DESC NULLS LAST, started_at DESC
            LIMIT 1
        """), {"d": trade_date}).scalar_one_or_none()
    if not row:
        return None
    return dict(row.get("logic_versions") or {})


def compute_day(engine, trade_date: dt.date, *, source_mode: str, run_id: str) -> dict[str, int]:
    rows: dict[str, int] = {}
    rows["membership"] = compute_membership(engine, start=trade_date, end=trade_date, source_mode=source_mode, run_id=run_id)
    rows["stock_orderflow"] = compute_stock_orderflow(engine, trade_date=trade_date, source_mode=source_mode, run_id=run_id)
    rows["sector_orderflow"] = compute_sector_orderflow(engine, trade_date=trade_date, source_mode=source_mode, run_id=run_id)
    return rows


def backfill(engine, *, start: dt.date, end: dt.date, run_mode: str = "manual", source_mode: str = "prefer_smartmoney", run_id: str | None = None, include_labels: bool = True) -> dict:
    run_id = run_id or new_run_id("SME-BACKFILL")
    start_run(engine, run_id=run_id, run_mode=run_mode, source_mode=source_mode, start=start, end=end, as_of=end)
    row_counts: dict[str, int] = {}
    try:
        audit_counts = audit_sources(engine, start=start, end=end)
        for k, v in audit_counts.items():
            row_counts[f"audit:{k}"] = v

        dates = trading_dates(engine, start, end)
        for d in dates:
            day_counts = compute_day(engine, d, source_mode=source_mode, run_id=run_id)
            for k, v in day_counts.items():
                row_counts[k] = row_counts.get(k, 0) + v

        if dates:
            row_counts["diffusion"] = compute_diffusion_range(engine, start=dates[0], end=dates[-1])
            row_counts["state"] = compute_state_range(engine, start=dates[0], end=dates[-1])

        if include_labels and dates:
            # Labels can only mature when enough future dates exist; the SQL itself
            # drops immature rows.
            row_counts["labels"] = compute_labels(engine, start=start, end=end)

        market_structure_rows = 0
        for d in dates:
            snapshot = build_market_structure_snapshot(engine, trade_date=d)
            market_structure_rows += persist_market_structure_snapshot(engine, snapshot)
        row_counts["market_structure"] = market_structure_rows
        if include_labels and dates:
            row_counts["strategy_eval"] = compute_strategy_eval(engine, start=dates[0], end=dates[-1])

        storage = audit_storage(engine, audit_date=end)
        row_counts["storage_total_bytes"] = int(storage["total_bytes"])
        finish_run(engine, run_id=run_id, status="success", row_counts=row_counts, quality_summary={"logic_versions": logic_versions()})
        return {"status": "success", "run_id": run_id, "row_counts": row_counts}
    except Exception as exc:
        finish_run(engine, run_id=run_id, status="failed", row_counts=row_counts, errors={"error": f"{type(exc).__name__}: {exc}"}, quality_summary={"logic_versions": logic_versions()})
        raise


def _core_tables_complete(engine, trade_date: dt.date) -> bool:
    tables = (
        "sme_sw_member_daily",
        "sme_stock_orderflow_daily",
        "sme_sector_orderflow_daily",
        "sme_sector_diffusion_daily",
        "sme_sector_state_daily",
        "sme_market_structure_daily",
    )
    with engine.connect() as conn:
        for table in tables:
            rows = conn.execute(text(f"SELECT COUNT(*) FROM sme.{table} WHERE trade_date = :d"), {"d": trade_date}).scalar_one()
            if int(rows) == 0:
                return False
    return True


def incremental(engine, *, as_of: dt.date | None = None, run_mode: str = "production", source_mode: str = "prefer_smartmoney", run_id: str | None = None, include_labels: bool = True, force: bool = False) -> dict:
    target = as_of or latest_trade_date(engine)
    existing_versions = _latest_successful_versions_covering(engine, target)
    expected_versions = logic_versions()
    if not force and _core_tables_complete(engine, target) and existing_versions == expected_versions:
        return {
            "status": "no_op",
            "as_of_trade_date": target,
            "reason": "core SME tables already contain rows for as_of; pass --force to recompute",
            "quality_summary": {"logic_versions": expected_versions},
        }
    return backfill(engine, start=target, end=target, run_mode=run_mode, source_mode=source_mode, run_id=run_id or new_run_id("SME-INCR"), include_labels=include_labels)
