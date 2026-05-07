"""SME audit writers."""
from __future__ import annotations

import datetime as dt
import json
from uuid import uuid4

from sqlalchemy import text

from ifa.families.sme.data.source_resolver import SMARTMONEY_SOURCES


def new_run_id(prefix: str = "SME") -> str:
    return f"{prefix}-{dt.datetime.utcnow():%Y%m%d%H%M%S}-{uuid4().hex[:8]}"


def start_run(engine, *, run_id: str, run_mode: str, source_mode: str, start: dt.date | None, end: dt.date | None, as_of: dt.date | None) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO sme.sme_etl_runs (
                run_id, run_mode, source_mode, as_of_trade_date, start_date, end_date, status
            ) VALUES (:run_id, :run_mode, :source_mode, :as_of, :start, :end, 'running')
            ON CONFLICT (run_id) DO NOTHING
        """), {"run_id": run_id, "run_mode": run_mode, "source_mode": source_mode, "as_of": as_of, "start": start, "end": end})


def finish_run(engine, *, run_id: str, status: str, row_counts: dict[str, int], errors: dict | None = None, quality_summary: dict | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE sme.sme_etl_runs
               SET finished_at = now(),
                   status = :status,
                   row_counts_json = CAST(:rows AS JSONB),
                   quality_summary_json = CAST(:quality AS JSONB),
                   error_json = CAST(:errors AS JSONB)
             WHERE run_id = :run_id
        """), {
            "run_id": run_id,
            "status": status,
            "rows": json.dumps(row_counts),
            "quality": json.dumps(quality_summary or {}),
            "errors": json.dumps(errors or {}),
        })


def audit_sources(engine, *, start: dt.date, end: dt.date) -> dict[str, int]:
    row_counts: dict[str, int] = {}
    with engine.begin() as conn:
        for logical, src in SMARTMONEY_SOURCES.items():
            date_col = "snapshot_month" if logical == "sw_member_monthly" else "trade_date"
            if logical == "sw_member":
                # raw_sw_member is interval-based, not daily. Audit as one row on end.
                rows = conn.execute(text(f"SELECT COUNT(*) FROM {src.fqtn}")).scalar_one()
                stock_count = conn.execute(text(f"SELECT COUNT(DISTINCT ts_code) FROM {src.fqtn}")).scalar_one()
                conn.execute(text("""
                    INSERT INTO sme.sme_source_audit_daily (
                        trade_date, source_name, source_schema, source_table, row_count,
                        distinct_stock_count, coverage_status, computed_at
                    ) VALUES (:d, :name, :schema, :table, :rows, :stocks, :status, now())
                    ON CONFLICT (trade_date, source_name) DO UPDATE SET
                        row_count = EXCLUDED.row_count,
                        distinct_stock_count = EXCLUDED.distinct_stock_count,
                        coverage_status = EXCLUDED.coverage_status,
                        computed_at = now()
                """), {"d": end, "name": logical, "schema": src.schema, "table": src.table, "rows": rows, "stocks": stock_count, "status": "ok" if rows else "blocked"})
                row_counts[logical] = int(rows)
                continue

            rows = conn.execute(text(f"""
                SELECT {date_col}, COUNT(*) AS n
                FROM {src.fqtn}
                WHERE {date_col} BETWEEN :start AND :end
                GROUP BY {date_col}
            """), {"start": start, "end": end}).fetchall()
            for d, n in rows:
                conn.execute(text("""
                    INSERT INTO sme.sme_source_audit_daily (
                        trade_date, source_name, source_schema, source_table, row_count,
                        coverage_status, computed_at
                    ) VALUES (:d, :name, :schema, :table, :rows, :status, now())
                    ON CONFLICT (trade_date, source_name) DO UPDATE SET
                        row_count = EXCLUDED.row_count,
                        coverage_status = EXCLUDED.coverage_status,
                        computed_at = now()
                """), {"d": d, "name": logical, "schema": src.schema, "table": src.table, "rows": n, "status": "ok" if n else "blocked"})
                row_counts[logical] = row_counts.get(logical, 0) + int(n)
    return row_counts


def audit_storage(engine, *, audit_date: dt.date | None = None) -> dict[str, int]:
    audit_date = audit_date or dt.date.today()
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT c.relname,
                   pg_total_relation_size(c.oid) AS total_bytes,
                   pg_relation_size(c.oid) AS table_bytes,
                   pg_indexes_size(c.oid) AS index_bytes,
                   COALESCE(s.n_live_tup, 0)::bigint AS row_count
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
              LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
             WHERE n.nspname = 'sme' AND c.relkind = 'r'
        """)).fetchall()
        total = 0
        for table, total_bytes, table_bytes, index_bytes, row_count in rows:
            total += int(total_bytes or 0)
            status = "block" if total > 10 * 1024**3 else ("warn" if total > 8 * 1024**3 else "ok")
            conn.execute(text("""
                INSERT INTO sme.sme_storage_audit_daily (
                    audit_date, schema_name, table_name, row_count, total_bytes,
                    table_bytes, index_bytes, storage_status, computed_at
                ) VALUES (:d, 'sme', :table, :rows, :total, :table_bytes, :index_bytes, :status, now())
                ON CONFLICT (audit_date, schema_name, table_name) DO UPDATE SET
                    row_count = EXCLUDED.row_count,
                    total_bytes = EXCLUDED.total_bytes,
                    table_bytes = EXCLUDED.table_bytes,
                    index_bytes = EXCLUDED.index_bytes,
                    storage_status = EXCLUDED.storage_status,
                    computed_at = now()
            """), {"d": audit_date, "table": table, "rows": row_count, "total": total_bytes, "table_bytes": table_bytes, "index_bytes": index_bytes, "status": status})
    return {"total_bytes": total, "total_gb": round(total / 1024**3, 3)}
