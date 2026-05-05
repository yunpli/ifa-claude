"""Bounded Stock Edge data inventory and backfill runner.

This script is intentionally an orchestration layer around existing repo data
adapters. It does not create a new TuShare client and never prints secrets.

Default behavior is safe: inventory + dry-run estimate only. Add ``--execute``
to fetch data.

Examples
--------
    uv run python scripts/stock_edge_data_backfill.py --inventory-only
    uv run python scripts/stock_edge_data_backfill.py --target 300042.SZ --family intraday --dry-run
    uv run python scripts/stock_edge_data_backfill.py --target 300042.SZ --family intraday --execute --resume
    uv run python scripts/stock_edge_data_backfill.py --universe top-liquidity --limit 500 --family intraday --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.config import get_settings
from ifa.core.calendar import trading_days_between
from ifa.core.db import get_engine
from ifa.core.report.timezones import bjt_now
from ifa.core.tushare import TuShareClient
from ifa.families.smartmoney.etl import raw_fetchers
from ifa.families.smartmoney.etl.sector_flow_sw_l2 import aggregate_sector_flow_sw_for_date
from ifa.families.stock.backtest.data import load_top_liquidity_universe
from ifa.families.stock.data.intraday_backfill import (
    IntradayBackfillSpec,
    backfill_intraday_sweep,
)
from ifa.families.stock.db.duckdb_client import get_conn, init_duckdb, parquet_path_for, reset_conn

IFAENV = Path("/Users/neoclaw/claude/ifaenv")
LOG_ROOT = IFAENV / "logs" / "stock_edge_data_backfill"
MANIFEST_ROOT = IFAENV / "manifests" / "stock_edge_data_backfill"

CORE_TABLES = ("raw_daily", "raw_daily_basic", "raw_moneyflow")
EVENT_TABLES = ("raw_top_list", "raw_top_inst", "raw_kpl_list", "raw_limit_list_d", "raw_block_trade")
MARKET_TABLES = ("raw_margin", "raw_moneyflow_hsgt")
SW_TABLES = ("sector_moneyflow_sw_daily",)
POSTGRES_DATASETS = [
    ("日线 OHLCV", "smartmoney", "raw_daily", "trade_date", "ts_code"),
    ("daily_basic", "smartmoney", "raw_daily_basic", "trade_date", "ts_code"),
    ("moneyflow", "smartmoney", "raw_moneyflow", "trade_date", "ts_code"),
    ("龙虎榜", "smartmoney", "raw_top_list", "trade_date", "ts_code"),
    ("龙虎榜机构", "smartmoney", "raw_top_inst", "trade_date", "ts_code"),
    ("涨停/炸板", "smartmoney", "raw_kpl_list", "trade_date", "ts_code"),
    ("涨跌停明细", "smartmoney", "raw_limit_list_d", "trade_date", "ts_code"),
    ("大宗交易", "smartmoney", "raw_block_trade", "trade_date", "ts_code"),
    ("北向市场", "smartmoney", "raw_moneyflow_hsgt", "trade_date", None),
    ("两融市场", "smartmoney", "raw_margin", "trade_date", None),
    ("SW L2 成员", "smartmoney", "sw_member_monthly", "snapshot_month", "ts_code"),
    ("SW L2 资金", "smartmoney", "sector_moneyflow_sw_daily", "trade_date", "l2_code"),
    ("SW L2 状态", "smartmoney", "sector_state_daily", "trade_date", "sector_code"),
    ("SmartMoney 因子", "smartmoney", "factor_daily", "trade_date", "ts_code"),
    ("市场状态", "smartmoney", "market_state_daily", "trade_date", None),
    ("Stock signals", "smartmoney", "stock_signals_daily", "trade_date", "ts_code"),
    ("Predictions", "smartmoney", "predictions_daily", "trade_date", "ts_code"),
    ("TA candidates", "ta", "candidates_daily", "trade_date", "ts_code"),
    ("TA setup metrics", "ta", "setup_metrics_daily", "trade_date", None),
    ("TA warnings", "ta", "warnings_daily", "trade_date", "ts_code"),
    ("交易日历", "smartmoney", "trade_cal", "cal_date", None),
    ("Research reports", "research", "report_runs", "created_at", "ts_code"),
    ("Research factors", "research", "period_factor_decomposition", "created_at", "ts_code"),
    ("Research PDF cache", "research", "pdf_extract_cache", "created_at", None),
]


@dataclass
class InventoryRow:
    data_family: str
    storage: str
    table_or_path: str
    entity_count: int | None
    date_range: str
    rows_or_files: str
    fields: str
    supports_5d: str
    supports_10d: str
    supports_20d: str
    needs_backfill: str


@dataclass
class Task:
    key: str
    family: str
    payload: dict[str, Any]
    estimated_rows: int = 0
    estimated_mb: float = 0.0
    status: str = "pending"
    error: str | None = None


@dataclass
class RunState:
    run_id: str
    created_at: str
    dry_run: bool
    max_new_data_gb: float
    tasks: dict[str, Task] = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def main() -> None:
    args = _parse_args()
    run_id = args.run_id or bjt_now().strftime("%Y%m%d_%H%M%S")
    run_dir = MANIFEST_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logger = _setup_logging(LOG_ROOT / f"{run_id}.log")
    engine = get_engine(get_settings())
    as_of = _resolve_as_of(engine, args.as_of)

    logger.info("Stock Edge data backfill run_id=%s as_of=%s execute=%s", run_id, as_of, args.execute)
    logger.info("Using existing Settings/get_engine/TuShareClient; tokens are never logged.")

    inventory = collect_inventory(engine)
    _write_inventory(inventory, run_dir / "inventory.md")
    print_inventory(inventory)
    if args.inventory_only:
        logger.info("inventory-only requested; stop.")
        return

    universe = resolve_universe(engine, args, as_of)
    if not universe and _needs_universe(args.family):
        raise SystemExit("No universe selected. Use --target TS_CODE or --universe top-liquidity.")
    if universe:
        logger.info("Universe size=%d sample=%s", len(universe), ", ".join(universe[:8]))

    tasks = build_tasks(engine, args, as_of, universe)
    state_path = run_dir / "checkpoint.json"
    retry_path = run_dir / "retry_queue.jsonl"
    state = load_or_create_state(state_path, run_id, dry_run=not args.execute, max_new_data_gb=args.max_new_data_gb, tasks=tasks, resume=args.resume)
    estimate = summarize_tasks(state.tasks.values())
    write_manifest(run_dir / "manifest.json", args=args, as_of=as_of, universe=universe, inventory=inventory, state=state, estimate=estimate)
    print_budget_report(args, estimate, universe)

    if estimate["estimated_gb"] > args.max_new_data_gb and not args.allow_over_budget:
        logger.error(
            "Estimated %.3f GB exceeds budget %.3f GB. Add --allow-over-budget or reduce universe/window.",
            estimate["estimated_gb"],
            args.max_new_data_gb,
        )
        return
    if not args.execute:
        logger.info("Dry-run only. Add --execute to fetch. Manifest: %s", run_dir / "manifest.json")
        return

    interrupted = {"stop": False}

    def _handle_sigint(_signum, _frame) -> None:
        interrupted["stop"] = True
        logger.warning("Interrupt received; finishing current task then checkpointing.")

    signal.signal(signal.SIGINT, _handle_sigint)
    execute_tasks(engine, state, args=args, state_path=state_path, retry_path=retry_path, logger=logger, interrupted=interrupted)
    write_manifest(run_dir / "manifest.json", args=args, as_of=as_of, universe=universe, inventory=inventory, state=state, estimate=summarize_tasks(state.tasks.values()))
    logger.info("Done. completed=%d failed=%d checkpoint=%s retry=%s", len(state.completed), len(state.failed), state_path, retry_path)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bounded Stock Edge data inventory/backfill for 5d/10d/20d decisions.")
    p.add_argument("--run-id", help="Resume/checkpoint namespace. Defaults to timestamp.")
    p.add_argument("--inventory-only", action="store_true", help="Only write/print inventory; no task planning.")
    p.add_argument("--execute", action="store_true", help="Actually fetch/write data. Default is dry-run.")
    p.add_argument("--dry-run", action="store_true", help="Explicit dry-run alias; kept for readability.")
    p.add_argument("--resume", action="store_true", help="Resume from checkpoint for this run-id.")
    p.add_argument("--family", default="intraday", help="Comma list: core,event,market,sw,intraday,all. Default intraday.")
    p.add_argument("--target", action="append", default=[], help="Target ts_code; can repeat.")
    p.add_argument("--target-file", help="File with one ts_code per line.")
    p.add_argument("--universe", choices=["targets", "top-liquidity"], default="targets")
    p.add_argument("--limit", type=int, default=500, help="Top-liquidity universe size.")
    p.add_argument("--as-of", help="YYYY-MM-DD; defaults to latest local raw_daily date.")
    p.add_argument("--start", default="2021-01-01", help="Core/event missing-date scan start.")
    p.add_argument("--intraday-days", type=int, default=180, help="5min lookback trading-day proxy.")
    p.add_argument("--intraday-freq", default="5min", help="Source minute freq to fetch. V1 default: 5min only.")
    p.add_argument("--derive-30-60", action=argparse.BooleanOptionalAction, default=True, help="Derive 30/60min parquet from 5min after fetch.")
    p.add_argument("--max-new-data-gb", type=float, default=10.0)
    p.add_argument("--allow-over-budget", action="store_true")
    p.add_argument("--batch-size", type=int, default=25)
    p.add_argument("--sleep-seconds", type=float, default=0.25)
    return p.parse_args()


def _setup_logging(path: Path) -> logging.Logger:
    logger = logging.getLogger("stock_edge_data_backfill")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)
    return logger


def _resolve_as_of(engine: Engine, raw: str | None) -> dt.date:
    if raw:
        return dt.date.fromisoformat(raw)
    with engine.connect() as conn:
        value = conn.execute(text("SELECT MAX(trade_date) FROM smartmoney.raw_daily")).scalar_one_or_none()
    if value is None:
        raise SystemExit("No local smartmoney.raw_daily date found.")
    return value


def collect_inventory(engine: Engine) -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    for family, schema, table, date_col, entity_col in POSTGRES_DATASETS:
        rows.append(_inventory_postgres(engine, family, schema, table, date_col, entity_col))
    rows.extend(_inventory_duckdb())
    rows.extend(_inventory_paths())
    return rows


def _inventory_postgres(engine: Engine, family: str, schema: str, table: str, date_col: str, entity_col: str | None) -> InventoryRow:
    full = f"{schema}.{table}"
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT to_regclass(:name)"), {"name": full}).scalar_one_or_none()
        if not exists:
            return InventoryRow(family, "PostgreSQL", full, None, "缺表", "0", "", "否", "否", "否", "需要建表/接入")
        cols = [r[0] for r in conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_schema=:s AND table_name=:t ORDER BY ordinal_position"),
            {"s": schema, "t": table},
        )]
        dc = date_col if date_col in cols else None
        ec = entity_col if entity_col and entity_col in cols else None
        select = ["COUNT(*) AS rows"]
        select.append(f"COUNT(DISTINCT {ec}) AS entities" if ec else "NULL::bigint AS entities")
        select.extend([f"MIN({dc}) AS min_date", f"MAX({dc}) AS max_date"] if dc else ["NULL AS min_date", "NULL AS max_date"])
        data = conn.execute(text(f"SELECT {', '.join(select)} FROM {full}")).mappings().one()
    min_date, max_date = data["min_date"], data["max_date"]
    enough = _enough_recent(max_date)
    needs = "否" if enough and int(data["rows"] or 0) > 0 else "是"
    return InventoryRow(
        family,
        "PostgreSQL",
        full,
        int(data["entities"]) if data["entities"] is not None else None,
        f"{min_date or '—'} → {max_date or '—'}",
        str(data["rows"]),
        ", ".join(cols[:14]) + ("..." if len(cols) > 14 else ""),
        "是" if enough else "否",
        "是" if enough else "否",
        "是" if enough else "否",
        needs,
    )


def _inventory_duckdb() -> list[InventoryRow]:
    out: list[InventoryRow] = []
    try:
        init_duckdb()
        conn = get_conn(read_only=True)
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        for view in ["intraday_5min", "intraday_30min", "intraday_60min", "kronos_embeddings"]:
            if view not in tables:
                out.append(InventoryRow(view, "DuckDB", view, None, "缺 view", "0", "", "否", "否", "否", "视策略需要补"))
                continue
            cols = [r[0] for r in conn.execute(f"DESCRIBE {view}").fetchall()]
            date_expr = "CAST(trade_time AS DATE)" if "trade_time" in cols else ("trade_date" if "trade_date" in cols else None)
            entity_col = "ts_code" if "ts_code" in cols else None
            select = ["COUNT(*) AS rows"]
            select.append(f"COUNT(DISTINCT {entity_col}) AS entities" if entity_col else "NULL AS entities")
            select.extend([f"MIN({date_expr}) AS min_date", f"MAX({date_expr}) AS max_date"] if date_expr else ["NULL AS min_date", "NULL AS max_date"])
            data = conn.execute(f"SELECT {', '.join(select)} FROM {view}").fetchdf().iloc[0].to_dict()
            enough = _enough_recent(data.get("max_date"))
            out.append(InventoryRow(
                view,
                "DuckDB/Parquet",
                view,
                int(data["entities"]) if not pd.isna(data.get("entities")) else None,
                f"{data.get('min_date') or '—'} → {data.get('max_date') or '—'}",
                str(int(data.get("rows") or 0)),
                ", ".join(cols[:12]) + ("..." if len(cols) > 12 else ""),
                "是" if enough else "部分",
                "是" if enough else "部分",
                "是" if enough else "部分",
                "视 universe/窗口补",
            ))
    except Exception as exc:  # noqa: BLE001
        out.append(InventoryRow("分钟线", "DuckDB", "stock.duckdb", None, "不可用", "0", str(exc)[:80], "否", "否", "否", "需要检查 DuckDB"))
    return out


def _inventory_paths() -> list[InventoryRow]:
    paths = [
        ("模型 artifact", IFAENV / "models" / "stock" / "tuning"),
        ("报告输出", IFAENV / "out"),
        ("Parquet root", IFAENV / "duckdb" / "parquet"),
        ("Backfill manifests", MANIFEST_ROOT),
    ]
    out: list[InventoryRow] = []
    for family, path in paths:
        files = sum(1 for p in path.rglob("*") if p.is_file()) if path.exists() else 0
        out.append(InventoryRow(family, "Filesystem", str(path), None, "—", f"{files} files", "filesystem artifacts", "不适用", "不适用", "不适用", "否"))
    return out


def _enough_recent(max_date: Any) -> bool:
    if max_date is None or pd.isna(max_date):
        return False
    try:
        d = pd.to_datetime(max_date).date()
    except Exception:
        return False
    return (bjt_now().date() - d).days <= 14


def _write_inventory(rows: list[InventoryRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["数据族", "存储位置", "表/路径", "股票数", "日期范围", "行数/文件数", "字段", "5d", "10d", "20d", "需补"]
    lines = ["| " + " | ".join(headers) + " |", "|---" * len(headers) + "|"]
    for r in rows:
        values = [r.data_family, r.storage, r.table_or_path, str(r.entity_count or "—"), r.date_range, r.rows_or_files, r.fields, r.supports_5d, r.supports_10d, r.supports_20d, r.needs_backfill]
        lines.append("| " + " | ".join(_md(v) for v in values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_inventory(rows: list[InventoryRow]) -> None:
    print("\n[A1] 当前本地数据盘点")
    for r in rows:
        print(f"- {r.data_family:<22} {r.table_or_path:<55} rows/files={r.rows_or_files:<12} range={r.date_range} need={r.needs_backfill}", flush=True)


def resolve_universe(engine: Engine, args: argparse.Namespace, as_of: dt.date) -> list[str]:
    targets = [v.strip().upper() for v in args.target if v.strip()]
    if args.target_file:
        targets.extend(_read_codes(Path(args.target_file)))
    targets = _dedupe(targets)
    if args.universe == "targets":
        return targets
    if args.universe == "top-liquidity":
        codes = load_top_liquidity_universe(engine, as_of_date=as_of, limit=args.limit)
        return _dedupe([*targets, *codes])
    return targets


def _read_codes(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"target file not found: {path}")
    return [line.strip().upper() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


def _needs_universe(family_arg: str) -> bool:
    return any(f in _families(family_arg) for f in {"intraday"})


def _families(raw: str) -> set[str]:
    values = {v.strip().lower() for v in raw.split(",") if v.strip()}
    if "all" in values:
        return {"core", "event", "market", "sw", "intraday"}
    return values


def build_tasks(engine: Engine, args: argparse.Namespace, as_of: dt.date, universe: list[str]) -> dict[str, Task]:
    families = _families(args.family)
    tasks: dict[str, Task] = {}
    start = dt.date.fromisoformat(args.start)
    if families & {"core", "event", "market", "sw"}:
        tasks.update(_build_postgres_tasks(engine, families, start, as_of))
    if "intraday" in families:
        for code in universe:
            task = _intraday_task(engine, code, as_of, args)
            if task:
                tasks[task.key] = task
    return tasks


def _build_postgres_tasks(engine: Engine, families: set[str], start: dt.date, as_of: dt.date) -> dict[str, Task]:
    table_groups: list[str] = []
    if "core" in families:
        table_groups.extend(CORE_TABLES)
    if "event" in families:
        table_groups.extend(EVENT_TABLES)
    if "market" in families:
        table_groups.extend(MARKET_TABLES)
    tasks: dict[str, Task] = {}
    days = trading_days_between(engine, start, as_of)
    for table in _dedupe(table_groups):
        missing = _missing_table_dates(engine, table, days)
        for day in missing:
            tasks[f"postgres:{table}:{day:%Y%m%d}"] = Task(
                key=f"postgres:{table}:{day:%Y%m%d}",
                family="postgres",
                payload={"table": table, "trade_date": day.isoformat()},
                estimated_rows=6000 if table in CORE_TABLES else 800,
                estimated_mb=1.5 if table in CORE_TABLES else 0.25,
            )
    if "sw" in families:
        missing = _missing_table_dates(engine, "sector_moneyflow_sw_daily", days)
        for day in missing:
            tasks[f"postgres:sector_moneyflow_sw_daily:{day:%Y%m%d}"] = Task(
                key=f"postgres:sector_moneyflow_sw_daily:{day:%Y%m%d}",
                family="sector_aggregate",
                payload={"trade_date": day.isoformat()},
                estimated_rows=150,
                estimated_mb=0.05,
            )
    return tasks


def _missing_table_dates(engine: Engine, table: str, days: list[dt.date]) -> list[dt.date]:
    if not days:
        return []
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT to_regclass(:name)"), {"name": f"smartmoney.{table}"}).scalar_one_or_none()
        if not exists:
            return days
        existing = conn.execute(
            text(f"SELECT DISTINCT trade_date FROM smartmoney.{table} WHERE trade_date = ANY(:days)"),
            {"days": days},
        ).scalars().all()
    return [day for day in days if day not in set(existing)]


def _intraday_task(engine: Engine, ts_code: str, as_of: dt.date, args: argparse.Namespace) -> Task | None:
    start = as_of - dt.timedelta(days=max(args.intraday_days * 2, args.intraday_days + 14))
    existing_rows = _existing_intraday_rows(ts_code, start, as_of, args.intraday_freq)
    expected_rows = int(args.intraday_days * (48 if args.intraday_freq == "5min" else 16))
    if existing_rows >= max(40, expected_rows * 0.75):
        return None
    missing_rows = max(0, expected_rows - existing_rows)
    estimated_mb = missing_rows * 160 / 1_000_000 / 5.0
    # Small-file overhead matters more than row payload for many symbols/months.
    estimated_mb += max(1.0, math.ceil(args.intraday_days / 21)) * 0.02
    return Task(
        key=f"intraday:{ts_code}:{args.intraday_freq}:{args.intraday_days}",
        family="intraday",
        payload={"ts_code": ts_code, "freq": args.intraday_freq, "lookback_days": args.intraday_days, "as_of": as_of.isoformat(), "derive_30_60": bool(args.derive_30_60)},
        estimated_rows=missing_rows,
        estimated_mb=round(estimated_mb, 4),
    )


def _existing_intraday_rows(ts_code: str, start: dt.date, end: dt.date, freq: str) -> int:
    try:
        init_duckdb()
        conn = get_conn(read_only=True)
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        if "intraday_5min" not in tables:
            return 0
        cols = {r[0] for r in conn.execute("DESCRIBE intraday_5min").fetchall()}
        freq_filter = "AND freq = ?" if "freq" in cols else ""
        params: list[Any] = [ts_code, start, end]
        if freq_filter:
            params.append(freq)
        return int(conn.execute(
            f"""
            SELECT COUNT(*)
            FROM intraday_5min
            WHERE ts_code = ?
              AND CAST(trade_time AS DATE) BETWEEN ? AND ?
              {freq_filter}
            """,
            params,
        ).fetchone()[0])
    except Exception:
        return 0


def summarize_tasks(tasks: Any) -> dict[str, Any]:
    rows = list(tasks)
    total_mb = sum(float(t.estimated_mb) for t in rows)
    api_calls = sum(1 for t in rows if t.family == "intraday") + sum(1 for t in rows if t.family == "postgres")
    return {
        "task_count": len(rows),
        "estimated_rows": sum(int(t.estimated_rows) for t in rows),
        "estimated_mb": round(total_mb, 3),
        "estimated_gb": round(total_mb / 1024.0, 4),
        "estimated_api_calls": api_calls,
        "estimated_runtime_minutes": round(max(api_calls * 1.2 / 60.0, len(rows) * 0.05 / 60.0), 1),
    }


def print_budget_report(args: argparse.Namespace, estimate: dict[str, Any], universe: list[str]) -> None:
    print("\n[A3] 数据预算估算")
    print(f"- 当前任务：tasks={estimate['task_count']} rows≈{estimate['estimated_rows']:,} parquet/DB≈{estimate['estimated_mb']:.3f} MB api_calls≈{estimate['estimated_api_calls']} runtime≈{estimate['estimated_runtime_minutes']} min")
    print(f"- 预算：{args.max_new_data_gb:.2f} GB；执行模式：{'execute' if args.execute else 'dry-run'}")
    for label, n, days in [("方案A", 500, 180), ("方案B", 800, 180), ("方案C", 1200, 252)]:
        rows = n * days * 48
        mb = rows * 160 / 1_000_000 / 5.0 + n * math.ceil(days / 21) * 0.02
        print(f"- {label}: Top {n} × {days} trading days 5min → rows≈{rows:,}, parquet≈{mb/1024:.2f} GB, API≈{n}, time≈{n*1.2/60:.1f} min")
    if universe:
        print(f"- 本次 universe={len(universe)}；建议 V1 默认选择不超过 10GB 的最小方案。")


def load_or_create_state(path: Path, run_id: str, *, dry_run: bool, max_new_data_gb: float, tasks: dict[str, Task], resume: bool) -> RunState:
    if resume and path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        state = RunState(
            run_id=raw["run_id"],
            created_at=raw["created_at"],
            dry_run=bool(raw["dry_run"]),
            max_new_data_gb=float(raw["max_new_data_gb"]),
            tasks={k: Task(**v) for k, v in raw["tasks"].items()},
            completed=list(raw.get("completed") or []),
            failed=list(raw.get("failed") or []),
        )
        for key, task in tasks.items():
            state.tasks.setdefault(key, task)
        return state
    state = RunState(run_id=run_id, created_at=dt.datetime.now(dt.timezone.utc).isoformat(), dry_run=dry_run, max_new_data_gb=max_new_data_gb, tasks=tasks)
    save_state(path, state)
    return state


def save_state(path: Path, state: RunState) -> None:
    payload = {
        "run_id": state.run_id,
        "created_at": state.created_at,
        "dry_run": state.dry_run,
        "max_new_data_gb": state.max_new_data_gb,
        "tasks": {k: asdict(v) for k, v in state.tasks.items()},
        "completed": state.completed,
        "failed": state.failed,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_manifest(path: Path, *, args: argparse.Namespace, as_of: dt.date, universe: list[str], inventory: list[InventoryRow], state: RunState, estimate: dict[str, Any]) -> None:
    payload = {
        "run_id": state.run_id,
        "created_at": state.created_at,
        "as_of": as_of.isoformat(),
        "args": {k: v for k, v in vars(args).items() if k not in {"target"}},
        "target_count": len(universe),
        "targets_sample": universe[:20],
        "estimate": estimate,
        "completed": len(state.completed),
        "failed": len(state.failed),
        "inventory_file": str(path.parent / "inventory.md"),
        "checkpoint_file": str(path.parent / "checkpoint.json"),
        "retry_queue_file": str(path.parent / "retry_queue.jsonl"),
        "inventory": [asdict(row) for row in inventory],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def execute_tasks(engine: Engine, state: RunState, *, args: argparse.Namespace, state_path: Path, retry_path: Path, logger: logging.Logger, interrupted: dict[str, bool]) -> None:
    client: TuShareClient | None = None
    pending = [task for task in state.tasks.values() if task.status not in {"done"}]
    started = time.monotonic()
    total = len(pending)
    for idx, task in enumerate(pending, start=1):
        if interrupted.get("stop"):
            save_state(state_path, state)
            return
        eta = _eta(started, idx - 1, total)
        logger.info("[%d/%d eta=%s] %s", idx, total, eta, task.key)
        try:
            for attempt in range(1, 4):
                try:
                    if task.family == "intraday":
                        _execute_intraday_task(task, logger)
                    elif task.family == "postgres":
                        client = client or TuShareClient()
                        _execute_postgres_task(engine, client, task)
                    elif task.family == "sector_aggregate":
                        aggregate_sector_flow_sw_for_date(engine, dt.date.fromisoformat(task.payload["trade_date"]))
                    else:
                        raise ValueError(f"Unknown task family {task.family}")
                    break
                except Exception:
                    if attempt >= 3:
                        raise
                    wait = min(60.0, max(args.sleep_seconds, 2.0) * attempt)
                    logger.warning("Task attempt %d failed; retrying after %.1fs: %s", attempt, wait, task.key, exc_info=True)
                    time.sleep(wait)
            task.status = "done"
            task.error = None
            if task.key not in state.completed:
                state.completed.append(task.key)
        except Exception as exc:  # noqa: BLE001
            task.status = "failed"
            task.error = f"{type(exc).__name__}: {exc}"
            state.failed.append(task.key)
            with retry_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(task), ensure_ascii=False, default=str) + "\n")
            logger.exception("Task failed: %s", task.key)
        save_state(state_path, state)
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)
        if args.batch_size > 0 and idx % args.batch_size == 0:
            logger.info("Batch checkpoint: completed=%d failed=%d remaining=%d", len(state.completed), len(state.failed), total - idx)


def _execute_postgres_task(engine: Engine, client: TuShareClient, task: Task) -> None:
    table = task.payload["table"]
    day = dt.date.fromisoformat(task.payload["trade_date"])
    fn = dict(raw_fetchers.TRADE_DATE_FETCHERS).get(table)
    if fn is None:
        raise ValueError(f"No raw fetcher for {table}")
    fn(client, engine, trade_date=day)


def _execute_intraday_task(task: Task, logger: logging.Logger) -> None:
    code = str(task.payload["ts_code"])
    freq = str(task.payload["freq"])
    days = int(task.payload["lookback_days"])
    as_of = dt.date.fromisoformat(str(task.payload["as_of"]))
    result = backfill_intraday_sweep([IntradayBackfillSpec(code, freq, days)], end_date=as_of, on_log=logger.info)
    logger.info("intraday fetched %s rows=%d files=%d", code, result.rows_written, len(result.files_written))
    if bool(task.payload.get("derive_30_60")) and freq == "5min":
        for derived in ["30min", "60min"]:
            rows = derive_intraday_from_5min(code, as_of=as_of, lookback_days=days, target_freq=derived)
            logger.info("derived %s %s rows=%d", code, derived, rows)


def derive_intraday_from_5min(ts_code: str, *, as_of: dt.date, lookback_days: int, target_freq: str) -> int:
    start = as_of - dt.timedelta(days=max(lookback_days * 2, lookback_days + 14))
    init_duckdb()
    conn = get_conn(read_only=True)
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    if "intraday_5min" not in tables:
        return 0
    df = conn.execute(
        """
        SELECT ts_code, trade_time, open, high, low, close, vol, amount
        FROM intraday_5min
        WHERE ts_code = ?
          AND freq = '5min'
          AND CAST(trade_time AS DATE) BETWEEN ? AND ?
        ORDER BY trade_time
        """,
        [ts_code, start, as_of],
    ).df()
    if df.empty:
        return 0
    df["trade_time"] = pd.to_datetime(df["trade_time"])
    rule = {"30min": "30min", "60min": "60min"}[target_freq]
    out_rows = []
    for _, day in df.groupby(df["trade_time"].dt.date, sort=True):
        grouped = day.set_index("trade_time").resample(rule, label="right", closed="right")
        agg = grouped.agg({"ts_code": "last", "open": "first", "high": "max", "low": "min", "close": "last", "vol": "sum", "amount": "sum"}).dropna(subset=["open", "high", "low", "close"])
        agg = agg.reset_index()
        agg["trade_date"] = agg["trade_time"].dt.date
        agg["freq"] = target_freq
        out_rows.append(agg)
    if not out_rows:
        return 0
    out = pd.concat(out_rows, ignore_index=True)
    written = 0
    reset_conn()
    for (year, month), group in out.groupby([out["trade_time"].dt.year, out["trade_time"].dt.month], sort=True):
        path = parquet_path_for(int(year), int(month), prefix=f"{ts_code.replace('.', '_')}_{target_freq}")
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        merged = pd.concat([existing, group], ignore_index=True) if not existing.empty else group.copy()
        merged["trade_time"] = pd.to_datetime(merged["trade_time"])
        merged = merged.sort_values(["ts_code", "freq", "trade_time"]).drop_duplicates(["ts_code", "freq", "trade_time"], keep="last")
        merged.to_parquet(path, index=False, compression="snappy")
        written += len(group)
    reset_conn()
    init_duckdb()
    conn = get_conn()
    conn.execute("CREATE OR REPLACE VIEW intraday_30min AS SELECT * FROM intraday_5min WHERE freq = '30min'")
    conn.execute("CREATE OR REPLACE VIEW intraday_60min AS SELECT * FROM intraday_5min WHERE freq = '60min'")
    return written


def _eta(started: float, done: int, total: int) -> str:
    if done <= 0:
        return "unknown"
    elapsed = time.monotonic() - started
    remaining = elapsed / done * max(total - done, 0)
    return f"{remaining / 60:.1f}m"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
