"""PIT replay panel — runs production strategy_matrix + decision_layer over historical (stock, as_of) samples.

Critical insight that drives perf: signal SCORES are independent of the weight params
we tune (signal_weights / cluster_weights / decision_layer.horizons.*.weights). Those
weights only act on aggregation. So we build the panel ONCE per (universe, dates,
base_params), then evaluate every candidate overlay by re-applying weights on the
cached signal matrix — millisecond-level instead of re-running compute_strategy_matrix.

Wall-time targets (Top 200 stocks × 15 PIT dates = 3,000 rows):
- naive serial: ~50-100 min
- + multiprocess (n_workers=8): ~7-10 min
- + parquet cache reuse: ~5 sec on 2nd run
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool, QueuePool

from ifa.families.stock.context import StockEdgeRequest, build_context
from ifa.families.stock.data import build_local_snapshot
from ifa.families.stock.data.availability import LoadResult
from ifa.families.stock.data.gateway import LocalDataGateway
from ifa.families.stock.decision_layer import DEFAULT_KEYS
from ifa.families.stock.params import load_params, params_hash
from ifa.families.stock.strategies import compute_strategy_matrix
from ifa.families.stock.strategies.catalog import IMPLEMENTED_STRATEGIES

PANEL_CACHE_ROOT = Path("/Users/neoclaw/claude/ifaenv/data/stock/replay_panels")
HORIZONS = (5, 10, 20)
ALL_SIGNAL_KEYS: tuple[str, ...] = tuple(item.key for item in IMPLEMENTED_STRATEGIES)


@dataclass(frozen=True)
class PanelRow:
    ts_code: str
    as_of_date: dt.date
    entry_close: float
    signals: dict[str, dict[str, Any]]   # key -> {score, status, weight, cluster}
    forward_5d_return: float | None
    forward_10d_return: float | None
    forward_20d_return: float | None
    forward_5d_target_first: bool | None
    forward_10d_target_first: bool | None
    forward_20d_target_first: bool | None
    forward_5d_stop_first: bool | None
    forward_10d_stop_first: bool | None
    forward_20d_stop_first: bool | None
    forward_5d_max_drawdown: float | None
    forward_10d_max_drawdown: float | None
    forward_20d_max_drawdown: float | None
    forward_5d_mfe: float | None
    forward_10d_mfe: float | None
    forward_20d_mfe: float | None
    forward_available_days: int
    regime: str | None = None
    decision_5d_score_baseline: float | None = None
    decision_10d_score_baseline: float | None = None
    decision_20d_score_baseline: float | None = None


@dataclass(frozen=True)
class PanelManifest:
    universe_id: str
    universe_size: int
    as_of_dates: list[dt.date]
    base_param_hash: str
    skip_llm: bool
    n_rows: int
    built_at: dt.datetime
    panel_path: str
    manifest_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "as_of_dates": [d.isoformat() for d in self.as_of_dates],
            "built_at": self.built_at.isoformat(),
        }


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────


def build_replay_panel(
    engine_url: str,
    *,
    ts_codes: Sequence[str],
    as_of_dates: Sequence[dt.date],
    base_params: dict[str, Any] | None = None,
    universe_id: str = "ad_hoc",
    skip_llm: bool = True,
    n_workers: int | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    target_return_pct_by_horizon: dict[int, float] | None = None,
) -> tuple[list[PanelRow], PanelManifest]:
    """Build PIT panel for (ts_codes × as_of_dates).

    `engine_url` is the SQLAlchemy URL string (not a live engine) so workers can each
    construct their own NullPool engine after fork.
    """
    base_params = base_params or load_params()
    targets = target_return_pct_by_horizon or {5: 5.0, 10: 8.0, 20: 20.0}
    base_hash = params_hash(_strip_unstable_params(base_params))

    cache_path = _panel_cache_path(universe_id, as_of_dates, base_hash, skip_llm)
    manifest_path = cache_path.with_suffix(".manifest.json")
    if cache_path.exists() and manifest_path.exists():
        rows = _load_panel_parquet(cache_path)
        manifest = _load_manifest(manifest_path)
        if on_progress:
            on_progress({"event": "cache_hit", "rows": len(rows), "path": str(cache_path)})
        return rows, manifest

    # Dispatch by (as_of_date, [ts_codes]) chunks so gateway sector cache hits within a chunk
    chunks: list[tuple[dt.date, list[str]]] = [(date, list(ts_codes)) for date in as_of_dates]
    total_pairs = sum(len(stocks) for _, stocks in chunks)
    started = time.monotonic()
    n_workers = n_workers or max(1, (os.cpu_count() or 4) - 1)

    rows: list[PanelRow] = []
    failed = 0
    completed = 0
    if n_workers <= 1:
        for as_of, codes in chunks:
            chunk_rows, chunk_fail = _build_chunk_for_date(engine_url, as_of, codes, base_params, skip_llm, targets)
            rows.extend(chunk_rows)
            failed += chunk_fail
            completed += len(codes)
            if on_progress:
                _emit_progress(on_progress, completed, total_pairs, len(rows), failed, started)
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            futures = {
                ex.submit(_build_chunk_for_date, engine_url, as_of, codes, base_params, skip_llm, targets): (as_of, codes)
                for as_of, codes in chunks
            }
            for fut in as_completed(futures):
                as_of, codes = futures[fut]
                try:
                    chunk_rows, chunk_fail = fut.result()
                except Exception as exc:
                    chunk_rows, chunk_fail = [], len(codes)
                    if on_progress:
                        on_progress({"event": "row_error", "error": repr(exc)})
                rows.extend(chunk_rows)
                failed += chunk_fail
                completed += len(codes)
                if on_progress:
                    _emit_progress(on_progress, completed, total_pairs, len(rows), failed, started)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    _save_panel_parquet(rows, cache_path)
    manifest = PanelManifest(
        universe_id=universe_id,
        universe_size=len(ts_codes),
        as_of_dates=list(as_of_dates),
        base_param_hash=base_hash,
        skip_llm=skip_llm,
        n_rows=len(rows),
        built_at=dt.datetime.now(dt.timezone.utc),
        panel_path=str(cache_path),
        manifest_path=str(manifest_path),
    )
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return rows, manifest


def load_replay_panel(panel_path: Path) -> list[PanelRow]:
    return _load_panel_parquet(panel_path)


# ──────────────────────────────────────────────────────────────────────────
# Worker entry — must be picklable (top-level function)
# ──────────────────────────────────────────────────────────────────────────


def _build_chunk_for_date(
    engine_url: str,
    as_of_date: dt.date,
    ts_codes: list[str],
    base_params: dict[str, Any],
    skip_llm: bool,
    target_pct_by_horizon: dict[int, float],
) -> tuple[list[PanelRow], int]:
    """Worker entry: build all rows for one (as_of_date, [ts_codes]) chunk.

    The shared `_PanelGateway` caches sector queries by l2_code so stocks in the
    same sector share the heavy CTE peer query instead of re-running it.
    """
    # QueuePool with size=2 reuses connections within a worker; ~30 queries per row
    # × 30 stocks per chunk × 6 dates = ~5400 queries per worker. Reusing connections
    # avoids ~5400 × ~5ms connect overhead = ~25s per worker.
    engine = create_engine(engine_url, poolclass=QueuePool, pool_size=2, max_overflow=2, pool_recycle=3600)
    try:
        gateway = _PanelGateway(engine)
        # T3.1: batch-load all stock-keyed tables for this chunk in 3 queries
        # (replaces 3 × len(ts_codes) per-stock queries)
        try:
            params = _params_for_panel(base_params, skip_llm=skip_llm)
            data_cfg = params.get("data", {})
            tech_window = int(data_cfg.get("technical_lookback_days", 60))
            default_lookback = int(params.get("runtime", {}).get("default_lookback_days", 7))
            gateway.preload_chunk(
                list(ts_codes),
                as_of_date,
                daily_window=max(default_lookback, tech_window),
                basic_window=max(20, default_lookback),
                moneyflow_window=max(20, default_lookback),
            )
        except Exception:
            # If preload fails, fall back to per-stock queries (no perf gain but correct)
            pass
        rows: list[PanelRow] = []
        failed = 0
        for ts_code in ts_codes:
            row = _build_one_row_with_gateway(
                engine, gateway, ts_code, as_of_date, base_params, skip_llm, target_pct_by_horizon
            )
            if row is None:
                failed += 1
            else:
                rows.append(row)
        return rows, failed
    finally:
        engine.dispose()


def _build_one_row(
    engine_url: str,
    ts_code: str,
    as_of_date: dt.date,
    base_params: dict[str, Any],
    skip_llm: bool,
    target_pct_by_horizon: dict[int, float],
) -> PanelRow | None:
    """Stand-alone single-row builder (used by tests and one-off profiling)."""
    # QueuePool with size=2 reuses connections within a worker; ~30 queries per row
    # × 30 stocks per chunk × 6 dates = ~5400 queries per worker. Reusing connections
    # avoids ~5400 × ~5ms connect overhead = ~25s per worker.
    engine = create_engine(engine_url, poolclass=QueuePool, pool_size=2, max_overflow=2, pool_recycle=3600)
    try:
        gateway = _PanelGateway(engine)
        return _build_one_row_with_gateway(
            engine, gateway, ts_code, as_of_date, base_params, skip_llm, target_pct_by_horizon
        )
    finally:
        engine.dispose()


def _build_one_row_with_gateway(
    engine: Engine,
    gateway: "_PanelGateway",
    ts_code: str,
    as_of_date: dt.date,
    base_params: dict[str, Any],
    skip_llm: bool,
    target_pct_by_horizon: dict[int, float],
) -> PanelRow | None:
    """Inner builder that reuses a passed-in cached gateway across multiple stocks."""
    try:
        params = _params_for_panel(base_params, skip_llm=skip_llm)
        request = StockEdgeRequest(
            ts_code=ts_code,
            requested_at=dt.datetime.combine(as_of_date, dt.time(15, 30)),
            mode="quick",
            run_mode="test",
        )
        try:
            ctx = build_context(request, engine=engine, params=params)
        except Exception:
            return None
        # Force as_of to the requested date if calendar resolution drifted
        if ctx.as_of.as_of_trade_date != as_of_date:
            return None
        try:
            snapshot = build_local_snapshot(ctx, gateway=gateway, allow_backfill=False)
        except Exception:
            return None
        try:
            matrix = compute_strategy_matrix(snapshot)
        except Exception:
            return None
        signals = {}
        for sig in matrix.get("signals") or []:
            key = sig.get("key")
            if not key:
                continue
            signals[str(key)] = {
                "score": float(sig.get("score") or 0.0),
                "weight": float(sig.get("weight") or 0.0),
                "status": str(sig.get("status") or "active"),
                "cluster": str(sig.get("cluster") or ""),
                "direction": str(sig.get("direction") or "neutral"),
            }

        # Compute baseline horizon scores INLINE from cached signals + decision_layer params.
        # Avoids a second compute_strategy_matrix call (saves ~8s).
        decision_cfg = params.get("decision_layer", {})
        baseline_5d = _baseline_horizon_score(signals, decision_cfg, "5d")
        baseline_10d = _baseline_horizon_score(signals, decision_cfg, "10d")
        baseline_20d = _baseline_horizon_score(signals, decision_cfg, "20d")

        # Forward labels — read PIT from DB without going through gateway
        forward = _forward_labels_from_db(engine, ts_code, as_of_date, target_pct_by_horizon)
        if forward is None:
            return None
        regime = _regime_for_date(engine, as_of_date)
        return PanelRow(
            ts_code=ts_code,
            as_of_date=as_of_date,
            entry_close=forward["entry_close"],
            signals=signals,
            forward_5d_return=forward.get("return_5d"),
            forward_10d_return=forward.get("return_10d"),
            forward_20d_return=forward.get("return_20d"),
            forward_5d_target_first=forward.get("target_first_5d"),
            forward_10d_target_first=forward.get("target_first_10d"),
            forward_20d_target_first=forward.get("target_first_20d"),
            forward_5d_stop_first=forward.get("stop_first_5d"),
            forward_10d_stop_first=forward.get("stop_first_10d"),
            forward_20d_stop_first=forward.get("stop_first_20d"),
            forward_5d_max_drawdown=forward.get("max_drawdown_5d"),
            forward_10d_max_drawdown=forward.get("max_drawdown_10d"),
            forward_20d_max_drawdown=forward.get("max_drawdown_20d"),
            forward_5d_mfe=forward.get("mfe_5d"),
            forward_10d_mfe=forward.get("mfe_10d"),
            forward_20d_mfe=forward.get("mfe_20d"),
            forward_available_days=int(forward.get("available_days") or 0),
            regime=regime,
            decision_5d_score_baseline=baseline_5d,
            decision_10d_score_baseline=baseline_10d,
            decision_20d_score_baseline=baseline_20d,
        )
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


class _PanelGateway(LocalDataGateway):
    """Gateway optimized for panel build.

    Two perf wins:
    1. Skip `load_model_context` (SmartMoney RF/XGB / Ningbo / Kronos PKL loads, ~6s saved)
    2. Cache heavy sector queries by (l2_code, as_of_date) — peer query inside
       `load_sector_membership` is sector-keyed, so 30 stocks in 5 sectors hit the
       slow CTE only 5 times, not 30. Saves ~3s × 25 = 75s per (date, universe).
    """

    def __init__(self, engine):
        super().__init__(engine)
        # Per-instance caches; one gateway = one worker = many (ts_code, date) within a date batch
        self._sector_lookup_cache: dict[tuple[str, dt.date], dict[str, Any]] = {}
        self._sector_data_cache: dict[tuple[str, dt.date], tuple[list, Any, Any, list, Any]] = {}
        # T3.1 batch caches: per-(table, ts_code) DataFrame slices preloaded at chunk start
        self._daily_bars_cache: dict[str, pd.DataFrame] = {}
        self._daily_basic_cache: dict[str, pd.DataFrame] = {}
        self._moneyflow_cache: dict[str, pd.DataFrame] = {}
        self._chunk_as_of: dt.date | None = None

    def preload_chunk(
        self,
        ts_codes: list[str],
        as_of_date: dt.date,
        *,
        daily_window: int = 60,
        basic_window: int = 20,
        moneyflow_window: int = 20,
    ) -> None:
        """Batch-load 3 stock-keyed tables for all stocks in chunk in 3 queries.

        Replaces ~300 individual queries (100 stocks × 3 tables) with 3 batch queries
        per chunk. Worker reuses the cache for all subsequent load_daily_bars /
        load_daily_basic / load_moneyflow calls.
        """
        self._chunk_as_of = as_of_date
        self._daily_bars_cache.clear()
        self._daily_basic_cache.clear()
        self._moneyflow_cache.clear()
        if not ts_codes:
            return

        # Use a date-window approach: pull all rows in (as_of - max_window_days .. as_of)
        # for every stock at once, then per-stock filter+truncate happens in load_*.
        # Take a generous lookback to cover the largest needed window across callers.
        history_days = max(daily_window, basic_window, moneyflow_window) * 3   # safety
        start_date = as_of_date - dt.timedelta(days=history_days)

        with self.engine.connect() as conn:
            # daily bars (deepest window)
            df_daily = pd.read_sql_query(
                text("""
                    SELECT ts_code, trade_date, open, high, low, close, pre_close,
                           change_, pct_chg, vol, amount
                    FROM smartmoney.raw_daily
                    WHERE ts_code = ANY(:codes)
                      AND trade_date <= :as_of
                      AND trade_date >= :start
                """),
                conn,
                params={"codes": list(ts_codes), "as_of": as_of_date, "start": start_date},
            )
            df_basic = pd.read_sql_query(
                text("""
                    SELECT ts_code, trade_date, close, turnover_rate, turnover_rate_f,
                           volume_ratio, pe, pe_ttm, pb, ps, ps_ttm, total_mv, circ_mv
                    FROM smartmoney.raw_daily_basic
                    WHERE ts_code = ANY(:codes)
                      AND trade_date <= :as_of
                      AND trade_date >= :start
                """),
                conn,
                params={"codes": list(ts_codes), "as_of": as_of_date, "start": start_date},
            )
            df_flow = pd.read_sql_query(
                text("""
                    SELECT ts_code, trade_date, buy_lg_amount, sell_lg_amount,
                           buy_elg_amount, sell_elg_amount, net_mf_amount
                    FROM smartmoney.raw_moneyflow
                    WHERE ts_code = ANY(:codes)
                      AND trade_date <= :as_of
                      AND trade_date >= :start
                """),
                conn,
                params={"codes": list(ts_codes), "as_of": as_of_date, "start": start_date},
            )
        for df, cache in (
            (df_daily, self._daily_bars_cache),
            (df_basic, self._daily_basic_cache),
            (df_flow, self._moneyflow_cache),
        ):
            if df.empty:
                continue
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
            for code, sub in df.groupby("ts_code"):
                cache[str(code)] = sub.sort_values("trade_date").reset_index(drop=True)

    def _serve_from_cache(
        self,
        cache: dict[str, pd.DataFrame],
        name: str,
        ts_code: str,
        as_of: dt.date,
        lookback_rows: int,
        min_rows: int,
        required: bool,
    ) -> LoadResult | None:
        """If chunk pre-loaded for this (ts_code, as_of), serve from cache."""
        if self._chunk_as_of != as_of or ts_code not in cache:
            return None
        full = cache[ts_code]
        sub = full[full["trade_date"] <= as_of].tail(lookback_rows).reset_index(drop=True)
        if sub.empty:
            return None
        status = "ok" if len(sub) >= min_rows else "partial"
        return LoadResult(
            name=name,
            data=sub,
            source="postgres_cached",
            status=status,
            rows=len(sub),
            as_of=sub["trade_date"].iloc[-1],
            required=required,
        )

    def load_daily_bars(self, ts_code, as_of_trade_date, *, lookback_rows=60, min_rows=20, required=True):
        cached = self._serve_from_cache(self._daily_bars_cache, "daily_bars", ts_code, as_of_trade_date, lookback_rows, min_rows, required)
        if cached is not None:
            return cached
        return super().load_daily_bars(ts_code, as_of_trade_date, lookback_rows=lookback_rows, min_rows=min_rows, required=required)

    def load_daily_basic(self, ts_code, as_of_trade_date, *, lookback_rows=20, min_rows=5, required=True):
        cached = self._serve_from_cache(self._daily_basic_cache, "daily_basic", ts_code, as_of_trade_date, lookback_rows, min_rows, required)
        if cached is not None:
            return cached
        return super().load_daily_basic(ts_code, as_of_trade_date, lookback_rows=lookback_rows, min_rows=min_rows, required=required)

    def load_moneyflow(self, ts_code, as_of_trade_date, *, lookback_rows=20, min_rows=3, required=False):
        cached = self._serve_from_cache(self._moneyflow_cache, "moneyflow", ts_code, as_of_trade_date, lookback_rows, min_rows, required)
        if cached is not None:
            return cached
        return super().load_moneyflow(ts_code, as_of_trade_date, lookback_rows=lookback_rows, min_rows=min_rows, required=required)

    def load_model_context(self, ts_code, as_of_trade_date, sector_data):
        return LoadResult(
            name="model_context",
            data={},
            source="skipped_for_tuning",
            status="missing",
            rows=0,
            as_of=None,
            required=False,
            message="model_context skipped during tuning panel build",
        )

    def load_sector_membership(self, ts_code, as_of_trade_date):
        """Cached version: sector-keyed heavy queries cached by l2_code."""
        snapshot_month = as_of_trade_date.replace(day=1)
        member_key = (ts_code, snapshot_month)
        member = self._sector_lookup_cache.get(member_key)
        if member is None:
            with self.engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT snapshot_month, l1_code, l1_name, l2_code, l2_name, name
                        FROM smartmoney.sw_member_monthly
                        WHERE ts_code = :ts_code AND snapshot_month <= :snapshot_month
                        ORDER BY snapshot_month DESC LIMIT 1
                    """),
                    {"ts_code": ts_code, "snapshot_month": snapshot_month},
                ).mappings().fetchone()
            member = dict(row) if row else {}
            self._sector_lookup_cache[member_key] = member

        if not member:
            return LoadResult(name="sector_membership", data={}, source="missing", status="missing",
                              rows=0, as_of=None, required=False, message="未找到 SW 成员")

        l2_code = member.get("l2_code")
        sector_data_key = (str(l2_code), as_of_trade_date)
        cached = self._sector_data_cache.get(sector_data_key)
        if cached is None:
            with self.engine.connect() as conn:
                sector_flow = [dict(r) for r in conn.execute(
                    text("""
                        SELECT trade_date, l2_code, l2_name, l1_code, l1_name,
                               net_amount, buy_elg_amount, sell_elg_amount,
                               buy_lg_amount, sell_lg_amount, stock_count
                        FROM smartmoney.sector_moneyflow_sw_daily
                        WHERE l2_code = :l2_code AND trade_date <= :as_of
                        ORDER BY trade_date DESC LIMIT 7
                    """),
                    {"l2_code": l2_code, "as_of": as_of_trade_date},
                ).mappings()]
                sector_state = conn.execute(
                    text("""
                        SELECT trade_date, sector_code, sector_source, sector_name,
                               role, cycle_phase, role_confidence, phase_confidence, evidence_json
                        FROM smartmoney.sector_state_daily
                        WHERE sector_code = :l2_code AND trade_date <= :as_of
                        ORDER BY trade_date DESC LIMIT 1
                    """),
                    {"l2_code": l2_code, "as_of": as_of_trade_date},
                ).mappings().fetchone()
                sector_factor = conn.execute(
                    text("""
                        SELECT trade_date, sector_code, sector_source, sector_name,
                               heat_score, trend_score, persistence_score, crowding_score, derived_json
                        FROM smartmoney.factor_daily
                        WHERE sector_code = :l2_code AND trade_date <= :as_of
                        ORDER BY trade_date DESC LIMIT 1
                    """),
                    {"l2_code": l2_code, "as_of": as_of_trade_date},
                ).mappings().fetchone()
                sector_peers = [dict(r) for r in conn.execute(
                    text("""
                        WITH members AS (
                            SELECT ts_code, name FROM smartmoney.sw_member_monthly
                            WHERE snapshot_month = :snapshot_month AND l2_code = :l2_code
                        ),
                        latest_daily AS (
                            SELECT DISTINCT ON (d.ts_code)
                                   d.ts_code, d.trade_date, d.close, d.pct_chg, d.amount
                            FROM smartmoney.raw_daily d
                            JOIN members m ON m.ts_code = d.ts_code
                            WHERE d.trade_date <= :as_of
                            ORDER BY d.ts_code, d.trade_date DESC
                        )
                        SELECT m.ts_code, m.name, ld.close, ld.pct_chg, ld.amount
                        FROM members m
                        LEFT JOIN latest_daily ld USING (ts_code)
                        ORDER BY ld.amount DESC NULLS LAST LIMIT 30
                    """),
                    {"snapshot_month": snapshot_month, "l2_code": l2_code, "as_of": as_of_trade_date},
                ).mappings()]
            cached = (sector_flow, dict(sector_state) if sector_state else None,
                      dict(sector_factor) if sector_factor else None, sector_peers, member)
            self._sector_data_cache[sector_data_key] = cached
        sector_flow, sector_state, sector_factor, sector_peers, _ = cached

        data = {
            "ts_code": ts_code,
            "snapshot_month": member.get("snapshot_month"),
            "l1_code": member.get("l1_code"), "l1_name": member.get("l1_name"),
            "l2_code": l2_code, "l2_name": member.get("l2_name"),
            "name": member.get("name"),
            "sector_flow": sector_flow,
            "sector_state": sector_state,
            "sector_factor": sector_factor,
            "sector_peers": sector_peers,
        }
        return LoadResult(name="sector_membership", data=data, source="postgres", status="ok",
                          rows=1, as_of=as_of_trade_date, required=False)


def _baseline_horizon_score(
    signals: dict[str, dict[str, Any]],
    decision_cfg: dict[str, Any],
    horizon: str,
) -> float | None:
    """Replicate decision_layer._horizon_score on cached signals (no snapshot needed)."""
    horizons = decision_cfg.get("horizons") or {}
    cfg = horizons.get(horizon) or {}
    weights = cfg.get("weights") or {}
    keys = DEFAULT_KEYS.get(horizon, {})
    positive_keys = list(keys.get("positive", []))
    risk_keys = list(keys.get("risk", []))

    raw = 0.0
    denom = 0.0
    active = 0
    for key in positive_keys + risk_keys:
        sig = signals.get(key)
        if not sig or sig.get("status") == "missing":
            continue
        if key in positive_keys:
            w = float(weights.get(key, 1.0))
        else:
            w = float(weights.get(key, weights.get("risk_penalty_weight", 1.0)))
        raw += float(sig.get("score") or 0.0) * w
        denom += abs(w)
        active += 1
    if denom == 0 or active == 0:
        return None
    edge = raw / denom
    base = float(cfg.get("base_score", 0.50))
    scale = float(cfg.get("raw_edge_scale", 0.50))
    return max(0.0, min(1.0, base + edge * scale))


def _params_for_panel(base_params: dict[str, Any], *, skip_llm: bool) -> dict[str, Any]:
    """Return params with LLM signals neutralized and intraday disabled (for tuning panels only)."""
    params = json.loads(json.dumps(base_params, default=str))
    if skip_llm:
        sig_weights = params.setdefault("strategy_matrix", {}).setdefault("signal_weights", {})
        for key in ("event_catalyst_llm", "fundamental_contradiction_llm", "llm_regime_cache", "llm_counterfactual_cache", "scenario_tree_llm"):
            sig_weights[key] = 0.0
    intraday = params.setdefault("intraday", {})
    intraday["enabled"] = False
    intraday["backfill_on_missing"] = False
    research_prefetch = params.setdefault("research_prefetch", {})
    research_prefetch["enabled"] = False
    return params


def _strip_unstable_params(base_params: dict[str, Any]) -> dict[str, Any]:
    """Remove fields that change per-run but don't affect signal/decision logic, for cache-key hashing."""
    params = json.loads(json.dumps(base_params, default=str))
    for k in ("runtime", "data", "cache", "research_prefetch", "intraday"):
        params.pop(k, None)
    return params


def _panel_cache_path(universe_id: str, as_of_dates: Sequence[dt.date], base_hash: str, skip_llm: bool) -> Path:
    sorted_dates = sorted(as_of_dates)
    date_sig = f"{sorted_dates[0].isoformat()}_{sorted_dates[-1].isoformat()}_{len(sorted_dates)}"
    suffix = hashlib.sha256(f"{universe_id}|{date_sig}|{base_hash}|llm={skip_llm}".encode()).hexdigest()[:12]
    fname = f"{universe_id}__{sorted_dates[0]:%Y%m%d}_{sorted_dates[-1]:%Y%m%d}__{suffix}.parquet"
    return PANEL_CACHE_ROOT / fname


def _load_manifest(path: Path) -> PanelManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return PanelManifest(
        universe_id=raw["universe_id"],
        universe_size=int(raw["universe_size"]),
        as_of_dates=[dt.date.fromisoformat(d) for d in raw["as_of_dates"]],
        base_param_hash=raw["base_param_hash"],
        skip_llm=bool(raw["skip_llm"]),
        n_rows=int(raw["n_rows"]),
        built_at=dt.datetime.fromisoformat(raw["built_at"]),
        panel_path=raw["panel_path"],
        manifest_path=raw["manifest_path"],
    )


def _emit_progress(cb: Callable, idx: int, total: int, ok: int, failed: int, started: float) -> None:
    elapsed = time.monotonic() - started
    eta = elapsed / idx * max(total - idx, 0) if idx else 0.0
    cb({
        "event": "progress",
        "completed": idx,
        "total": total,
        "ok": ok,
        "failed": failed,
        "elapsed_sec": round(elapsed, 1),
        "eta_sec": round(eta, 1),
        "rate_per_min": round(idx * 60.0 / max(elapsed, 0.01), 1),
    })


def _forward_labels_from_db(
    engine: Engine,
    ts_code: str,
    as_of_date: dt.date,
    target_pct_by_horizon: dict[int, float],
) -> dict[str, Any] | None:
    """Read forward bars via raw SQL and compute 5/10/20 horizon labels in-process."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT trade_date, open, high, low, close
            FROM smartmoney.raw_daily
            WHERE ts_code = :tc AND trade_date >= :start AND trade_date <= :end
            ORDER BY trade_date ASC
        """), {
            "tc": ts_code,
            "start": as_of_date,
            "end": as_of_date + dt.timedelta(days=45),
        }).fetchall()
    if not rows or rows[0][0] != as_of_date:
        return None
    entry = float(rows[0][4]) if rows[0][4] else 0.0
    if entry <= 0:
        return None
    out: dict[str, Any] = {"entry_close": entry, "available_days": len(rows) - 1}
    for h in HORIZONS:
        future = rows[1 : 1 + h]
        if len(future) < h:
            out[f"return_{h}d"] = None
            out[f"target_first_{h}d"] = None
            out[f"stop_first_{h}d"] = None
            out[f"max_drawdown_{h}d"] = None
            out[f"mfe_{h}d"] = None
            continue
        target_pct = target_pct_by_horizon.get(h, {5: 5.0, 10: 8.0, 20: 20.0}[h]) / 100.0
        stop_pct = 0.08
        target_price = entry * (1 + target_pct)
        stop_price = entry * (1 - stop_pct)
        first_event: str | None = None
        for _, _o, hi, lo, _c in future:
            hi_f = float(hi) if hi else 0.0
            lo_f = float(lo) if lo else 0.0
            hit_target_today = hi_f >= target_price
            hit_stop_today = lo_f <= stop_price
            if hit_stop_today and hit_target_today:
                first_event = "stop"  # ambiguous → conservative
                break
            if hit_stop_today:
                first_event = "stop"
                break
            if hit_target_today:
                first_event = "target"
                break
        max_h = max(float(r[2]) for r in future if r[2] is not None)
        min_l = min(float(r[3]) for r in future if r[3] is not None)
        final_close = float(future[-1][4])
        out[f"return_{h}d"] = round((final_close / entry - 1) * 100.0, 4)
        out[f"target_first_{h}d"] = first_event == "target"
        out[f"stop_first_{h}d"] = first_event == "stop"
        out[f"max_drawdown_{h}d"] = round((min_l / entry - 1) * 100.0, 4)
        out[f"mfe_{h}d"] = round((max_h / entry - 1) * 100.0, 4)
    return out


def _regime_for_date(engine: Engine, as_of_date: dt.date) -> str | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT regime FROM ta.regime_daily WHERE trade_date = :d"),
                {"d": as_of_date},
            ).fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────
# Parquet I/O — wide format for vectorized eval
# ──────────────────────────────────────────────────────────────────────────


def _save_panel_parquet(rows: list[PanelRow], path: Path) -> None:
    if not rows:
        pd.DataFrame().to_parquet(path)
        return
    out: list[dict[str, Any]] = []
    for r in rows:
        rec: dict[str, Any] = {
            "ts_code": r.ts_code,
            "as_of_date": r.as_of_date,
            "entry_close": r.entry_close,
            "regime": r.regime,
            "forward_available_days": r.forward_available_days,
            "decision_5d_baseline": r.decision_5d_score_baseline,
            "decision_10d_baseline": r.decision_10d_score_baseline,
            "decision_20d_baseline": r.decision_20d_score_baseline,
        }
        for h in HORIZONS:
            rec[f"forward_{h}d_return"] = getattr(r, f"forward_{h}d_return")
            rec[f"forward_{h}d_target_first"] = getattr(r, f"forward_{h}d_target_first")
            rec[f"forward_{h}d_stop_first"] = getattr(r, f"forward_{h}d_stop_first")
            rec[f"forward_{h}d_max_drawdown"] = getattr(r, f"forward_{h}d_max_drawdown")
            rec[f"forward_{h}d_mfe"] = getattr(r, f"forward_{h}d_mfe")
        for key in ALL_SIGNAL_KEYS:
            sig = r.signals.get(key)
            rec[f"sig_score__{key}"] = float(sig["score"]) if sig else 0.0
            rec[f"sig_active__{key}"] = bool(sig and sig.get("status") != "missing")
            rec[f"sig_cluster__{key}"] = sig["cluster"] if sig else ""
        out.append(rec)
    df = pd.DataFrame(out)
    df.to_parquet(path, compression="snappy", index=False)


def _load_panel_parquet(path: Path) -> list[PanelRow]:
    if not path.exists():
        return []
    df = pd.read_parquet(path)
    if df.empty:
        return []
    rows: list[PanelRow] = []
    for record in df.to_dict(orient="records"):
        signals = {}
        for key in ALL_SIGNAL_KEYS:
            score = record.get(f"sig_score__{key}", 0.0)
            active = record.get(f"sig_active__{key}", False)
            cluster = record.get(f"sig_cluster__{key}", "")
            if active or score != 0.0:
                signals[key] = {
                    "score": float(score),
                    "status": "active" if active else "missing",
                    "cluster": str(cluster or ""),
                    "weight": 1.0,
                    "direction": "positive" if float(score) >= 0 else "negative",
                }
        rows.append(PanelRow(
            ts_code=str(record["ts_code"]),
            as_of_date=record["as_of_date"] if isinstance(record["as_of_date"], dt.date) else dt.date.fromisoformat(str(record["as_of_date"])[:10]),
            entry_close=float(record["entry_close"]),
            signals=signals,
            forward_5d_return=_opt_float(record.get("forward_5d_return")),
            forward_10d_return=_opt_float(record.get("forward_10d_return")),
            forward_20d_return=_opt_float(record.get("forward_20d_return")),
            forward_5d_target_first=_opt_bool(record.get("forward_5d_target_first")),
            forward_10d_target_first=_opt_bool(record.get("forward_10d_target_first")),
            forward_20d_target_first=_opt_bool(record.get("forward_20d_target_first")),
            forward_5d_stop_first=_opt_bool(record.get("forward_5d_stop_first")),
            forward_10d_stop_first=_opt_bool(record.get("forward_10d_stop_first")),
            forward_20d_stop_first=_opt_bool(record.get("forward_20d_stop_first")),
            forward_5d_max_drawdown=_opt_float(record.get("forward_5d_max_drawdown")),
            forward_10d_max_drawdown=_opt_float(record.get("forward_10d_max_drawdown")),
            forward_20d_max_drawdown=_opt_float(record.get("forward_20d_max_drawdown")),
            forward_5d_mfe=_opt_float(record.get("forward_5d_mfe")),
            forward_10d_mfe=_opt_float(record.get("forward_10d_mfe")),
            forward_20d_mfe=_opt_float(record.get("forward_20d_mfe")),
            forward_available_days=int(record.get("forward_available_days") or 0),
            regime=str(record["regime"]) if record.get("regime") else None,
            decision_5d_score_baseline=_opt_float(record.get("decision_5d_baseline")),
            decision_10d_score_baseline=_opt_float(record.get("decision_10d_baseline")),
            decision_20d_score_baseline=_opt_float(record.get("decision_20d_baseline")),
        ))
    return rows


def _opt_float(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _opt_bool(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return bool(v)
