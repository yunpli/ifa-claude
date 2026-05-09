#!/usr/bin/env python
"""End-to-end Stock Edge panel-based coarse tuning.

This is the production-aligned tuner: it builds a PIT replay panel by running the
real `compute_strategy_matrix` over (universe × dates), caches signals to parquet,
then runs random search over `decision_layer.horizons.*` weights/thresholds —
the actually load-bearing params for 5/10/20 decisions.

Usage:
    uv run python scripts/stock_edge_panel_tune.py \
        --as-of 2026-03-31 --top 50 --pit-samples 8 \
        --max-candidates 256 --workers -1 [--dry-run]

The legacy `scripts/stock_edge_global_preset.py` runs a SURROGATE optimizer
(unrelated to compute_strategy_matrix) — do not use it for production tuning.
"""
from __future__ import annotations

import argparse
import builtins
import datetime as dt
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.families.stock.backtest.optimizer import (
    fit_global_preset_successive_halving,
    fit_global_preset_via_panel,
)
from ifa.families.stock.backtest.panel_evaluator import (
    bootstrap_rank_ic_lift,
    evaluate_overlay_on_panel,
    k_fold_rolling_walk_forward,
    kfold_aggregate_ci,
    panel_matrix_from_rows,
    regime_bucketed_rank_ic_lift,
    walk_forward_split,
)
from ifa.families.stock.backtest.outcome_proxy import (
    benchmark_outcome_proxy_builders,
    build_outcome_proxy_cache,
    compare_proxy_candidate_families,
    score_proxy_candidate_families,
    summarize_outcome_proxy,
)
from ifa.families.stock.backtest.promotion import auto_promote_if_passing, evaluate_promotion_gates
from ifa.families.stock.backtest.replay_panel import build_replay_panel
from ifa.families.stock.backtest.tuning_artifact import write_tuning_artifact
from ifa.families.stock.params import load_params


def print(*args, **kwargs) -> None:
    """Flush status lines promptly for ACP/background runs."""
    kwargs.setdefault("flush", True)
    builtins.print(*args, **kwargs)


def _select_universe(
    engine,
    *,
    top_n: int,
    lookback_days: int = 20,
    as_of: dt.date,
    liquidity_offset: int = 0,
) -> list[str]:
    """Top N by 20-day average daily turnover, ending at as_of.

    `liquidity_offset` makes out-of-cohort validation explicit: offset 0 is the
    production top-liquidity train cohort, offset 100 gives the next 100 names.
    """
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT ts_code, AVG(amount) AS avg_amount
            FROM smartmoney.raw_daily
            WHERE trade_date <= :as_of AND trade_date >= :start
            GROUP BY ts_code
            HAVING COUNT(*) >= :min_days
            ORDER BY avg_amount DESC
            LIMIT :n
            OFFSET :offset
        """), {
            "as_of": as_of,
            "start": as_of - dt.timedelta(days=lookback_days * 2),
            "min_days": int(lookback_days * 0.7),
            "n": top_n,
            "offset": liquidity_offset,
        }).all()
    return [r[0] for r in rows]


def _select_stratified_pit_universe(
    engine,
    *,
    top_n: int,
    as_of: dt.date,
    lookback_days: int = 60,
    pool_multiple: int = 8,
) -> tuple[list[str], dict[str, Any]]:
    """PIT-safe stratified universe using only rows <= as_of.

    Minimum viable stratification dimensions:
      1. liquidity bucket: 60d average amount
      2. market-cap bucket: latest total_mv
      3. SW L1 industry bucket

    Volatility is computed and recorded as a diagnostic dimension; it is not
    part of the hard partition in MVP1 because industry x liquidity x size x
    volatility can over-fragment small panels.
    """
    pool_n = max(top_n, top_n * max(2, pool_multiple))
    start = as_of - dt.timedelta(days=lookback_days * 2)
    snapshot_month = as_of.replace(day=1)
    with engine.connect() as c:
        rows = c.execute(text("""
            WITH daily_window AS (
                SELECT ts_code,
                       AVG(amount) AS avg_amount,
                       STDDEV_SAMP(pct_chg) AS vol_pct,
                       COUNT(*) AS n_days
                FROM smartmoney.raw_daily
                WHERE trade_date <= :as_of AND trade_date >= :start
                GROUP BY ts_code
                HAVING COUNT(*) >= :min_days
            ),
            latest_basic AS (
                SELECT DISTINCT ON (ts_code)
                       ts_code, total_mv, circ_mv
                FROM smartmoney.raw_daily_basic
                WHERE trade_date <= :as_of
                ORDER BY ts_code, trade_date DESC
            ),
            members AS (
                SELECT DISTINCT ON (ts_code)
                       ts_code, l1_code, l1_name, l2_code, l2_name
                FROM smartmoney.sw_member_monthly
                WHERE snapshot_month <= :snapshot_month
                ORDER BY ts_code, snapshot_month DESC
            ),
            ranked_pool AS (
                SELECT d.ts_code,
                       d.avg_amount,
                       d.vol_pct,
                       COALESCE(b.total_mv, b.circ_mv, 0) AS mv,
                       COALESCE(m.l1_code, 'UNKNOWN') AS l1_code,
                       COALESCE(m.l1_name, 'UNKNOWN') AS l1_name
                FROM daily_window d
                LEFT JOIN latest_basic b USING (ts_code)
                LEFT JOIN members m USING (ts_code)
                ORDER BY d.avg_amount DESC NULLS LAST
                LIMIT :pool_n
            ),
            bucketed AS (
                SELECT *,
                       NTILE(3) OVER (ORDER BY avg_amount NULLS FIRST) AS liquidity_bucket,
                       NTILE(3) OVER (ORDER BY mv NULLS FIRST) AS size_bucket,
                       NTILE(3) OVER (ORDER BY COALESCE(vol_pct, 0) NULLS FIRST) AS volatility_bucket
                FROM ranked_pool
            )
            SELECT ts_code, avg_amount, mv, vol_pct, l1_code, l1_name,
                   liquidity_bucket, size_bucket, volatility_bucket
            FROM bucketed
            ORDER BY l1_code, liquidity_bucket, size_bucket, md5(ts_code || :seed)
        """), {
            "as_of": as_of,
            "start": start,
            "snapshot_month": snapshot_month,
            "min_days": int(lookback_days * 0.55),
            "pool_n": pool_n,
            "seed": as_of.isoformat(),
        }).mappings().all()
    buckets: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row["l1_code"] or "UNKNOWN"), int(row["liquidity_bucket"] or 0), int(row["size_bucket"] or 0))
        buckets[key].append(dict(row))
    selected: list[dict[str, Any]] = []
    for level in range(max((len(v) for v in buckets.values()), default=0)):
        for key in sorted(buckets):
            items = buckets[key]
            if level < len(items):
                selected.append(items[level])
                if len(selected) >= top_n:
                    break
        if len(selected) >= top_n:
            break
    codes = [str(row["ts_code"]) for row in selected]
    meta = {
        "selection_as_of": as_of.isoformat(),
        "lookback_start": start.isoformat(),
        "lookback_days": lookback_days,
        "candidate_pool_rows": len(rows),
        "pool_multiple": pool_multiple,
        "selected_count": len(codes),
        "membership_hash": _digest_codes(codes),
        "dimensions": ["sw_l1_industry", "liquidity_bucket", "size_bucket", "volatility_bucket_diagnostic"],
        "sample_head": codes[:10],
        "strata_counts": _strata_counts(selected),
    }
    return codes, meta


def _select_stratified_pit_universes_batch(
    engine,
    *,
    top_n: int,
    as_of_dates: list[dt.date],
    lookback_days: int = 60,
    pool_multiple: int = 8,
) -> tuple[dict[dt.date, list[str]], dict[str, dict[str, Any]], dict[str, float]]:
    """Batch PIT-safe stratified universe selection for many dates.

    The single-date selector is correct but expensive for 60-date validation
    because each PIT date repeats the same daily/basic/member scans. This helper
    pulls the full date window once and reproduces the same stratification in
    pandas using only rows visible at each PIT date.
    """
    if not as_of_dates:
        return {}, {}, {"batch_total_sec": 0.0}
    t_all = time.monotonic()
    sorted_dates = sorted(as_of_dates)
    min_as_of = sorted_dates[0]
    max_as_of = sorted_dates[-1]
    daily_start = min_as_of - dt.timedelta(days=lookback_days * 2)
    min_days = int(lookback_days * 0.55)
    pool_n = max(top_n, top_n * max(2, pool_multiple))
    max_snapshot = max_as_of.replace(day=1)

    t0 = time.monotonic()
    with engine.connect() as c:
        daily = pd.read_sql_query(
            text("""
                SELECT ts_code, trade_date, amount, pct_chg
                FROM smartmoney.raw_daily
                WHERE trade_date <= :max_as_of AND trade_date >= :daily_start
            """),
            c,
            params={"max_as_of": max_as_of, "daily_start": daily_start},
        )
        basic = pd.read_sql_query(
            text("""
                SELECT ts_code, trade_date, total_mv, circ_mv
                FROM smartmoney.raw_daily_basic
                WHERE trade_date <= :max_as_of AND trade_date >= :daily_start
            """),
            c,
            params={"max_as_of": max_as_of, "daily_start": daily_start},
        )
        members = pd.read_sql_query(
            text("""
                SELECT ts_code, snapshot_month, l1_code, l1_name, l2_code, l2_name
                FROM smartmoney.sw_member_monthly
                WHERE snapshot_month <= :max_snapshot
            """),
            c,
            params={"max_snapshot": max_snapshot},
        )
    query_sec = time.monotonic() - t0

    t0 = time.monotonic()
    for df in (daily, basic):
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    if not members.empty:
        members["snapshot_month"] = pd.to_datetime(members["snapshot_month"]).dt.date
    daily = daily.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    basic = basic.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    members = members.sort_values(["snapshot_month", "ts_code"]).reset_index(drop=True)
    index_sec = time.monotonic() - t0

    by_date: dict[dt.date, list[str]] = {}
    by_date_meta: dict[str, dict[str, Any]] = {}
    t0 = time.monotonic()
    for as_of in sorted_dates:
        start = as_of - dt.timedelta(days=lookback_days * 2)
        dw = daily[(daily["trade_date"] <= as_of) & (daily["trade_date"] >= start)]
        if dw.empty:
            by_date[as_of] = []
            by_date_meta[as_of.isoformat()] = {
                "selection_as_of": as_of.isoformat(),
                "lookback_start": start.isoformat(),
                "lookback_days": lookback_days,
                "candidate_pool_rows": 0,
                "pool_multiple": pool_multiple,
                "selected_count": 0,
                "membership_hash": _digest_codes([]),
                "dimensions": ["sw_l1_industry", "liquidity_bucket", "size_bucket", "volatility_bucket_diagnostic"],
                "sample_head": [],
                "strata_counts": {},
                "selection_backend": "batch_pandas",
            }
            continue
        grouped = (
            dw.groupby("ts_code", sort=False)
            .agg(avg_amount=("amount", "mean"), vol_pct=("pct_chg", "std"), n_days=("trade_date", "count"))
            .reset_index()
        )
        ranked_pool = grouped[grouped["n_days"] >= min_days].sort_values("avg_amount", ascending=False).head(pool_n)

        basic_latest = _latest_rows_for_as_of(basic, "trade_date", as_of)
        if not basic_latest.empty:
            ranked_pool = ranked_pool.merge(basic_latest[["ts_code", "total_mv", "circ_mv"]], on="ts_code", how="left")
        else:
            ranked_pool["total_mv"] = pd.NA
            ranked_pool["circ_mv"] = pd.NA
        snapshot_month = as_of.replace(day=1)
        member_latest = _latest_rows_for_as_of(members, "snapshot_month", snapshot_month)
        if not member_latest.empty:
            ranked_pool = ranked_pool.merge(member_latest[["ts_code", "l1_code", "l1_name", "l2_code", "l2_name"]], on="ts_code", how="left")
        else:
            ranked_pool["l1_code"] = "UNKNOWN"
            ranked_pool["l1_name"] = "UNKNOWN"
            ranked_pool["l2_code"] = None
            ranked_pool["l2_name"] = None

        ranked_pool["mv"] = ranked_pool["total_mv"].fillna(ranked_pool["circ_mv"]).fillna(0)
        ranked_pool["l1_code"] = ranked_pool["l1_code"].fillna("UNKNOWN")
        ranked_pool["l1_name"] = ranked_pool["l1_name"].fillna("UNKNOWN")
        ranked_pool["liquidity_bucket"] = _ntile(ranked_pool["avg_amount"], 3)
        ranked_pool["size_bucket"] = _ntile(ranked_pool["mv"], 3)
        ranked_pool["volatility_bucket"] = _ntile(ranked_pool["vol_pct"].fillna(0), 3)
        seed = as_of.isoformat()
        ranked_pool["_stable_order"] = ranked_pool["ts_code"].astype(str).map(lambda code: _stable_md5(f"{code}{seed}"))
        ranked_pool = ranked_pool.sort_values(["l1_code", "liquidity_bucket", "size_bucket", "_stable_order"])

        buckets: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
        for row in ranked_pool.to_dict(orient="records"):
            key = (str(row.get("l1_code") or "UNKNOWN"), int(row.get("liquidity_bucket") or 0), int(row.get("size_bucket") or 0))
            buckets[key].append(row)
        selected: list[dict[str, Any]] = []
        for level in range(max((len(v) for v in buckets.values()), default=0)):
            for key in sorted(buckets):
                items = buckets[key]
                if level < len(items):
                    selected.append(items[level])
                    if len(selected) >= top_n:
                        break
            if len(selected) >= top_n:
                break
        codes = [str(row["ts_code"]) for row in selected]
        by_date[as_of] = codes
        by_date_meta[as_of.isoformat()] = {
            "selection_as_of": as_of.isoformat(),
            "lookback_start": start.isoformat(),
            "lookback_days": lookback_days,
            "candidate_pool_rows": int(len(ranked_pool)),
            "pool_multiple": pool_multiple,
            "selected_count": len(codes),
            "membership_hash": _digest_codes(codes),
            "dimensions": ["sw_l1_industry", "liquidity_bucket", "size_bucket", "volatility_bucket_diagnostic"],
            "sample_head": codes[:10],
            "strata_counts": _strata_counts(selected),
            "selection_backend": "batch_pandas",
        }
    assemble_sec = time.monotonic() - t0
    return by_date, by_date_meta, {
        "batch_total_sec": round(time.monotonic() - t_all, 3),
        "query_sec": round(query_sec, 3),
        "index_sec": round(index_sec, 3),
        "assemble_sec": round(assemble_sec, 3),
        "query_daily_rows": float(len(daily)),
        "query_basic_rows": float(len(basic)),
        "query_member_rows": float(len(members)),
    }


def _latest_rows_for_as_of(df: pd.DataFrame, date_col: str, as_of: dt.date) -> pd.DataFrame:
    if df.empty:
        return df
    sub = df[df[date_col] <= as_of]
    if sub.empty:
        return sub
    return sub.sort_values(["ts_code", date_col]).groupby("ts_code", sort=False).tail(1)


def _ntile(values: pd.Series, n: int) -> pd.Series:
    if values.empty:
        return pd.Series(dtype=int)
    ranks = values.fillna(0).rank(method="first", ascending=True)
    return ((ranks - 1) * n / len(values)).astype(int) + 1


def _stable_md5(value: str) -> str:
    import hashlib

    return hashlib.md5(value.encode()).hexdigest()


def _strata_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "l1_code": dict(Counter(str(r.get("l1_code") or "UNKNOWN") for r in rows).most_common()),
        "liquidity_bucket": dict(Counter(str(r.get("liquidity_bucket") or "unknown") for r in rows).most_common()),
        "size_bucket": dict(Counter(str(r.get("size_bucket") or "unknown") for r in rows).most_common()),
        "volatility_bucket": dict(Counter(str(r.get("volatility_bucket") or "unknown") for r in rows).most_common()),
    }


def _digest_codes(codes: list[str]) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(codes, ensure_ascii=False).encode()).hexdigest()[:16]


def _select_pit_dates(
    engine,
    *,
    n_samples: int,
    latest_as_of: dt.date,
    forward_min_days: int = 25,
    lookback_days: int = 18 * 30,
) -> list[dt.date]:
    """Pick N trading days that have at least `forward_min_days` of future bars in DB.

    Strategy: take all SSE trading days between (latest - lookback_days) and (latest - forward_min_days * 1.5/business),
    sort descending, evenly sample.
    """
    horizon_days = max(35, int(forward_min_days * 1.5))
    end_max = latest_as_of - dt.timedelta(days=horizon_days)
    start = latest_as_of - dt.timedelta(days=max(30, lookback_days))
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT cal_date FROM smartmoney.trade_cal
            WHERE exchange = 'SSE' AND is_open = true
              AND cal_date >= :start AND cal_date <= :end
            ORDER BY cal_date DESC
        """), {"start": start, "end": end_max}).all()
    days = [r[0] for r in rows]
    if not days or len(days) < n_samples:
        return days
    # Even spacing across the window for regime diversity
    step = max(1, len(days) // n_samples)
    sampled = days[::step][:n_samples]
    return sorted(sampled)


def _cheap_proxy_rows(rows: list, *, max_rows: int, seed: str) -> list:
    """Deterministic diverse subset for cheap proxy search."""
    if max_rows <= 0 or len(rows) <= max_rows:
        return rows
    grouped: dict[tuple[str, object], list] = defaultdict(list)
    for row in rows:
        grouped[(row.regime or "unknown", row.as_of_date)].append(row)
    rng = random.Random(seed)
    for items in grouped.values():
        rng.shuffle(items)
    out = []
    level = 0
    keys = sorted(grouped, key=lambda item: (str(item[0]), str(item[1])))
    while len(out) < max_rows:
        added = False
        for key in keys:
            items = grouped[key]
            if level < len(items):
                out.append(items[level])
                added = True
                if len(out) >= max_rows:
                    break
        if not added:
            break
        level += 1
    return out


def _proxy_family_full_replay_gate_report(
    rows: list,
    *,
    family_names: list[str],
    selected_pairs_by_family: dict[str, set[tuple[dt.date, str]]],
    score_by_pair: dict[tuple[dt.date, str], dict[str, float]],
    base_params: dict[str, Any],
    manifest: dict[str, Any],
    universe_selection: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate proxy-family scores on rows that survived full replay.

    The expensive replay path is still the source of truth for forward labels and
    Stock Edge baseline scores here. Proxy family scores are used only as
    candidate ranking inputs for this gate, not as production parameters.
    """
    replay_pairs = {(row.as_of_date, row.ts_code) for row in rows}
    families: dict[str, Any] = {}
    for family in family_names:
        selected_pairs = selected_pairs_by_family.get(family, set())
        family_records = []
        for row in rows:
            pair = (row.as_of_date, row.ts_code)
            if pair not in selected_pairs:
                continue
            scores = score_by_pair.get(pair, {})
            if family not in scores:
                continue
            family_records.append({
                "as_of_date": row.as_of_date,
                "ts_code": row.ts_code,
                "score": float(scores[family]),
                "forward_5d_return": row.forward_5d_return,
                "forward_10d_return": row.forward_10d_return,
                "forward_20d_return": row.forward_20d_return,
            })
        families[family] = {
            "selected_pairs": len(selected_pairs),
            "replay_rows": len(family_records),
            "replay_success_rate": round(len(family_records) / max(len(selected_pairs), 1), 6),
            "horizons": _proxy_score_horizon_metrics(family_records),
            "month_stability": _proxy_score_month_stability(family_records),
        }

    baseline_metrics = evaluate_overlay_on_panel(panel_matrix_from_rows(rows), {}, base_params)
    baseline_month_stability = _baseline_month_stability(rows, base_params)
    random_control_records = _random_score_control_records(rows)
    return {
        "usage": {
            "purpose": "small full replay gate for named proxy family directions",
            "yaml_policy": "production YAML untouched; no auto-promote/apply-to-baseline",
            "interpretation": "family metrics use proxy scores joined to rows that successfully completed full Stock Edge replay",
        },
        "manifest": manifest,
        "universe_selection": universe_selection,
        "rows": {
            "replay_rows": len(rows),
            "unique_replay_pairs": len(replay_pairs),
            "unique_stocks": len({row.ts_code for row in rows}),
            "date_count": len({row.as_of_date for row in rows}),
            "dates": [d.isoformat() for d in sorted({row.as_of_date for row in rows})],
        },
        "families": families,
        "baseline_stock_edge_on_union": baseline_metrics,
        "baseline_stock_edge_month_stability": baseline_month_stability,
        "random_score_control_on_union": {
            "purpose": "deterministic random score on the same replay union; does not add replay pairs",
            "horizons": _proxy_score_horizon_metrics(random_control_records),
            "month_stability": _proxy_score_month_stability(random_control_records),
        },
    }


def _proxy_score_month_stability(records: list[dict[str, Any]]) -> dict[str, Any]:
    months = sorted({str(rec["as_of_date"])[:7] for rec in records})
    return {
        month: _proxy_score_horizon_metrics([rec for rec in records if str(rec["as_of_date"])[:7] == month])
        for month in months
    }


def _baseline_month_stability(rows: list, base_params: dict[str, Any]) -> dict[str, Any]:
    months = sorted({str(row.as_of_date)[:7] for row in rows})
    out: dict[str, Any] = {}
    for month in months:
        month_rows = [row for row in rows if str(row.as_of_date).startswith(month)]
        if not month_rows:
            continue
        out[month] = evaluate_overlay_on_panel(panel_matrix_from_rows(month_rows), {}, base_params)
    return out


def _random_score_control_records(rows: list) -> list[dict[str, Any]]:
    records = []
    for row in rows:
        rng = random.Random(f"stock_edge_proxy_family_gate_v1|{row.as_of_date}|{row.ts_code}")
        records.append({
            "as_of_date": row.as_of_date,
            "ts_code": row.ts_code,
            "score": rng.random(),
            "forward_5d_return": row.forward_5d_return,
            "forward_10d_return": row.forward_10d_return,
            "forward_20d_return": row.forward_20d_return,
        })
    return records


def _proxy_score_horizon_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        f"{h}d": _proxy_score_one_horizon(records, f"forward_{h}d_return")
        for h in (5, 10, 20)
    }


def _proxy_score_one_horizon(records: list[dict[str, Any]], label_key: str) -> dict[str, Any]:
    pairs = [
        (float(rec["score"]), float(rec[label_key]))
        for rec in records
        if rec.get(label_key) is not None
    ]
    pairs = [(s, r) for s, r in pairs if not (s != s or r != r)]
    n = len(pairs)
    if n < 10:
        return {
            "n": n,
            "rank_ic": 0.0,
            "top_bucket_return": 0.0,
            "top_bucket_win_rate": 0.0,
            "top_vs_bottom_spread": 0.0,
        }
    pairs.sort(key=lambda item: item[0])
    scores = [p[0] for p in pairs]
    returns = [p[1] for p in pairs]
    bucket_n = max(1, int(round(n * 0.20)))
    bottom = returns[:bucket_n]
    top = returns[-bucket_n:]
    return {
        "n": n,
        "rank_ic": round(_spearman_rank_ic(scores, returns), 6),
        "top_bucket_return": round((sum(top) / len(top)) / 100.0, 6) if top else 0.0,
        "top_bucket_win_rate": round(sum(1 for value in top if value > 0) / len(top), 6) if top else 0.0,
        "top_vs_bottom_spread": round(((sum(top) / len(top)) - (sum(bottom) / len(bottom))) / 100.0, 6) if top and bottom else 0.0,
    }


def _spearman_rank_ic(x: list[float], y: list[float]) -> float:
    if len(x) < 2 or len(y) < 2:
        return 0.0
    xr = _ordinal_ranks(x)
    yr = _ordinal_ranks(y)
    x_mean = sum(xr) / len(xr)
    y_mean = sum(yr) / len(yr)
    cov = sum((a - x_mean) * (b - y_mean) for a, b in zip(xr, yr))
    x_var = sum((a - x_mean) ** 2 for a in xr)
    y_var = sum((b - y_mean) ** 2 for b in yr)
    if x_var <= 1e-12 or y_var <= 1e-12:
        return 0.0
    return cov / ((x_var * y_var) ** 0.5)


def _ordinal_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda idx: values[idx])
    ranks = [0.0] * len(values)
    for rank, idx in enumerate(order):
        ranks[idx] = float(rank)
    return ranks


def main() -> int:
    parser = argparse.ArgumentParser(description="Stock Edge panel-based coarse tuning")
    parser.add_argument("--as-of", default=None, help="Latest as_of trade date (default: auto)")
    parser.add_argument("--top", type=int, default=50, help="Top N by liquidity (default 50)")
    parser.add_argument("--liquidity-offset", type=int, default=0, help="Skip the top K liquidity names before selecting --top. Use for OOC cohorts, e.g. 100 = ranks 101..")
    parser.add_argument("--pit-samples", type=int, default=8, help="PIT trading days to sample (default 8)")
    parser.add_argument("--pit-lookback-days", type=int, default=18 * 30, help="Calendar lookback for PIT date sampling before forward-label cutoff (default ~18 months)")
    parser.add_argument("--max-candidates", type=int, default=256, help="Search candidates (default 256)")
    parser.add_argument("--workers", type=int, default=-1, help="Parallel workers (-1 = auto, default -1)")
    parser.add_argument("--panel-chunk-size", type=int, default=25, help="Max stocks per replay worker chunk (default 25)")
    parser.add_argument("--universe-id", default="top_liquidity", help="Cache key prefix")
    parser.add_argument(
        "--universe-mode",
        choices=("latest", "pit-local", "stratified-pit"),
        default="latest",
        help="Universe selection mode: latest = one top-liquidity cohort; pit-local = reselect top N; stratified-pit = PIT-safe liquidity/size/industry stratified sampling",
    )
    parser.add_argument("--stratified-pool-multiple", type=int, default=8, help="Candidate pool multiple for stratified-pit (default 8)")
    parser.add_argument("--diagnose-panel", action=argparse.BooleanOptionalAction, default=True, help="Write panel diagnosis artifact after panel build")
    parser.add_argument("--diagnosis-output-dir", default="/Users/neoclaw/claude/ifaenv/manifests/stock_edge_panel_diagnostics", help="Directory for panel diagnosis JSON artifacts")
    parser.add_argument("--proxy-only", action="store_true", help="Build fast forward-label/cheap-feature proxy cache and diagnostics, then exit before full replay")
    parser.add_argument("--force-proxy-cache", action="store_true", help="Rebuild the fast outcome proxy cache even if it already exists")
    parser.add_argument("--proxy-benchmark-legacy-dates", type=int, default=0, help="Benchmark old per-date proxy builder on the first N PIT dates before the batch build (default 0)")
    parser.add_argument("--two-stage", action="store_true", help="Use cheap proxy prefilter before expensive replay search")
    parser.add_argument("--proxy-candidates", type=int, default=128, help="Cheap proxy candidate budget in --two-stage mode")
    parser.add_argument("--proxy-max-rows", type=int, default=600, help="Max rows for cheap proxy subset in --two-stage mode")
    parser.add_argument("--proxy-family-gate", action="store_true", help="Use named proxy family scores to select a PIT top subset, run full replay only for that subset, then emit gate metrics and exit")
    parser.add_argument("--proxy-family-names", default="weak_industry_avoid_quality_flow,industry_relative_momentum_flow", help="Comma-separated proxy family names for --proxy-family-gate")
    parser.add_argument("--proxy-family-top-per-date", type=int, default=12, help="Top rows per PIT date per proxy family for --proxy-family-gate")
    parser.add_argument("--include-llm", action="store_true", help="Include LLM signals (slower)")
    parser.add_argument("--dry-run", action="store_true", help="Build panel + tune but don't write artifact")
    parser.add_argument("--n-iterations", type=int, default=3, help="Search iterations (default 3)")
    parser.add_argument("--no-warmstart", action="store_true", help="Disable IC-derived warmstart")
    parser.add_argument("--no-negative-weights", action="store_true", help="Disable negative weights for inverted signals")
    parser.add_argument("--search-algo", choices=("random", "tpe"), default="random", help="Search algorithm (default 'random'; 'tpe' uses Optuna TPE sampler)")
    parser.add_argument("--successive-halving", action="store_true", help="Use 3-stage successive halving (broad → narrow → fine); ignores --n-iterations")
    parser.add_argument("--auto-promote", action="store_true", help="Apply gates; if passed, write YAML variant")
    parser.add_argument("--variant-output", default=None, help="Where to write YAML variant (default: side-by-side .variant.yaml; ignored if --apply-to-baseline)")
    parser.add_argument("--apply-to-baseline", action="store_true", help="Overwrite the base YAML directly (with .bak_<ts> backup); ideal for weekly cron")
    parser.add_argument("--base-yaml", default="ifa/families/stock/params/stock_edge_v2.2.yaml")
    parser.add_argument("--oos", action="store_true", help="Walk-forward OOS: tune on older half, gate on newer half")
    parser.add_argument("--train-fraction", type=float, default=0.5, help="OOS train fraction (default 0.5)")
    parser.add_argument("--embargo-days", type=int, default=10, help="OOS embargo days between train end and val start (default 10)")
    parser.add_argument("--k-fold", type=int, default=0, help="K-fold rolling walk-forward (default 0 = single split). Each fold tunes on growing train window, evaluates on next val_dates_per_fold dates")
    parser.add_argument("--val-dates-per-fold", type=int, default=2, help="Validation dates per fold (default 2)")
    parser.add_argument("--min-train-dates", type=int, default=4, help="Minimum train dates for first fold (default 4)")
    parser.add_argument("--k-fold-min-positive", type=int, default=0, help="G9 gate: minimum number of folds with positive val lift per horizon (default = ceil(0.75 * n_folds))")
    parser.add_argument("--bootstrap-iterations", type=int, default=1000, help="G5 gate: bootstrap iterations for CI (default 1000; 0 disables G5)")
    parser.add_argument("--bootstrap-confidence", type=float, default=0.95, help="G5 gate: confidence level (default 0.95)")
    parser.add_argument("--regime-min-bucket-pct", type=float, default=0.75, help="G4 gate: minimum fraction of regime buckets that must improve (default 0.75)")
    parser.add_argument("--regime-min-samples", type=int, default=30, help="G4 gate: minimum val rows per regime bucket (default 30)")
    args = parser.parse_args()

    engine = get_engine()
    if args.as_of:
        as_of = dt.date.fromisoformat(args.as_of)
    else:
        with engine.connect() as c:
            row = c.execute(text("SELECT MAX(trade_date) FROM smartmoney.raw_daily")).scalar()
        if row is None:
            print("ERROR: no raw_daily data; cannot infer as_of", file=sys.stderr)
            return 2
        as_of = row

    print(f"=== Stock Edge Panel Tune ===")
    print(f"  as_of:           {as_of}")
    print(f"  top N:           {args.top}")
    print(f"  liquidity offset:{args.liquidity_offset}")
    print(f"  universe mode:   {args.universe_mode}")
    print(f"  PIT samples:     {args.pit_samples}")
    print(f"  PIT lookback:    {args.pit_lookback_days} calendar days")
    print(f"  candidates:      {args.max_candidates}")
    print(f"  workers:         {args.workers if args.workers > 0 else os.cpu_count() - 1}")
    print(f"  skip_llm:        {not args.include_llm}")
    print(f"  dry_run:         {args.dry_run}")
    if args.dry_run:
        print("  safety:         DRY RUN - artifact will NOT be written; baseline YAML will NOT be touched")
    liquidity_offset = max(0, args.liquidity_offset)
    universe_label_base = f"{args.universe_id}_top{args.top}" if liquidity_offset == 0 else f"{args.universe_id}_top{args.top}_offset{liquidity_offset}"
    if args.universe_mode == "latest":
        universe_label = universe_label_base
    elif args.universe_mode == "pit-local":
        universe_label = f"{universe_label_base}_pitlocal"
    else:
        universe_label = f"{universe_label_base}_stratifiedpit"

    print(f"\n[1/4] Selecting PIT trading days...")
    t0 = time.monotonic()
    pit_dates = _select_pit_dates(
        engine,
        n_samples=args.pit_samples,
        latest_as_of=as_of,
        lookback_days=args.pit_lookback_days,
    )
    if len(pit_dates) < args.pit_samples:
        print(f"      WARN: only {len(pit_dates)} dates available")
    print(f"      dates: {[d.isoformat() for d in pit_dates]} ({time.monotonic()-t0:.1f}s)")

    print(f"\n[2/4] Selecting universe...")
    t0 = time.monotonic()
    ts_codes_by_date: dict[dt.date, list[str]] | None = None
    universe_selection: dict[str, object] = {
        "mode": args.universe_mode,
        "top_n": args.top,
        "liquidity_offset": liquidity_offset,
        "lookback_days": 20,
        "min_days_fraction": 0.7,
        "leakage_guard": (
            "latest mode selects once using rows <= as_of; pit-local mode selects each "
            "date using rows <= that PIT date; stratified-pit uses rows <= each PIT date "
            "and stratifies by liquidity, size, SW L1 industry, with volatility diagnostics"
        ),
    }
    if args.universe_mode == "latest":
        ts_codes = _select_universe(engine, top_n=args.top, as_of=as_of, liquidity_offset=liquidity_offset)
        universe_selection.update({
            "selection_as_of": as_of.isoformat(),
            "selected_count": len(ts_codes),
            "membership_hash": _digest_codes(ts_codes),
            "sample_head": ts_codes[:10],
        })
        print(f"      {len(ts_codes)} stocks selected as of {as_of} ({time.monotonic()-t0:.1f}s)")
    elif args.universe_mode == "pit-local":
        ts_codes_by_date = {}
        by_date_meta: dict[str, dict[str, object]] = {}
        union_codes: set[str] = set()
        for pit_date in pit_dates:
            codes = _select_universe(engine, top_n=args.top, as_of=pit_date, liquidity_offset=liquidity_offset)
            ts_codes_by_date[pit_date] = codes
            union_codes.update(codes)
            by_date_meta[pit_date.isoformat()] = {
                "selection_as_of": pit_date.isoformat(),
                "lookback_start": (pit_date - dt.timedelta(days=20 * 2)).isoformat(),
                "selected_count": len(codes),
                "membership_hash": _digest_codes(codes),
                "sample_head": codes[:10],
            }
        ts_codes = sorted(union_codes)
        universe_selection.update({
            "unique_stock_count": len(ts_codes),
            "date_count": len(pit_dates),
            "membership_hash": _digest_codes([
                f"{pit_date.isoformat()}:{','.join(ts_codes_by_date.get(pit_date, []))}"
                for pit_date in pit_dates
            ]),
            "by_date": by_date_meta,
        })
        min_n = min((len(v) for v in ts_codes_by_date.values()), default=0)
        max_n = max((len(v) for v in ts_codes_by_date.values()), default=0)
        print(
            f"      {len(ts_codes)} unique stocks across {len(pit_dates)} PIT-local cohorts "
            f"(per-date {min_n}..{max_n}, {time.monotonic()-t0:.1f}s)"
        )
    else:
        ts_codes_by_date, by_date_meta, selection_timing = _select_stratified_pit_universes_batch(
            engine,
            top_n=args.top,
            as_of_dates=pit_dates,
            pool_multiple=args.stratified_pool_multiple,
        )
        union_codes = {code for codes in ts_codes_by_date.values() for code in codes}
        ts_codes = sorted(union_codes)
        universe_selection.update({
            "unique_stock_count": len(ts_codes),
            "date_count": len(pit_dates),
            "membership_hash": _digest_codes([
                f"{pit_date.isoformat()}:{','.join(ts_codes_by_date.get(pit_date, []))}"
                for pit_date in pit_dates
            ]),
            "stratified_pool_multiple": args.stratified_pool_multiple,
            "stratification_dimensions": ["liquidity", "market_cap", "sw_l1_industry", "volatility_diagnostic"],
            "by_date": by_date_meta,
            "selection_backend": "batch_pandas",
            "selection_timing": selection_timing,
        })
        min_n = min((len(v) for v in ts_codes_by_date.values()), default=0)
        max_n = max((len(v) for v in ts_codes_by_date.values()), default=0)
        print(
            f"      {len(ts_codes)} unique stocks across {len(pit_dates)} stratified PIT cohorts "
            f"(per-date {min_n}..{max_n}, {time.monotonic()-t0:.1f}s)"
        )
        print(f"      selection timing: {selection_timing}")

    requested_rows = sum(len(ts_codes_by_date.get(d, [])) for d in pit_dates) if ts_codes_by_date else len(ts_codes) * len(pit_dates)

    proxy_gate_scores: dict[tuple[dt.date, str], dict[str, float]] = {}
    proxy_gate_selected: dict[str, set[tuple[dt.date, str]]] = {}
    if args.proxy_only or args.proxy_family_gate:
        print(f"\n[3/4] Building fast outcome proxy ({requested_rows} requested rows)...")
        benchmark_report = None
        if args.proxy_benchmark_legacy_dates > 0:
            print(f"      benchmarking legacy proxy builder on first {args.proxy_benchmark_legacy_dates} PIT date(s)...")
            t_bench = time.monotonic()
            benchmark_report = benchmark_outcome_proxy_builders(
                engine,
                as_of_dates=pit_dates,
                ts_codes=ts_codes,
                ts_codes_by_date=ts_codes_by_date,
                max_dates=args.proxy_benchmark_legacy_dates,
            )
            print(
                "      legacy benchmark: "
                f"old={benchmark_report['legacy']['runtime_sec']:.2f}s "
                f"new={benchmark_report['batch']['runtime_sec']:.2f}s "
                f"speedup={benchmark_report['speedup']:.2f}x "
                f"({time.monotonic() - t_bench:.1f}s wall)"
            )
        t0 = time.monotonic()
        proxy_df, proxy_manifest = build_outcome_proxy_cache(
            engine,
            universe_id=universe_label,
            as_of_dates=pit_dates,
            ts_codes=ts_codes,
            ts_codes_by_date=ts_codes_by_date,
            universe_selection=universe_selection,
            force=args.force_proxy_cache,
        )
        proxy_elapsed = time.monotonic() - t0
        summary = summarize_outcome_proxy(proxy_df)
        family_comparison = compare_proxy_candidate_families(proxy_df)
        output_dir = Path(args.diagnosis_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        proxy_report_path = output_dir / f"{universe_label}__outcome_proxy_{stamp}.json"
        proxy_report = {
            "manifest": proxy_manifest.to_dict(),
            "diagnostics": summary,
            "candidate_family_comparison": family_comparison,
            "benchmark": benchmark_report,
            "usage": {
                "purpose": "fast PIT forward-label and cheap-feature diagnostics; no production strategy replay scores",
                "next_step": "run full replay only after proxy diagnostics show label/cohort coverage is acceptable",
            },
        }
        proxy_report_path.write_text(json.dumps(proxy_report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        print(f"      proxy built: {len(proxy_df)} rows in {proxy_elapsed:.1f}s ({len(proxy_df)*60/max(proxy_elapsed,1):.1f} rows/min)")
        print(f"      cached at: {proxy_manifest.cache_path}")
        print(f"      manifest: {proxy_manifest.manifest_path}")
        print(f"      diagnostics: {proxy_report_path}")
        print(
            f"      row summary: requested={proxy_manifest.requested_rows} ok={proxy_manifest.n_rows} "
            f"fail={proxy_manifest.failed_rows} failure_rate={proxy_manifest.failed_rows / max(proxy_manifest.requested_rows, 1):.2%}"
        )
        for h in (5, 10, 20):
            hdiag = summary.get("horizons", {}).get(f"{h}d", {})
            cheap_ic = summary.get("cheap_composite_rank_ic", {}).get(f"{h}d", 0.0)
            print(
                f"      {h}d labels: n={hdiag.get('n', 0)} avg={float(hdiag.get('avg_return', 0.0))*100:+.2f}% "
                f"positive={float(hdiag.get('positive_rate', 0.0)):.2f} cheap_rank_ic={cheap_ic:+.3f}"
            )
        print("      candidate families:")
        for family in ("sector_cycle_leader", "weak_industry_avoid_quality_flow", "industry_relative_momentum_flow"):
            payload = family_comparison.get("families", {}).get(family, {})
            horizons = payload.get("horizons", {})
            mar = payload.get("month_stability", {}).get("2026-03", {})
            print(f"        {family}")
            for h in (5, 10, 20):
                m = horizons.get(f"{h}d", {})
                mm = mar.get(f"{h}d", {})
                print(
                    f"          {h}d rank_ic={float(m.get('rank_ic', 0.0)):+.3f} "
                    f"top_avg={float(m.get('top_bucket_return', 0.0))*100:+.2f}% "
                    f"top_win={float(m.get('top_bucket_win_rate', 0.0)):.2f} "
                    f"spread={float(m.get('top_vs_bottom_spread', 0.0))*100:+.2f}% "
                    f"Mar_ic={float(mm.get('rank_ic', 0.0)):+.3f}"
                )
        if args.proxy_family_gate:
            family_names = [name.strip() for name in str(args.proxy_family_names).split(",") if name.strip()]
            family_scores = score_proxy_candidate_families(proxy_df)
            missing = sorted(set(family_names) - set(family_scores))
            if missing:
                print(f"ERROR: unknown proxy family name(s): {missing}", file=sys.stderr)
                return 2
            proxy_df = proxy_df.copy()
            proxy_df["as_of_date"] = proxy_df["as_of_date"].apply(
                lambda d: d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
            )
            selected_by_date: dict[dt.date, set[str]] = defaultdict(set)
            top_n = max(1, int(args.proxy_family_top_per_date))
            for family in family_names:
                score = family_scores[family]
                proxy_gate_selected[family] = set()
                ranked = proxy_df.assign(_proxy_score=score)
                for pit_date, sub in ranked.groupby("as_of_date", sort=True):
                    top = sub.dropna(subset=["_proxy_score"]).sort_values("_proxy_score", ascending=False).head(top_n)
                    for rec in top[["as_of_date", "ts_code", "_proxy_score"]].to_dict(orient="records"):
                        key = (rec["as_of_date"], str(rec["ts_code"]))
                        selected_by_date[rec["as_of_date"]].add(str(rec["ts_code"]))
                        proxy_gate_selected[family].add(key)
                        proxy_gate_scores.setdefault(key, {})[family] = float(rec["_proxy_score"])
            ts_codes_by_date = {d: sorted(codes) for d, codes in selected_by_date.items()}
            ts_codes = sorted({code for codes in ts_codes_by_date.values() for code in codes})
            requested_rows = sum(len(codes) for codes in ts_codes_by_date.values())
            universe_label = f"{universe_label}_proxyfam_top{top_n}_{_digest_codes(family_names)[:8]}"
            universe_selection["proxy_family_gate"] = {
                "enabled": True,
                "families": family_names,
                "top_per_date_per_family": top_n,
                "source_proxy_cache": proxy_manifest.cache_path,
                "requested_replay_rows": requested_rows,
                "selected_unique_stocks": len(ts_codes),
                "by_date_counts": {d.isoformat(): len(codes) for d, codes in ts_codes_by_date.items()},
                "purpose": "small full replay gate for proxy family directions; production YAML untouched",
            }
            print(
                f"      proxy family gate: families={family_names} top_per_date={top_n} "
                f"-> replay rows={requested_rows}, unique_stocks={len(ts_codes)}"
            )
            print(f"      by-date rows: {universe_selection['proxy_family_gate']['by_date_counts']}")
        if args.proxy_only:
            print("\n  [proxy-only] full strategy replay/search skipped; YAML untouched")
            return 0

    print(f"\n[3/4] Building replay panel ({requested_rows} requested rows)...")
    t0 = time.monotonic()
    url = engine.url.render_as_string(hide_password=False)
    base = load_params()
    n_workers = args.workers if args.workers > 0 else max(1, (os.cpu_count() or 4) - 1)

    last_progress_at = [time.monotonic()]
    def on_progress(p):
        if p.get("event") == "cache_hit":
            print(f"      [cache] reused panel: {p['rows']} rows from {p['path']}")
            return
        if p.get("event") == "cache_miss":
            resumed = int(p.get("resumed_pairs") or 0)
            pending = int(p.get("pending_pairs") or p.get("total_pairs") or 0)
            resume_text = f", resumed={resumed}, pending={pending}" if resumed else ""
            print(f"      [cache] miss/rebuild: {p.get('total_pairs', 0)} requested rows{resume_text} -> {p['path']}")
            if p.get("checkpoint_dir"):
                print(f"      [cache] checkpoints: {p.get('checkpoint_dir')}")
            return
        if p.get("event") == "row_error":
            print(f"      [panel row error] {p.get('error')}")
            return
        now = time.monotonic()
        if now - last_progress_at[0] >= 5.0 or p.get("completed") == p.get("total"):
            last_progress_at[0] = now
            print(f"      progress: {p['completed']}/{p['total']} ok={p.get('ok')} fail={p.get('failed')} "
                  f"rate={p.get('rate_per_min')}/min eta={p.get('eta_sec')}s")

    rows, manifest = build_replay_panel(
        url,
        ts_codes=ts_codes,
        as_of_dates=pit_dates,
        ts_codes_by_date=ts_codes_by_date,
        base_params=base,
        universe_id=universe_label,
        universe_mode=args.universe_mode,
        universe_selection=universe_selection,
        skip_llm=not args.include_llm,
        n_workers=n_workers,
        on_progress=on_progress,
        max_codes_per_chunk=args.panel_chunk_size,
    )
    panel_elapsed = time.monotonic() - t0
    print(f"      panel built: {len(rows)} rows in {panel_elapsed:.1f}s ({len(rows)*60/max(panel_elapsed,1):.1f} rows/min)")
    print(f"      cached at: {manifest.panel_path}")
    expected_rows = manifest.total_pairs or (manifest.universe_size * len(manifest.as_of_dates))
    failed_rows = manifest.failed_rows if manifest.failed_rows is not None else max(0, expected_rows - len(rows))
    print(f"      manifest: {manifest.manifest_path}")
    failure_rate = failed_rows / expected_rows if expected_rows else 0.0
    print(f"      row summary: requested={expected_rows} ok={len(rows)} fail={failed_rows} failure_rate={failure_rate:.2%}")
    if failed_rows:
        details = list(getattr(manifest, "failure_details", []) or [])
        if details:
            reason_counts: dict[str, int] = {}
            for item in details:
                reason = str(item.get("reason") or "unknown")
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            print(f"      WARN: row failures by reason: {reason_counts}")
            for item in details[:10]:
                print(
                    "        "
                    f"{item.get('ts_code')}@{item.get('as_of_date')}: "
                    f"{item.get('reason')}"
                )
            if len(details) > 10:
                print(f"        ... {len(details) - 10} more failures in manifest")
        else:
            print(f"      WARN: row failures detected; legacy manifest has no per-row diagnostics")

    if not rows:
        print("ERROR: panel is empty; cannot tune", file=sys.stderr)
        return 3

    if args.proxy_family_gate:
        family_names = [name.strip() for name in str(args.proxy_family_names).split(",") if name.strip()]
        gate_report = _proxy_family_full_replay_gate_report(
            rows,
            family_names=family_names,
            selected_pairs_by_family=proxy_gate_selected,
            score_by_pair=proxy_gate_scores,
            base_params=base,
            manifest=manifest.to_dict(),
            universe_selection=universe_selection,
        )
        output_dir = Path(args.diagnosis_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        gate_path = output_dir / f"{universe_label}__proxy_family_full_replay_gate_{stamp}.json"
        gate_path.write_text(json.dumps(gate_report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        print(f"\n=== Proxy Family Full Replay Gate ===")
        print(f"  artifact: {gate_path}")
        print(f"  replay rows: requested={expected_rows} ok={len(rows)} fail={failed_rows} failure_rate={failure_rate:.2%}")
        for family in family_names:
            payload = gate_report["families"].get(family, {})
            print(f"  {family}: selected={payload.get('selected_pairs', 0)} replay_ok={payload.get('replay_rows', 0)}")
            for h in (5, 10, 20):
                m = payload.get("horizons", {}).get(f"{h}d", {})
                mar = payload.get("month_stability", {}).get("2026-03", {}).get(f"{h}d", {})
                print(
                    f"    {h}d rank_ic={float(m.get('rank_ic', 0.0)):+.3f} "
                    f"top_ret={float(m.get('top_bucket_return', 0.0))*100:+.2f}% "
                    f"top_win={float(m.get('top_bucket_win_rate', 0.0)):.2f} "
                    f"spread={float(m.get('top_vs_bottom_spread', 0.0))*100:+.2f}% "
                    f"Mar_ic={float(mar.get('rank_ic', 0.0)):+.3f}"
                )
        base_payload = gate_report.get("baseline_stock_edge_on_union", {})
        base_month = gate_report.get("baseline_stock_edge_month_stability", {}).get("2026-03", {})
        print("  baseline Stock Edge on union subset:")
        for h in (5, 10, 20):
            m = base_payload.get(f"objective_{h}d", {})
            mar = base_month.get(f"objective_{h}d", {})
            print(
                f"    {h}d rank_ic={float(m.get('rank_ic', 0.0)):+.3f} "
                f"top_ret={float(m.get('top_bucket_avg_return', 0.0))*100:+.2f}% "
                f"top_win={float(m.get('top_bucket_win_rate', 0.0)):.2f} "
                f"spread={float(m.get('top_bottom_spread', 0.0))*100:+.2f}% "
                f"Mar_ic={float(mar.get('rank_ic', 0.0)):+.3f} "
                f"Mar_top={float(mar.get('top_bucket_avg_return', 0.0))*100:+.2f}%"
            )
        control_payload = gate_report.get("random_score_control_on_union", {})
        print("  random score control on union subset:")
        for h in (5, 10, 20):
            m = control_payload.get("horizons", {}).get(f"{h}d", {})
            mar = control_payload.get("month_stability", {}).get("2026-03", {}).get(f"{h}d", {})
            print(
                f"    {h}d rank_ic={float(m.get('rank_ic', 0.0)):+.3f} "
                f"top_ret={float(m.get('top_bucket_return', 0.0))*100:+.2f}% "
                f"top_win={float(m.get('top_bucket_win_rate', 0.0)):.2f} "
                f"spread={float(m.get('top_vs_bottom_spread', 0.0))*100:+.2f}% "
                f"Mar_ic={float(mar.get('rank_ic', 0.0)):+.3f} "
                f"Mar_top={float(mar.get('top_bucket_return', 0.0))*100:+.2f}%"
            )
        print("\n  [proxy-family-gate] search skipped; auto-promote disabled; YAML untouched")
        print(f"\n  total wall time: {panel_elapsed:.1f}s")
        return 0

    if args.diagnose_panel:
        try:
            from stock_edge_panel_diagnose import diagnose, write_diagnosis_artifact

            report = diagnose(Path(manifest.panel_path), min_slice_rows=max(10, args.regime_min_samples))
            diagnosis_path = write_diagnosis_artifact(report, output_dir=Path(args.diagnosis_output_dir))
            print(f"      diagnosis artifact: {diagnosis_path}")
            flags = report.get("diagnosis_flags") or []
            if flags:
                print(f"      diagnosis flags: {flags[:5]}")
        except Exception as exc:
            print(f"      WARN: panel diagnosis failed: {type(exc).__name__}: {exc}")

    # K-fold rolling walk-forward (Phase 5 v2 — robust OOS)
    k_fold_done = False
    kfold_for_gate: list[dict] | None = None
    if args.k_fold and args.k_fold >= 2:
        folds = k_fold_rolling_walk_forward(
            rows,
            n_folds=args.k_fold,
            val_dates_per_fold=args.val_dates_per_fold,
            min_train_dates=args.min_train_dates,
            embargo_days=args.embargo_days,
        )
        if not folds:
            print("ERROR: not enough dates for k-fold; falling back to single split", file=sys.stderr)
            args.k_fold = 0
        else:
            print(f"\n[K-fold rolling walk-forward] {len(folds)} folds, val_dates={args.val_dates_per_fold} each, min_train={args.min_train_dates}, embargo={args.embargo_days}d")
            for i, (tr, va) in enumerate(folds):
                tr_dates = sorted({r.as_of_date for r in tr})
                va_dates = sorted({r.as_of_date for r in va})
                print(f"  Fold {i+1}: train={len(tr)} rows ({len(tr_dates)} dates: {tr_dates[0]}..{tr_dates[-1]}) | val={len(va)} rows ({va_dates[0]}..{va_dates[-1]})")

            # Run search per fold; aggregate val metrics
            print(f"\n[4/4] Running K-fold search (each fold: {args.max_candidates} candidates × {args.n_iterations} iter)...")
            fold_results = []
            t0 = time.monotonic()
            search_progress_at = [0.0]

            def search_progress(prefix: str):
                def _progress(p):
                    event = p.get("event")
                    if event in {"stage_start", "stage_done"}:
                        label = "start" if event == "stage_start" else "done"
                        print(
                            f"      {prefix} stage {p.get('stage')} {label}: "
                            f"budget={p.get('total', 0)} score={float(p.get('score', 0.0)):.4f} "
                            f"best={float(p.get('best_score', 0.0)):.4f}"
                        )
                        return
                    now = time.monotonic()
                    if now - search_progress_at[0] < 5.0 and p.get("candidate") != p.get("total"):
                        return
                    search_progress_at[0] = now
                    stage = p.get("stage")
                    stage_part = f" stage={stage}" if stage is not None else ""
                    print(
                        f"      {prefix}{stage_part} cand {p.get('candidate', 0)}/{p.get('total', 0)} "
                        f"score={float(p.get('score', 0.0)):.4f} best={float(p.get('best_score', 0.0)):.4f} "
                        f"elapsed={p.get('elapsed_seconds', '?')}s"
                    )
                return _progress

            for i, (train_rows, val_rows) in enumerate(folds):
                print(f"  Fold {i+1}: search start train_rows={len(train_rows)} val_rows={len(val_rows)}")
                initial_overlay = None
                if args.two_stage:
                    proxy_rows = _cheap_proxy_rows(
                        train_rows,
                        max_rows=args.proxy_max_rows,
                        seed=f"{universe_label}:fold{i}:proxy",
                    )
                    print(
                        f"    Fold {i+1} two-stage proxy: rows={len(proxy_rows)}/{len(train_rows)} "
                        f"candidates={args.proxy_candidates}"
                    )
                    proxy_artifact = fit_global_preset_via_panel(
                        proxy_rows,
                        as_of_date=as_of,
                        base_params=base,
                        universe=f"{universe_label}_fold{i}_proxy",
                        max_candidates=args.proxy_candidates,
                        n_iterations=1,
                        use_ic_warmstart=not args.no_warmstart,
                        allow_negative_weights=not args.no_negative_weights,
                        search_algo=args.search_algo,
                        on_progress=search_progress(f"fold {i+1} proxy"),
                    )
                    initial_overlay = proxy_artifact.overlay
                    print(f"    Fold {i+1} proxy best score={proxy_artifact.objective_score:.4f}; expensive replay starts from proxy overlay")
                if args.successive_halving:
                    fold_artifact = fit_global_preset_successive_halving(
                        train_rows, as_of_date=as_of, base_params=base,
                        universe=f"{universe_label}_fold{i}",
                        total_budget=args.max_candidates,
                        use_ic_warmstart=not args.no_warmstart,
                        allow_negative_weights=not args.no_negative_weights,
                        search_algo=args.search_algo,
                        initial_overlay=initial_overlay,
                        on_progress=search_progress(f"fold {i+1}"),
                    )
                else:
                    fold_artifact = fit_global_preset_via_panel(
                        train_rows, as_of_date=as_of, base_params=base,
                        universe=f"{universe_label}_fold{i}",
                        max_candidates=args.max_candidates,
                        n_iterations=args.n_iterations,
                        use_ic_warmstart=not args.no_warmstart,
                        allow_negative_weights=not args.no_negative_weights,
                        search_algo=args.search_algo,
                        initial_overlay=initial_overlay,
                        on_progress=search_progress(f"fold {i+1}"),
                    )
                if args.two_stage:
                    fold_artifact.metrics["two_stage"] = {
                        "enabled": True,
                        "proxy_candidates": args.proxy_candidates,
                        "proxy_max_rows": args.proxy_max_rows,
                        "expensive_candidates": args.max_candidates,
                    }
                val_panel = panel_matrix_from_rows(val_rows)
                val_baseline = evaluate_overlay_on_panel(val_panel, {}, base)
                val_tuned = evaluate_overlay_on_panel(val_panel, fold_artifact.overlay, base)
                fold_results.append({
                    "fold": i + 1,
                    "train_dates": [d.isoformat() for d in sorted({r.as_of_date for r in train_rows})],
                    "val_dates": [d.isoformat() for d in sorted({r.as_of_date for r in val_rows})],
                    "train_artifact": fold_artifact,
                    "val_baseline": val_baseline,
                    "val_tuned": val_tuned,
                })
                vt5 = val_tuned['objective_5d']['rank_ic']
                vt10 = val_tuned['objective_10d']['rank_ic']
                vt20 = val_tuned['objective_20d']['rank_ic']
                vb5 = val_baseline['objective_5d']['rank_ic']
                vb10 = val_baseline['objective_10d']['rank_ic']
                vb20 = val_baseline['objective_20d']['rank_ic']
                print(f"  Fold {i+1}: val rank IC 5d {vb5:+.3f}→{vt5:+.3f} (Δ {vt5-vb5:+.3f}) | "
                      f"10d {vb10:+.3f}→{vt10:+.3f} (Δ {vt10-vb10:+.3f}) | "
                      f"20d {vb20:+.3f}→{vt20:+.3f} (Δ {vt20-vb20:+.3f})")
                for h in (5, 10, 20):
                    bm = val_baseline[f"objective_{h}d"]
                    tm = val_tuned[f"objective_{h}d"]
                    print(
                        f"      {h}d payoff: top_ret {float(bm.get('top_bucket_avg_return', 0.0))*100:+.2f}%"
                        f"→{float(tm.get('top_bucket_avg_return', 0.0))*100:+.2f}% "
                        f"spread {float(bm.get('top_bottom_spread', 0.0))*100:+.2f}%"
                        f"→{float(tm.get('top_bottom_spread', 0.0))*100:+.2f}% "
                        f"mono {float(bm.get('bucket_monotonicity', 0.0)):+.2f}"
                        f"→{float(tm.get('bucket_monotonicity', 0.0)):+.2f}"
                    )
            elapsed = time.monotonic() - t0
            print(f"      total search across {len(folds)} folds: {elapsed:.1f}s")

            # Summary table
            print(f"\n=== K-Fold Aggregate (median across {len(folds)} folds) ===")
            import statistics
            for h in (5, 10, 20):
                lifts = [r['val_tuned'][f'objective_{h}d']['rank_ic'] - r['val_baseline'][f'objective_{h}d']['rank_ic'] for r in fold_results]
                tuneds = [r['val_tuned'][f'objective_{h}d']['rank_ic'] for r in fold_results]
                bases = [r['val_baseline'][f'objective_{h}d']['rank_ic'] for r in fold_results]
                pos_folds = sum(1 for l in lifts if l > 0)
                print(f"  {h}d: median val_lift {statistics.median(lifts):+.4f} | per-fold lifts {[f'{l:+.3f}' for l in lifts]} | positive {pos_folds}/{len(folds)} folds")
                print(f"      tuned IC range: {min(tuneds):+.3f}..{max(tuneds):+.3f}, baseline range: {min(bases):+.3f}..{max(bases):+.3f}")

            # Pick "best fold" artifact for downstream auto-promote (latest fold = most recent training)
            artifact = fold_results[-1]['train_artifact']
            val_metrics_baseline = fold_results[-1]['val_baseline']
            val_metrics_tuned = fold_results[-1]['val_tuned']
            # Compact fold metrics for G9 gate input
            kfold_for_gate = [
                {"val_baseline": fr["val_baseline"], "val_tuned": fr["val_tuned"], "fold": fr["fold"]}
                for fr in fold_results
            ]
            print(f"\n  [auto-promote will use latest fold's artifact + K-fold results for G9 gate]")
            search_elapsed = elapsed

            # Skip the regular [4/4] search and the single-OOS split
            k_fold_done = True
            args.oos = False

    # Walk-forward OOS split (Phase 5)
    if not k_fold_done and args.oos:
        train_rows, val_rows = walk_forward_split(
            rows, train_fraction=args.train_fraction, embargo_days=args.embargo_days,
        )
        train_dates = sorted({r.as_of_date for r in train_rows})
        val_dates = sorted({r.as_of_date for r in val_rows})
        print(f"\n[OOS split] train={len(train_rows)} rows ({len(train_dates)} dates: {train_dates[0]}..{train_dates[-1]}) | val={len(val_rows)} rows ({len(val_dates)} dates: {val_dates[0] if val_dates else '-'}..{val_dates[-1] if val_dates else '-'}) | embargo={args.embargo_days}d")
        if not val_rows:
            print(f"      ERROR: validation set empty (panel only spans {len(set(r.as_of_date for r in rows))} dates with embargo {args.embargo_days}d). Cannot do OOS.", file=sys.stderr)
            return 4
        search_rows = train_rows
    elif not k_fold_done:
        search_rows = rows

    if not k_fold_done:
        print(f"\n[4/4] Running search ({args.max_candidates} candidates × {args.n_iterations} iterations over decision_layer space)...")
        t0 = time.monotonic()
        search_progress_at = [0.0]

        def search_progress(p):
            event = p.get("event")
            if event in {"stage_start", "stage_done"}:
                label = "start" if event == "stage_start" else "done"
                print(
                    f"      search stage {p.get('stage')} {label}: "
                    f"budget={p.get('total', 0)} score={float(p.get('score', 0.0)):.4f} "
                    f"best={float(p.get('best_score', 0.0)):.4f}"
                )
                return
            now = time.monotonic()
            if now - search_progress_at[0] < 5.0 and p.get("candidate") != p.get("total"):
                return
            search_progress_at[0] = now
            stage = p.get("stage")
            stage_part = f" stage={stage}" if stage is not None else ""
            print(
                f"      search{stage_part} cand {p.get('candidate', 0)}/{p.get('total', 0)} "
                f"score={float(p.get('score', 0.0)):.4f} best={float(p.get('best_score', 0.0)):.4f} "
                f"elapsed={p.get('elapsed_seconds', '?')}s"
            )

        initial_overlay = None
        if args.two_stage:
            proxy_rows = _cheap_proxy_rows(
                search_rows,
                max_rows=args.proxy_max_rows,
                seed=f"{universe_label}:proxy",
            )
            print(
                f"      two-stage proxy search: rows={len(proxy_rows)}/{len(search_rows)} "
                f"candidates={args.proxy_candidates}"
            )
            proxy_artifact = fit_global_preset_via_panel(
                proxy_rows,
                as_of_date=as_of,
                base_params=base,
                universe=f"{universe_label}_proxy",
                max_candidates=args.proxy_candidates,
                n_iterations=1,
                use_ic_warmstart=not args.no_warmstart,
                allow_negative_weights=not args.no_negative_weights,
                search_algo=args.search_algo,
                initial_overlay=initial_overlay,
                on_progress=search_progress,
            )
            initial_overlay = proxy_artifact.overlay
            print(f"      proxy best objective score: {proxy_artifact.objective_score:.6f}")

        if args.successive_halving:
            artifact = fit_global_preset_successive_halving(
                search_rows,
                as_of_date=as_of,
                base_params=base,
                universe=universe_label,
                total_budget=args.max_candidates,
                use_ic_warmstart=not args.no_warmstart,
                allow_negative_weights=not args.no_negative_weights,
                search_algo=args.search_algo,
                on_progress=search_progress,
            )
        else:
            artifact = fit_global_preset_via_panel(
                search_rows,
                as_of_date=as_of,
                base_params=base,
                universe=universe_label,
                max_candidates=args.max_candidates,
                n_iterations=args.n_iterations,
                use_ic_warmstart=not args.no_warmstart,
                allow_negative_weights=not args.no_negative_weights,
                search_algo=args.search_algo,
                initial_overlay=initial_overlay,
                on_progress=search_progress,
            )
        if args.two_stage:
            artifact.metrics["two_stage"] = {
                "enabled": True,
                "proxy_candidates": args.proxy_candidates,
                "proxy_max_rows": args.proxy_max_rows,
                "expensive_candidates": args.max_candidates,
                "note": "cheap proxy uses deterministic regime/date-balanced row subset; expensive stage evaluates production replay panel",
            }
        search_elapsed = time.monotonic() - t0
        print(f"      search: {search_elapsed:.2f}s ({artifact.candidate_count}/{search_elapsed:.1f}s = {artifact.candidate_count/max(search_elapsed, 0.001):.0f} cand/sec, {artifact.metrics.get('search_iterations', 1)} iterations)")

        print(f"\n=== Results ===")
        print(f"  best objective score: {artifact.objective_score:.6f}")
        print(f"  candidates evaluated: {artifact.candidate_count}")
        print(f"  panel rows used:      {artifact.metrics.get('panel_n_rows', 0)}")
        for h in (5, 10, 20):
            m = artifact.metrics.get(f"objective_{h}d", {})
            print(f"  {h}d: n={m.get('sample_count', 0):4d} ic={m.get('ic', 0):+.3f} rank_ic={m.get('rank_ic', 0):+.3f} "
                  f"avg_ret={float(m.get('avg_return', 0))*100:+.2f}% top_ret={float(m.get('top_bucket_avg_return', 0))*100:+.2f}% "
                  f"top_win={m.get('top_bucket_win_rate', 0):.2f} spread={float(m.get('top_bottom_spread', 0))*100:+.2f}% "
                  f"mono={m.get('bucket_monotonicity', 0):+.2f} left_tail={float(m.get('top_bucket_left_tail', 0))*100:+.2f}% "
                  f"buy_n={m.get('buy_signals', 0)}")

        print(f"\n  Top 10 weight changes:")
        weight_deltas = [(k, v) for k, v in artifact.overlay.items() if "weights." in k]
        weight_deltas.sort(key=lambda kv: -abs(float(kv[1]) - 1.0))
        for k, v in weight_deltas[:10]:
            print(f"    {k} = {v:.3f}")

    if not args.dry_run:
        path = write_tuning_artifact(artifact)
        print(f"\n  artifact written: {path}")
    else:
        print(f"\n  [dry-run] artifact NOT written")
        print(f"  [dry-run] baseline YAML NOT touched: {args.base_yaml}")

    # ── OOS validation (Phase 5) ──────────────────────────────
    if args.oos:
        print(f"\n=== OOS Validation (held-out {len(val_rows)} rows from {val_dates[0]}..{val_dates[-1]}) ===")
        val_panel = panel_matrix_from_rows(val_rows)
        train_panel = panel_matrix_from_rows(train_rows)
        train_metrics_baseline = evaluate_overlay_on_panel(train_panel, {}, base)
        train_metrics_tuned = artifact.metrics
        val_metrics_baseline = evaluate_overlay_on_panel(val_panel, {}, base)
        val_metrics_tuned = evaluate_overlay_on_panel(val_panel, artifact.overlay, base)

        print(f"\n  {'Metric':30s}  {'Train base':>12s}  {'Train tuned':>12s}  {'Val base':>12s}  {'Val tuned':>12s}  {'Overfit':>10s}")
        print(f"  {'-'*30}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*10}")
        for h in (5, 10, 20):
            tb = train_metrics_baseline[f'objective_{h}d']['rank_ic']
            tt = train_metrics_tuned[f'objective_{h}d']['rank_ic']
            vb = val_metrics_baseline[f'objective_{h}d']['rank_ic']
            vt = val_metrics_tuned[f'objective_{h}d']['rank_ic']
            train_lift = tt - tb
            val_lift = vt - vb
            overfit = train_lift - val_lift
            mark = "✅" if vt > 0 and val_lift > 0 else ("⚠" if val_lift > 0 else "❌")
            print(f"  {f'{h}d rank_ic':30s}  {tb:>+12.4f}  {tt:>+12.4f}  {vb:>+12.4f}  {vt:>+12.4f}  {overfit:>+10.4f} {mark}")
        cb = train_metrics_baseline['composite_objective']['score']
        ct = train_metrics_tuned['composite_objective']['score']
        vcb = val_metrics_baseline['composite_objective']['score']
        vct = val_metrics_tuned['composite_objective']['score']
        print(f"  {'composite':30s}  {cb:>12.4f}  {ct:>12.4f}  {vcb:>12.4f}  {vct:>12.4f}  {(ct-cb)-(vct-vcb):>+10.4f}")
        print(f"\n  Headline: VAL 10d lift = {val_metrics_tuned['objective_10d']['rank_ic'] - val_metrics_baseline['objective_10d']['rank_ic']:+.4f}, "
              f"VAL 10d rank IC = {val_metrics_tuned['objective_10d']['rank_ic']:+.4f}")

    # ── Auto-promotion (Phase 4) ──────────────────────────────
    if args.auto_promote and args.dry_run:
        print(f"\n=== Auto-Promotion Gates ===")
        print("  [dry-run] auto-promotion skipped; no variant YAML or baseline YAML will be written")

    if args.auto_promote and not args.dry_run:
        print(f"\n=== Auto-Promotion Gates ===")
        if args.oos or k_fold_done:
            origin = "K-fold latest fold" if k_fold_done else "single OOS split"
            print(f"      (Gating on VALIDATION set from {origin}, not training)")
            candidate_metrics_for_gate = val_metrics_tuned
            baseline_metrics = val_metrics_baseline
        else:
            panel = panel_matrix_from_rows(rows)
            baseline_metrics = evaluate_overlay_on_panel(panel, {}, base)
            candidate_metrics_for_gate = artifact.metrics
        gate_config: dict = {}
        if args.k_fold_min_positive > 0:
            gate_config["g9_min_positive_folds"] = args.k_fold_min_positive
        gate_config["g4_min_improved_bucket_pct"] = args.regime_min_bucket_pct

        # Pick val panel for downstream stat checks (G4 regime / G5 bootstrap).
        # Latest fold = the artifact we'd promote (its overlay was tuned on the most
        # recent training data without leakage). Earlier folds' val rows ARE part of
        # the latest fold's training window, so pooling them would leak.
        val_panel_for_stats = None
        if args.oos or k_fold_done:
            if k_fold_done:
                val_panel_for_stats = panel_matrix_from_rows(folds[-1][1])
            elif args.oos:
                val_panel_for_stats = panel_matrix_from_rows(val_rows)

        # ── G5 Bootstrap CI: compute on val panel ─────────────
        # K-fold mode: use across-fold t-CI (4 independent OOS lifts → real CI on
        # mean lift; no leakage). Single-OOS mode: bootstrap on the val panel rows.
        bootstrap_results = None
        if k_fold_done and kfold_for_gate:
            bootstrap_results = kfold_aggregate_ci(
                kfold_for_gate,
                confidence=args.bootstrap_confidence,
            )
            print(f"      G5 across-fold t-CI: {len(kfold_for_gate)} folds, conf={args.bootstrap_confidence}")
        elif args.bootstrap_iterations > 0 and val_panel_for_stats is not None:
            t0_boot = time.monotonic()
            bootstrap_results = bootstrap_rank_ic_lift(
                val_panel_for_stats, artifact.overlay, base,
                n_iterations=args.bootstrap_iterations,
                confidence=args.bootstrap_confidence,
            )
            t_boot = time.monotonic() - t0_boot
            print(f"      bootstrap CI: {args.bootstrap_iterations} iter on val panel, {t_boot:.2f}s")

        # ── G4 Regime-bucketed: compute on val panel ──────────
        regime_results = None
        if val_panel_for_stats is not None:
            t0_reg = time.monotonic()
            regime_results = regime_bucketed_rank_ic_lift(
                val_panel_for_stats, artifact.overlay, base,
                min_samples_per_bucket=args.regime_min_samples,
            )
            t_reg = time.monotonic() - t0_reg
            n_buckets_total = sum(len(v) for v in regime_results.values())
            print(f"      regime breakdown: {n_buckets_total} (horizon, regime) buckets ≥ {args.regime_min_samples} samples, {t_reg*1000:.0f}ms")

        decision = evaluate_promotion_gates(
            candidate_metrics_for_gate, baseline_metrics, artifact.overlay,
            config=gate_config or None,
            kfold_results=kfold_for_gate,
            bootstrap_results=bootstrap_results,
            regime_breakdown=regime_results,
            artifact_metrics=artifact.metrics,
        )
        for g in decision.gates:
            mark = "✓" if g.passed else "✗"
            print(f"  {mark} {g.gate_id} {g.name:35s} passed={g.passed}")
            print(f"      {g.detail}")
        print(f"\n  → {decision.summary}")

        base_yaml = Path(args.base_yaml)
        if args.apply_to_baseline:
            variant_path = base_yaml
            print(f"      [apply-to-baseline] gates pass → will overwrite {base_yaml} with backup")
        elif args.variant_output:
            variant_path = Path(args.variant_output)
        else:
            variant_path = base_yaml.with_suffix(".variant.yaml")
        reject_dir = Path("/Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/rejected")
        result = auto_promote_if_passing(
            decision,
            candidate_overlay=artifact.overlay,
            base_yaml=base_yaml,
            variant_output=variant_path,
            reject_dir=reject_dir,
            backup=True,
        )
        # T1.4 horizon-selective output
        applied = result.get("horizons_applied") or []
        kept = result.get("horizons_kept_baseline") or []
        if result.get("variant_path"):
            if applied and not kept:
                print(f"\n  ✅ ACCEPTED ALL — variant YAML written: {result['variant_path']}")
            elif applied:
                print(f"\n  🟡 PARTIAL — horizon-selective variant written: {result['variant_path']}")
                print(f"     ▸ applied:        {', '.join(applied)} (passed G4+G5+G9 per-horizon)")
                print(f"     ▸ kept baseline:  {', '.join(kept)} (failed at least one per-horizon gate)")
            if result.get("backup_path"):
                print(f"     backup: {result['backup_path']}")
        else:
            print(f"\n  ⚠ REJECTED — no horizon passes per-horizon gates")
            print(f"     reject report: {result.get('reject_path', '(no reject_dir)')}")

    print(f"\n  total wall time: {panel_elapsed + search_elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
