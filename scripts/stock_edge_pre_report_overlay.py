"""Standalone pre-report Stock Edge overlay tuning.

Example:
  uv run python scripts/stock_edge_pre_report_overlay.py 300042.SZ --as-of 2026-04-30
"""
from __future__ import annotations

import argparse
import datetime as dt

from sqlalchemy import text

from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.core.report.timezones import bjt_now
from ifa.families.stock.backtest import fit_pre_report_overlay, plan_pre_report_tuning, write_tuning_artifact
from ifa.families.stock.backtest.data import load_daily_bars_for_tuning
from ifa.families.stock.data.tushare_backfill import backfill_core_stock_window
from ifa.families.stock.params import load_params


def main() -> None:
    parser = argparse.ArgumentParser(description="Run standalone Stock Edge pre-report overlay tuning.")
    parser.add_argument("ts_code", help="A-share ts_code, e.g. 300042.SZ")
    parser.add_argument("--as-of", dest="as_of", help="As-of trade date YYYY-MM-DD; defaults to latest local raw_daily date")
    parser.add_argument("--max-candidates", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true", help="Do not write artifact")
    args = parser.parse_args()

    settings = get_settings()
    engine = get_engine(settings)
    params = load_params()
    as_of_date = _as_of_date(args.as_of, engine)
    ts_code = args.ts_code.strip().upper()
    bars = load_daily_bars_for_tuning(
        engine,
        ts_code=ts_code,
        as_of_date=as_of_date,
        lookback_rows=int(params.get("tuning", {}).get("pre_report_overlay", {}).get("max_history_rows", 900)),
    )
    overlay_cfg = params.get("tuning", {}).get("pre_report_overlay", {})
    min_history_rows = int(overlay_cfg.get("min_history_rows", params.get("tuning", {}).get("min_history_rows", 360)))
    max_history_rows = int(overlay_cfg.get("max_history_rows", params.get("tuning", {}).get("max_history_rows", 900)))
    plan = plan_pre_report_tuning(
        bars,
        ts_code=ts_code,
        as_of_trade_date=as_of_date,
        stale_after_days=int(overlay_cfg.get("ttl_days", 10)),
        min_history_rows=min_history_rows,
        max_history_rows=max_history_rows,
    )
    if (
        not plan.should_tune
        and plan.history_rows < min_history_rows
        and bool(overlay_cfg.get("backfill_on_short_history", params.get("data", {}).get("tushare_backfill_on_missing", True)))
    ):
        print(f"history short {plan.history_rows}/{min_history_rows}; trying TuShare backfill")
        backfill = backfill_core_stock_window(
            engine,
            ts_code,
            as_of_date,
            daily_rows=max_history_rows,
            basic_rows=max(20, int(params.get("runtime", {}).get("default_lookback_days", 7))),
            moneyflow_rows=max(20, int(params.get("runtime", {}).get("default_lookback_days", 7))),
        )
        print(f"backfill dates={len(backfill.requested_dates)} errors={len(backfill.errors)} counts={backfill.fetched_counts}")
        bars = load_daily_bars_for_tuning(
            engine,
            ts_code=ts_code,
            as_of_date=as_of_date,
            lookback_rows=max_history_rows,
        )
        plan = plan_pre_report_tuning(
            bars,
            ts_code=ts_code,
            as_of_trade_date=as_of_date,
            stale_after_days=int(overlay_cfg.get("ttl_days", 10)),
            min_history_rows=min_history_rows,
            max_history_rows=max_history_rows,
        )
    print(f"{ts_code} overlay plan: {plan.reason}")
    if not plan.should_tune:
        return
    artifact = fit_pre_report_overlay(
        bars,
        ts_code=ts_code,
        as_of_trade_date=as_of_date,
        base_params=params,
        max_candidates=args.max_candidates,
    )
    print(
        f"overlay tuned score={artifact.objective_score:.4f} "
        f"samples={artifact.metrics.get('sample_count', 0)} candidates={artifact.candidate_count}"
    )
    if not args.dry_run:
        print(f"artifact -> {write_tuning_artifact(artifact)}")


def _as_of_date(raw: str | None, engine) -> dt.date:
    if raw:
        return dt.date.fromisoformat(raw)
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT MAX(trade_date) FROM smartmoney.raw_daily WHERE trade_date <= :today"),
            {"today": bjt_now().date()},
        ).scalar_one()
    return value


if __name__ == "__main__":
    main()
