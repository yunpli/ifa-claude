"""Standalone weekend/overnight Stock Edge global preset tuning.

Example:
  uv run python scripts/stock_edge_global_preset.py --as-of 2026-04-30 --limit 500
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

from sqlalchemy import text

from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.core.report.timezones import bjt_now
from ifa.families.stock.backtest import fit_global_preset, plan_global_preset_refresh, write_tuning_artifact
from ifa.families.stock.backtest.data import load_top_liquidity_universe, load_universe_daily_bars_with_backfill
from ifa.families.stock.params import load_params

LOG_ROOT = Path("/Users/neoclaw/claude/ifaenv/logs/stock_edge_tuning")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run standalone Stock Edge global preset tuning.")
    parser.add_argument("--as-of", dest="as_of", help="As-of trade date YYYY-MM-DD; defaults to latest local raw_daily date")
    parser.add_argument("--universe", default="top_liquidity_500")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--max-candidates", type=int, default=96)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true", help="Do not write artifact")
    parser.add_argument(
        "--no-backfill-short-history",
        action="store_true",
        help="Skip TuShare backfill for short-history stocks and exclude them from tuning samples.",
    )
    args = parser.parse_args()
    logger = _logger(f"global_preset_{args.as_of or 'latest'}_{args.limit}_{args.max_candidates}.log")

    settings = get_settings()
    engine = get_engine(settings)
    params = load_params()
    as_of_date = _as_of_date(args.as_of, engine)
    preset_cfg = params.get("tuning", {}).get("global_preset", {})
    plan = plan_global_preset_refresh(
        as_of_date=as_of_date,
        universe=args.universe,
        min_stocks=int(preset_cfg.get("min_stocks", 300)),
        max_stocks=int(preset_cfg.get("max_stocks", 800)),
        refresh_after_days=int(preset_cfg.get("artifact_ttl_days", 10)),
    )
    _log(logger, f"{args.universe} global preset plan: {plan.reason}")
    ts_codes = load_top_liquidity_universe(engine, as_of_date=as_of_date, limit=args.limit)
    _log(logger, f"loading daily bars for {len(ts_codes)} stocks...")
    bars_by_stock, backfill_meta = load_universe_daily_bars_with_backfill(
        engine,
        ts_codes=ts_codes,
        as_of_date=as_of_date,
        lookback_rows=int(params.get("tuning", {}).get("pre_report_overlay", {}).get("max_history_rows", 900)),
        min_history_rows=int(params.get("tuning", {}).get("min_history_rows", 360)),
        backfill_short_history=(
            not args.no_backfill_short_history
            and bool(preset_cfg.get("backfill_short_history", params.get("data", {}).get("tushare_backfill_on_missing", True)))
        ),
        max_backfill_stocks=int(preset_cfg.get("max_backfill_stocks", 50)),
        on_log=lambda msg: _log(logger, msg),
    )
    if backfill_meta.get("backfill_attempted"):
        _log(
            logger,
            f"backfill attempted={backfill_meta['backfill_attempted']} "
            f"errors={backfill_meta['backfill_errors']} short_after={backfill_meta.get('short_history_after_backfill', 0)}"
        )
    elif backfill_meta.get("short_history_count"):
        _log(
            logger,
            f"short-history stocks skipped={backfill_meta['short_history_count']} "
            f"(<{int(params.get('tuning', {}).get('min_history_rows', 360))} rows)",
        )
    artifact = fit_global_preset(
        bars_by_stock,
        as_of_date=as_of_date,
        base_params=params,
        universe=args.universe,
        max_candidates=args.max_candidates,
        progress_every=args.progress_every,
        on_progress=lambda p: _log(
            logger,
            f"candidate {p['candidate']}/{p['total']} score={p['score']:.6f} "
            f"best={p['best_score']:.6f} elapsed={p['elapsed_seconds']}s eta={p['eta_seconds']}s",
        ),
    )
    _log(
        logger,
        f"global preset tuned score={artifact.objective_score:.4f} "
        f"stocks={artifact.metrics.get('stock_count', 0)} samples={artifact.metrics.get('sample_count', 0)} "
        f"candidates={artifact.candidate_count}",
    )
    _log(logger, f"top changed params: {_top_changed_params(artifact.overlay, params)}")
    if not args.dry_run:
        _log(logger, f"artifact -> {write_tuning_artifact(artifact)}")


def _as_of_date(raw: str | None, engine) -> dt.date:
    if raw:
        return dt.date.fromisoformat(raw)
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT MAX(trade_date) FROM smartmoney.raw_daily WHERE trade_date <= :today"),
            {"today": bjt_now().date()},
        ).scalar_one()
    return value


def _logger(name: str) -> logging.Logger:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"stock_edge_global_preset.{name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    fh = logging.FileHandler(LOG_ROOT / name, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)
    return logger


def _log(logger: logging.Logger, message: str) -> None:
    logger.info(message)
    for handler in logger.handlers:
        handler.flush()


def _top_changed_params(overlay: dict, params: dict, limit: int = 12) -> str:
    if not overlay:
        return "baseline selected; no overlay changes"
    ranked = []
    for key, value in overlay.items():
        old = _get_param(params, key)
        try:
            delta = abs(float(value) - float(old))
        except Exception:
            delta = 0.0
        ranked.append((delta, key, old, value))
    ranked.sort(reverse=True)
    return "; ".join(f"{key}:{old}->{value}" for _delta, key, old, value in ranked[:limit])


def _get_param(params: dict, dotted_key: str):
    current = params if dotted_key.startswith(("risk.", "t0.", "model.", "runtime.", "data.", "intraday.", "cache.", "report.", "tuning.")) else params.get("strategy_matrix", {})
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


if __name__ == "__main__":
    main()
