#!/usr/bin/env python
"""Staged Stock Edge validation diagnostics.

This script is deliberately read-only. It inventories 2021-2026 data coverage
and estimates the mature PIT stock-date label panel that a serious Stock Edge
validation pipeline can use. Tiny replay panels such as pit8/top30 are smoke
tests only; they must not be treated as parameter or algorithm validation.

The intended sequence is recent-6m validation first, then multiple 6m regime
windows, then full-history purged/walk-forward as the final YAML promotion gate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.db import get_engine

DEFAULT_START = dt.date(2021, 1, 1)
DEFAULT_OUTPUT_DIR = Path("/Users/neoclaw/claude/ifaenv/manifests/stock_edge_full_history_validation")
HORIZONS = (5, 10, 20)


def _date(value: str | dt.date | None, default: dt.date | None = None) -> dt.date:
    if value is None:
        if default is None:
            raise ValueError("date is required")
        return default
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(value)


def _table_exists(engine: Engine, schema: str, table: str) -> bool:
    with engine.connect() as c:
        return bool(c.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = :schema AND table_name = :table
            )
        """), {"schema": schema, "table": table}).scalar())


def _columns(engine: Engine, schema: str, table: str) -> set[str]:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
        """), {"schema": schema, "table": table}).all()
    return {str(r[0]) for r in rows}


def _first_existing_col(engine: Engine, schema: str, table: str, candidates: tuple[str, ...]) -> str | None:
    existing = _columns(engine, schema, table)
    for candidate in candidates:
        if candidate in existing:
            return candidate
    return None


def _latest_raw_daily(engine: Engine) -> dt.date:
    with engine.connect() as c:
        value = c.execute(text("SELECT MAX(trade_date) FROM smartmoney.raw_daily")).scalar()
    if value is None:
        raise SystemExit("smartmoney.raw_daily is empty; cannot diagnose Stock Edge history")
    return value


def _calendar_by_year(engine: Engine, start: dt.date, end: dt.date) -> dict[str, int]:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT EXTRACT(YEAR FROM cal_date)::int AS year, COUNT(*)::int AS open_days
            FROM smartmoney.trade_cal
            WHERE exchange = 'SSE'
              AND is_open = true
              AND cal_date BETWEEN :start AND :end
            GROUP BY 1
            ORDER BY 1
        """), {"start": start, "end": end}).mappings().all()
    return {str(r["year"]): int(r["open_days"]) for r in rows}


def _table_coverage(
    engine: Engine,
    *,
    schema: str,
    table: str,
    date_col: str,
    start: dt.date,
    end: dt.date,
    calendar_by_year: dict[str, int],
    code_col: str | None = "ts_code",
) -> dict[str, Any]:
    if not _table_exists(engine, schema, table):
        return {"exists": False}
    if code_col and code_col not in _columns(engine, schema, table):
        code_col = None
    code_sql = f"COUNT(DISTINCT {code_col})::int AS unique_codes" if code_col else "NULL::int AS unique_codes"
    with engine.connect() as c:
        rows = c.execute(text(f"""
            SELECT EXTRACT(YEAR FROM {date_col})::int AS year,
                   COUNT(*)::bigint AS rows,
                   COUNT(DISTINCT {date_col})::int AS dates,
                   {code_sql}
            FROM {schema}.{table}
            WHERE {date_col} BETWEEN :start AND :end
            GROUP BY 1
            ORDER BY 1
        """), {"start": start, "end": end}).mappings().all()
    by_year = {}
    for r in rows:
        year = str(r["year"])
        open_days = calendar_by_year.get(year, 0)
        dates = int(r["dates"] or 0)
        by_year[year] = {
            "rows": int(r["rows"] or 0),
            "dates": dates,
            "open_days": open_days,
            "date_coverage": round(dates / open_days, 6) if open_days else None,
            "unique_codes": int(r["unique_codes"]) if r["unique_codes"] is not None else None,
        }
    return {"exists": True, "by_year": by_year}


def _input_intersection(engine: Engine, start: dt.date, end: dt.date) -> dict[str, Any]:
    with engine.connect() as c:
        rows = c.execute(text("""
            WITH daily AS (
                SELECT trade_date, ts_code FROM smartmoney.raw_daily
                WHERE trade_date BETWEEN :start AND :end
            ),
            basic AS (
                SELECT trade_date, ts_code FROM smartmoney.raw_daily_basic
                WHERE trade_date BETWEEN :start AND :end
            ),
            moneyflow AS (
                SELECT trade_date, ts_code FROM smartmoney.raw_moneyflow
                WHERE trade_date BETWEEN :start AND :end
            ),
            joined AS (
                SELECT d.trade_date, d.ts_code,
                       (b.ts_code IS NOT NULL) AS has_basic,
                       (m.ts_code IS NOT NULL) AS has_moneyflow
                FROM daily d
                LEFT JOIN basic b USING (trade_date, ts_code)
                LEFT JOIN moneyflow m USING (trade_date, ts_code)
            )
            SELECT EXTRACT(YEAR FROM trade_date)::int AS year,
                   COUNT(*)::bigint AS raw_daily_rows,
                   COUNT(*) FILTER (WHERE has_basic)::bigint AS has_basic_rows,
                   COUNT(*) FILTER (WHERE has_moneyflow)::bigint AS has_moneyflow_rows,
                   COUNT(*) FILTER (WHERE has_basic AND has_moneyflow)::bigint AS core_intersection_rows,
                   COUNT(DISTINCT ts_code)::int AS raw_daily_codes,
                   COUNT(DISTINCT ts_code) FILTER (WHERE has_basic AND has_moneyflow)::int AS core_intersection_codes,
                   COUNT(DISTINCT trade_date)::int AS dates
            FROM joined
            GROUP BY 1
            ORDER BY 1
        """), {"start": start, "end": end}).mappings().all()
    by_year = {}
    total_core = 0
    for r in rows:
        year = str(r["year"])
        raw_rows = int(r["raw_daily_rows"] or 0)
        core_rows = int(r["core_intersection_rows"] or 0)
        total_core += core_rows
        by_year[year] = {
            "raw_daily_rows": raw_rows,
            "has_basic_rows": int(r["has_basic_rows"] or 0),
            "has_moneyflow_rows": int(r["has_moneyflow_rows"] or 0),
            "core_intersection_rows": core_rows,
            "core_intersection_rate": round(core_rows / raw_rows, 6) if raw_rows else None,
            "raw_daily_codes": int(r["raw_daily_codes"] or 0),
            "core_intersection_codes": int(r["core_intersection_codes"] or 0),
            "dates": int(r["dates"] or 0),
        }
    return {"by_year": by_year, "total_core_intersection_rows": total_core}


def _forward_label_capacity(engine: Engine, start: dt.date, end: dt.date, horizons: tuple[int, ...] = HORIZONS) -> dict[str, Any]:
    horizon_values = ",".join(f"({h})" for h in horizons)
    with engine.connect() as c:
        rows = c.execute(text(f"""
            WITH base AS (
                SELECT trade_date, ts_code, close,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date) AS rn
                FROM smartmoney.raw_daily
                WHERE trade_date >= :history_start
                  AND trade_date <= :label_end
                  AND close IS NOT NULL
            ),
            h(horizon) AS (VALUES {horizon_values}),
            labels AS (
                SELECT b.trade_date, b.ts_code, h.horizon, f.close AS future_close
                FROM base b
                CROSS JOIN h
                LEFT JOIN base f
                  ON f.ts_code = b.ts_code
                 AND f.rn = b.rn + h.horizon
                WHERE b.trade_date BETWEEN :start AND :end
            )
            SELECT EXTRACT(YEAR FROM trade_date)::int AS year,
                   horizon::int,
                   COUNT(*)::bigint AS candidate_rows,
                   COUNT(*) FILTER (WHERE future_close IS NOT NULL)::bigint AS mature_label_rows,
                   COUNT(DISTINCT trade_date)::int AS dates,
                   COUNT(DISTINCT ts_code)::int AS codes
            FROM labels
            GROUP BY 1, 2
            ORDER BY 1, 2
        """), {
            "history_start": start - dt.timedelta(days=90),
            "label_end": end + dt.timedelta(days=60),
            "start": start,
            "end": end,
        }).mappings().all()
    by_horizon: dict[str, dict[str, Any]] = {str(h): {"by_year": {}, "total_mature_label_rows": 0} for h in horizons}
    for r in rows:
        h = str(int(r["horizon"]))
        candidate = int(r["candidate_rows"] or 0)
        mature = int(r["mature_label_rows"] or 0)
        by_horizon[h]["total_mature_label_rows"] += mature
        by_horizon[h]["by_year"][str(r["year"])] = {
            "candidate_rows": candidate,
            "mature_label_rows": mature,
            "maturity_rate": round(mature / candidate, 6) if candidate else None,
            "dates": int(r["dates"] or 0),
            "codes": int(r["codes"] or 0),
        }
    return by_horizon


def _market_regime_coverage(engine: Engine, start: dt.date, end: dt.date) -> dict[str, Any]:
    if not _table_exists(engine, "smartmoney", "market_state_daily"):
        return {"exists": False}
    regime_col = _first_existing_col(
        engine,
        "smartmoney",
        "market_state_daily",
        ("market_regime", "market_state", "state", "regime", "risk_regime"),
    )
    if regime_col is None:
        return {"exists": True, "by_year": {}, "warning": "no recognizable regime column"}
    with engine.connect() as c:
        rows = c.execute(text(f"""
            SELECT EXTRACT(YEAR FROM trade_date)::int AS year,
                   COALESCE({regime_col}::text, 'unknown') AS regime,
                   COUNT(*)::int AS dates
            FROM smartmoney.market_state_daily
            WHERE trade_date BETWEEN :start AND :end
            GROUP BY 1, 2
            ORDER BY 1, 2
        """), {"start": start, "end": end}).mappings().all()
    by_year: dict[str, dict[str, int]] = {}
    for r in rows:
        by_year.setdefault(str(r["year"]), {})[str(r["regime"])] = int(r["dates"] or 0)
    return {"exists": True, "by_year": by_year}


def _sector_state_coverage(engine: Engine, start: dt.date, end: dt.date) -> dict[str, Any]:
    if not _table_exists(engine, "smartmoney", "sector_state_daily"):
        return {"exists": False}
    sector_col = _first_existing_col(
        engine,
        "smartmoney",
        "sector_state_daily",
        ("sector_code", "l2_code", "sw_l2_code", "industry_code"),
    )
    if sector_col is None:
        return {"exists": True, "by_year": {}, "warning": "no recognizable sector code column"}
    with engine.connect() as c:
        rows = c.execute(text(f"""
            SELECT EXTRACT(YEAR FROM trade_date)::int AS year,
                   COUNT(*)::bigint AS rows,
                   COUNT(DISTINCT trade_date)::int AS dates,
                   COUNT(DISTINCT {sector_col})::int AS sectors
            FROM smartmoney.sector_state_daily
            WHERE trade_date BETWEEN :start AND :end
            GROUP BY 1
            ORDER BY 1
        """), {"start": start, "end": end}).mappings().all()
    return {
        "exists": True,
        "by_year": {
            str(r["year"]): {
                "rows": int(r["rows"] or 0),
                "dates": int(r["dates"] or 0),
                "sectors": int(r["sectors"] or 0),
            }
            for r in rows
        },
    }


def _validation_plan(report: dict[str, Any]) -> dict[str, Any]:
    core_rows = int(report["input_intersection"]["total_core_intersection_rows"])
    mature_20d = int(report["forward_label_capacity"]["20"]["total_mature_label_rows"])
    target_panel = min(core_rows, mature_20d)
    return {
        "standard": "staged validation: pit8/top30 smoke only; recent 6m first; multi-6m regime robustness second; 2021-2026 purged walk-forward final gate",
        "target_panel_stock_date_rows_estimate": target_panel,
        "recommended_pipeline": [
            "inventory: rerun this script before tuning to verify raw_daily/basic/moneyflow, sector/market states, and mature labels",
            "recent_6m_proxy: compute SQL/precomputed factor panel for the latest roughly six months across 5/10/20d horizons",
            "recent_6m_validation: run PIT-local or stratified PIT validation before any five-year expensive replay",
            "multi_6m_regime_windows: repeat recent/prior/2022/2023/2024/2025 six-month windows and report regime robustness separately from current edge",
            "final_full_history_gate: only after staged evidence, run 2021-2026 walk-forward/purged CV with horizon embargo >= max horizon",
            "regime_buckets: require positive lift in market regime, liquidity, size, SW L1 industry, and volatility buckets",
            "expensive_replay: run production replay only on stratified PIT samples inside the active validation window, cached by date/universe/param hash",
            "promotion_gate: YAML changes only after horizon-specific OOS/OOC lift, positive folds, and regime robustness pass",
        ],
        "engineering_bottlenecks_to_fix_before_full_expensive_replay": [
            "batch feature build and persisted stock-date feature cache",
            "SQL/precomputed forward labels and target/stop path labels",
            "stratified PIT universe manifests per fold, not current-liquidity cohorts",
            "two-stage search: cheap proxy pre-screen for the active validation window, then expensive replay re-rank",
        ],
    }


def build_report(engine: Engine, *, start: dt.date, end: dt.date) -> dict[str, Any]:
    calendar = _calendar_by_year(engine, start, end)
    table_specs = [
        ("smartmoney", "raw_daily", "trade_date", "ts_code"),
        ("smartmoney", "raw_daily_basic", "trade_date", "ts_code"),
        ("smartmoney", "raw_moneyflow", "trade_date", "ts_code"),
        ("smartmoney", "factor_daily", "trade_date", "sector_code"),
        ("smartmoney", "market_state_daily", "trade_date", None),
        ("smartmoney", "sector_state_daily", "trade_date", "sector_code"),
        ("smartmoney", "sw_member_monthly", "snapshot_month", "ts_code"),
    ]
    coverage = {
        f"{schema}.{table}": _table_coverage(
            engine,
            schema=schema,
            table=table,
            date_col=date_col,
            start=start,
            end=end,
            calendar_by_year=calendar,
            code_col=code_col,
        )
        for schema, table, date_col, code_col in table_specs
    }
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "calendar_open_days_by_year": calendar,
        "coverage": coverage,
        "input_intersection": _input_intersection(engine, start, end),
        "forward_label_capacity": _forward_label_capacity(engine, start, end),
        "market_regime_coverage": _market_regime_coverage(engine, start, end),
        "sector_state_coverage": _sector_state_coverage(engine, start, end),
    }
    report["validation_plan"] = _validation_plan(report)
    report["hard_warnings"] = [
        "pit8/top30 or any several-day replay panel is smoke only; do not use it for parameter validation or YAML promotion",
        "do not jump directly from smoke to a five-year hard run; run recent six-month validation first, then multi-window regime robustness, then full-history final gate",
    ]
    return report


def write_report(report: dict[str, Any], output: Path | None) -> Path:
    if output is None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = DEFAULT_OUTPUT_DIR / f"stock_edge_full_history_validation_{stamp}.json"
    elif output.suffix.lower() != ".json":
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = output / f"stock_edge_full_history_validation_{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return output


def _print_summary(report: dict[str, Any]) -> None:
    print("=== Stock Edge Staged Validation Diagnostics ===")
    print(f"window: {report['window']['start']}..{report['window']['end']}")
    print("standard: pit8/top30 is smoke only; run recent 6m first, multi-6m regime windows second, full-history final gate last")
    print("\nCore input intersection:")
    for year, row in report["input_intersection"]["by_year"].items():
        print(
            f"  {year}: core={row['core_intersection_rows']:,} "
            f"raw={row['raw_daily_rows']:,} rate={row['core_intersection_rate']}"
        )
    print("\nMature forward label rows:")
    for h, payload in report["forward_label_capacity"].items():
        print(f"  {h}d: total={payload['total_mature_label_rows']:,}")
    plan = report["validation_plan"]
    print(f"\nTarget panel estimate: {plan['target_panel_stock_date_rows_estimate']:,} stock-date rows")
    print("Next pipeline:")
    for item in plan["recommended_pipeline"][:4]:
        print(f"  - {item}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Stock Edge staged validation readiness")
    parser.add_argument("--start", default=DEFAULT_START.isoformat(), help="Start date, default 2021-01-01")
    parser.add_argument("--end", default=None, help="End date, default max smartmoney.raw_daily trade_date")
    parser.add_argument("--output", type=Path, default=None, help="JSON output path or directory")
    parser.add_argument("--no-write", action="store_true", help="Print summary only; do not write JSON artifact")
    parser.add_argument("--json", action="store_true", help="Print full JSON to stdout")
    args = parser.parse_args()

    engine = get_engine()
    start = _date(args.start)
    end = _date(args.end, _latest_raw_daily(engine))
    report = build_report(engine, start=start, end=end)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    else:
        _print_summary(report)
    if not args.no_write:
        path = write_report(report, args.output)
        print(f"\nartifact: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
