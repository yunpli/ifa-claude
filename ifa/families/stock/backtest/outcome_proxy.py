"""Fast Stock Edge outcome proxy cache.

This module deliberately avoids the expensive production strategy replay. It
builds PIT-safe forward labels plus cheap sortable features so validation runs can
inspect label quality, cohort drift, and simple feature direction before spending
hours on the full strategy matrix panel.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .replay_panel import HORIZONS, _membership_hash

OUTCOME_PROXY_ROOT = Path("/Users/neoclaw/claude/ifaenv/data/stock/outcome_proxy")


@dataclass(frozen=True)
class OutcomeProxyManifest:
    universe_id: str
    as_of_dates: list[dt.date]
    n_rows: int
    requested_rows: int
    failed_rows: int
    cache_path: str
    manifest_path: str
    built_at: dt.datetime
    runtime_sec: float
    feature_version: str = "outcome_proxy_v2"
    universe_selection: dict[str, Any] = field(default_factory=dict)
    failure_details: list[dict[str, Any]] = field(default_factory=list)
    timing: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "as_of_dates": [d.isoformat() for d in self.as_of_dates],
            "built_at": self.built_at.isoformat(),
            "failure_rate": round(self.failed_rows / self.requested_rows, 6) if self.requested_rows else 0.0,
        }


def build_outcome_proxy_cache(
    engine: Engine,
    *,
    universe_id: str,
    as_of_dates: Sequence[dt.date],
    ts_codes: Sequence[str],
    ts_codes_by_date: Mapping[dt.date, Sequence[str]] | None = None,
    universe_selection: dict[str, Any] | None = None,
    force: bool = False,
) -> tuple[pd.DataFrame, OutcomeProxyManifest]:
    """Build or load PIT-safe labels plus cheap features.

    The cache key is intentionally separate from the full replay panel cache. It
    includes exact date-specific membership but excludes strategy params because
    no production strategy scores are computed here.
    """
    chunks = [
        (as_of, list(ts_codes_by_date.get(as_of, [])) if ts_codes_by_date else list(ts_codes))
        for as_of in as_of_dates
    ]
    membership_hash = _membership_hash(chunks)
    cache_path = _proxy_cache_path(universe_id, as_of_dates, membership_hash)
    manifest_path = cache_path.with_suffix(".manifest.json")
    if not force and cache_path.exists() and manifest_path.exists():
        df = pd.read_parquet(cache_path)
        manifest = _load_proxy_manifest(manifest_path)
        return df, manifest

    started = time.monotonic()
    rows, failures, timing = _build_proxy_rows_batch(engine, chunks)

    df = pd.DataFrame(rows)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, compression="snappy", index=False)
    requested = sum(len(codes) for _, codes in chunks)
    manifest = OutcomeProxyManifest(
        universe_id=universe_id,
        as_of_dates=list(as_of_dates),
        n_rows=len(df),
        requested_rows=requested,
        failed_rows=len(failures),
        cache_path=str(cache_path),
        manifest_path=str(manifest_path),
        built_at=dt.datetime.now(dt.timezone.utc),
        runtime_sec=round(time.monotonic() - started, 3),
        universe_selection=dict(universe_selection or {}),
        failure_details=failures[:200],
        timing=timing,
    )
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return df, manifest


def benchmark_outcome_proxy_builders(
    engine: Engine,
    *,
    as_of_dates: Sequence[dt.date],
    ts_codes: Sequence[str],
    ts_codes_by_date: Mapping[dt.date, Sequence[str]] | None = None,
    max_dates: int = 3,
) -> dict[str, Any]:
    """Compare legacy per-date proxy build against the batch builder.

    This is a diagnostic helper only. It does not write cache files and it caps
    the sampled PIT dates so a production run can produce a before/after timing
    without paying the full old-path cost.
    """
    sample_dates = list(as_of_dates)[: max(1, int(max_dates))]
    chunks = [
        (as_of, list(ts_codes_by_date.get(as_of, [])) if ts_codes_by_date else list(ts_codes))
        for as_of in sample_dates
    ]
    requested = sum(len(codes) for _, codes in chunks)

    t0 = time.monotonic()
    legacy_rows: list[dict[str, Any]] = []
    legacy_failures: list[dict[str, Any]] = []
    for as_of, codes in chunks:
        if not codes:
            continue
        rows, failures = _build_proxy_rows_for_date(engine, as_of, codes)
        legacy_rows.extend(rows)
        legacy_failures.extend(failures)
    legacy_sec = time.monotonic() - t0

    t0 = time.monotonic()
    batch_rows, batch_failures, batch_timing = _build_proxy_rows_batch(engine, chunks)
    batch_sec = time.monotonic() - t0

    return {
        "sample_dates": [d.isoformat() for d in sample_dates],
        "requested_rows": requested,
        "legacy": {
            "runtime_sec": round(legacy_sec, 3),
            "rows": len(legacy_rows),
            "failures": len(legacy_failures),
            "rows_per_min": round(len(legacy_rows) * 60 / max(legacy_sec, 1e-9), 1),
            "method": "per-PIT-date SQL loop",
        },
        "batch": {
            "runtime_sec": round(batch_sec, 3),
            "rows": len(batch_rows),
            "failures": len(batch_failures),
            "rows_per_min": round(len(batch_rows) * 60 / max(batch_sec, 1e-9), 1),
            "method": "one window query per source table plus in-memory PIT lookup",
            "timing": batch_timing,
        },
        "speedup": round(legacy_sec / max(batch_sec, 1e-9), 3),
        "row_count_match": len(legacy_rows) == len(batch_rows),
        "failure_count_match": len(legacy_failures) == len(batch_failures),
    }


def summarize_outcome_proxy(df: pd.DataFrame) -> dict[str, Any]:
    """Return outcome-first diagnostics for cheap features."""
    if df.empty:
        return {"rows": 0, "horizons": {}, "feature_rank_ic": {}}
    feature_cols = [
        "ret_5d_pct",
        "ret_20d_pct",
        "volatility_20d_pct",
        "avg_amount_20d",
        "moneyflow_net_5d_pct_amount",
        "total_mv",
        "sector_cycle_accumulation_score",
        "leader_quality_score",
    ]
    out: dict[str, Any] = {
        "rows": int(len(df)),
        "date_count": int(df["as_of_date"].nunique()) if "as_of_date" in df else 0,
        "stock_count": int(df["ts_code"].nunique()) if "ts_code" in df else 0,
        "horizons": {},
        "feature_rank_ic": {},
        "cheap_composite_rank_ic": {},
    }
    for h in HORIZONS:
        label = f"forward_{h}d_return"
        valid = df[label].notna() if label in df else pd.Series(False, index=df.index)
        values = df.loc[valid, label].astype(float)
        out["horizons"][f"{h}d"] = {
            "n": int(valid.sum()),
            "avg_return": float(values.mean() / 100.0) if len(values) else 0.0,
            "median_return": float(values.median() / 100.0) if len(values) else 0.0,
            "positive_rate": float((values > 0).mean()) if len(values) else 0.0,
            "p10_return": float(values.quantile(0.10) / 100.0) if len(values) else 0.0,
            "p90_return": float(values.quantile(0.90) / 100.0) if len(values) else 0.0,
        }
        feature_ics: dict[str, float] = {}
        for col in feature_cols:
            if col not in df:
                continue
            mask = valid & df[col].notna()
            feature_ics[col] = _rank_ic(df.loc[mask, col].astype(float), df.loc[mask, label].astype(float))
        out["feature_rank_ic"][f"{h}d"] = feature_ics
        composite = _cheap_composite_score(df)
        mask = valid & composite.notna()
        out["cheap_composite_rank_ic"][f"{h}d"] = _rank_ic(composite[mask], df.loc[mask, label].astype(float))
    return out


def compare_proxy_candidate_families(df: pd.DataFrame) -> dict[str, Any]:
    """Compare finance-motivated cheap proxy score families on one cached panel.

    This is still a pre-replay diagnostic surface. It intentionally does not
    mutate production Stock Edge YAML or call the expensive strategy matrix. The
    families below encode hypotheses from recent outcome diagnostics: short-term
    price momentum has been unstable, while medium liquidity, large-cap bias,
    quality moneyflow, weak-industry avoidance, and left-tail control have shown
    more durable 10d/20d alignment.
    """
    if df.empty:
        return {"rows": 0, "families": {}, "ranking": []}

    families = score_proxy_candidate_families(df)

    out: dict[str, Any] = {
        "rows": int(len(df)),
        "date_count": int(df["as_of_date"].nunique()) if "as_of_date" in df else 0,
        "stock_count": int(df["ts_code"].nunique()) if "ts_code" in df else 0,
        "families": {},
    }
    for name, score in families.items():
        out["families"][name] = _score_proxy_family(df, score)

    def ranking_key(item: tuple[str, dict[str, Any]]) -> tuple[float, float, float]:
        metrics = item[1].get("horizons", {})
        h10 = float(metrics.get("10d", {}).get("rank_ic", 0.0) or 0.0)
        h20 = float(metrics.get("20d", {}).get("rank_ic", 0.0) or 0.0)
        mar = float(item[1].get("month_stability", {}).get("2026-03", {}).get("10d", {}).get("rank_ic", -1.0) or -1.0)
        return (h10 + h20, min(h10, h20), mar)

    out["ranking"] = [
        {"family": name, "score": round(ranking_key((name, payload))[0], 6)}
        for name, payload in sorted(out["families"].items(), key=ranking_key, reverse=True)
    ]
    return out


def score_proxy_candidate_families(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Return cheap proxy family scores for PIT-safe row selection.

    These scores are research candidates only. They are deliberately kept outside
    production Stock Edge YAML until a later full-replay/OOS gate proves that the
    family direction survives the expensive strategy-matrix path.
    """
    features = _proxy_candidate_features(df)
    return {
        "baseline_cheap_composite_v1": _cheap_composite_score(df),
        "mid_liquidity_large_cap_quality_flow": (
            0.30 * features["moneyflow_quality"]
            + 0.22 * features["mid_liquidity"]
            + 0.18 * features["large_cap"]
            + 0.14 * features["industry_tilt_static"]
            + 0.10 * features["low_left_tail_risk"]
            + 0.06 * features["regime_gate"]
        ),
        "industry_relative_momentum_flow": (
            0.28 * features["industry_relative_flow"]
            + 0.22 * features["industry_relative_reversal"]
            + 0.20 * features["industry_tilt_static"]
            + 0.12 * features["mid_liquidity"]
            + 0.10 * features["large_cap"]
            + 0.08 * features["low_left_tail_risk"]
        ),
        "regime_aware_10_20d_selection": (
            0.24 * features["moneyflow_quality"]
            + 0.20 * features["large_cap"]
            + 0.18 * features["mid_liquidity"]
            + 0.14 * features["low_left_tail_risk"]
            + 0.14 * features["regime_adjusted_momentum"]
            + 0.10 * features["industry_tilt_dynamic"]
        ),
        "weak_industry_avoid_quality_flow": (
            0.30 * features["moneyflow_quality"]
            + 0.22 * features["weak_industry_avoid"]
            + 0.16 * features["large_cap"]
            + 0.14 * features["mid_liquidity"]
            + 0.12 * features["low_left_tail_risk"]
            + 0.06 * features["industry_relative_flow"]
        ),
        "sector_cycle_leader": (
            0.30 * features["sector_cycle_accumulation"]
            + 0.22 * features["leader_quality"]
            + 0.14 * features["sector_flow_persistence"]
            + 0.12 * features["anti_retail_crowding"]
            + 0.10 * features["sector_heat_confirmation"]
            + 0.08 * features["tradability"]
            + 0.04 * features["theme_heat_match"]
        ),
    }


def _proxy_candidate_features(df: pd.DataFrame) -> dict[str, pd.Series]:
    date_key = df["as_of_date"] if "as_of_date" in df else pd.Series("all", index=df.index)
    industry_key = df["l1_name"] if "l1_name" in df else pd.Series("unknown", index=df.index)
    industry_name = industry_key.fillna("unknown").astype(str)

    ret_5 = _num(df, "ret_5d_pct")
    ret_20 = _num(df, "ret_20d_pct")
    flow = _num(df, "moneyflow_net_5d_pct_amount")
    amount = _num(df, "avg_amount_20d")
    mv = _num(df, "total_mv")
    vol = _num(df, "volatility_20d_pct")
    turnover = _num(df, "turnover_rate")
    sector_main_5d = _num(df, "sector_main_net_5d_pct_amount")
    sector_retail_5d = _num(df, "sector_retail_net_5d_pct_amount")
    sector_main_persist = _num(df, "sector_main_persistence_5d")
    sector_return_5d = _num(df, "sector_return_5d")
    sector_diffusion = _num(df, "sector_diffusion_score")
    sector_top5_share = _num(df, "sector_top5_main_net_share")
    stock_main_5d = _num(df, "stock_main_net_5d_pct_amount")
    stock_main_persist = _num(df, "stock_main_persistence_5d")
    leader_quality_raw = _num(df, "leader_quality_score")
    sector_cycle_raw = _num(df, "sector_cycle_accumulation_score")
    theme_heat = _num(df, "theme_heat_score")

    liquidity_rank = _group_rank(amount, date_key)
    size_rank = _group_rank(mv, date_key)
    vol_rank = _group_rank(vol, date_key)
    turnover_rank = _group_rank(turnover, date_key)
    flow_rank = _group_rank(flow.clip(lower=-0.12, upper=0.12), date_key)
    sector_main_rank = _group_rank(sector_main_5d.clip(lower=-0.12, upper=0.12), date_key)
    sector_retail_rank = _group_rank(sector_retail_5d.clip(lower=-0.12, upper=0.12), date_key)
    sector_return_rank = _group_rank(sector_return_5d, date_key)
    sector_diffusion_rank = _group_rank(sector_diffusion, date_key)
    stock_main_rank = _group_rank(stock_main_5d.clip(lower=-0.12, upper=0.12), [date_key, industry_key])
    leader_quality_rank = _group_rank(leader_quality_raw, date_key)
    sector_cycle_rank = _group_rank(sector_cycle_raw, date_key)
    theme_heat_rank = _group_rank(theme_heat, date_key)

    industry_flow_rank = _group_rank(flow, [date_key, industry_key])
    industry_ret5_rank = _group_rank(ret_5, [date_key, industry_key])
    industry_ret20_rank = _group_rank(ret_20, [date_key, industry_key])

    favored = {"有色金属", "家用电器", "建筑装饰"}
    weak = {"房地产", "商贸零售", "医药生物", "食品饮料", "轻工制造"}
    industry_tilt = pd.Series(0.50, index=df.index, dtype=float)
    industry_tilt[industry_name.isin(favored)] = 0.75
    industry_tilt[industry_name.isin(weak)] = 0.25

    dynamic_tilt = industry_tilt.copy()
    dynamic_tilt = dynamic_tilt.where(flow_rank < 0.85, dynamic_tilt + 0.08).clip(0.0, 1.0)
    weak_avoid = pd.Series(0.65, index=df.index, dtype=float)
    weak_avoid[industry_name.isin(weak)] = 0.20
    weak_avoid = weak_avoid.where(flow_rank < 0.80, weak_avoid + 0.12).clip(0.0, 1.0)

    regime = df["regime"].fillna("unknown") if "regime" in df else pd.Series("unknown", index=df.index)
    regime_gate = pd.Series(0.55, index=df.index, dtype=float)
    regime_gate[regime.isin(["trend_continuation", "early_risk_on"])] = 0.68
    regime_gate[regime.isin(["cooldown"])] = 0.35

    # Momentum is de-emphasized and made regime-aware: avoid rewarding extended
    # 20d winners in cooldown/range-bound regimes where March 2026 reversed hard.
    reversal = 1.0 - industry_ret20_rank
    trend_follow = 0.65 * industry_ret5_rank + 0.35 * industry_ret20_rank
    regime_adjusted = trend_follow.where(regime.isin(["trend_continuation", "early_risk_on"]), reversal)
    risk_flags = df["sector_risk_flags"].fillna("").astype(str) if "sector_risk_flags" in df else pd.Series("", index=df.index)
    crowded_flag = risk_flags.str.contains("retail_chase|leader_crowded|crowding", case=False, regex=True)
    anti_crowding = (0.62 * (1.0 - sector_retail_rank) + 0.38 * (1.0 - _safe_rank(sector_top5_share, date_key))).clip(0.0, 1.0)
    anti_crowding = anti_crowding.where(~crowded_flag, anti_crowding * 0.55)
    sector_accumulation = (
        0.44 * sector_main_rank
        + 0.22 * _bounded01(sector_main_persist)
        + 0.18 * anti_crowding
        + 0.10 * sector_diffusion_rank
        + 0.06 * sector_cycle_rank
    ).clip(0.0, 1.0)
    leader_quality = (
        0.30 * stock_main_rank
        + 0.20 * _bounded01(stock_main_persist)
        + 0.18 * industry_ret5_rank
        + 0.14 * industry_flow_rank
        + 0.10 * leader_quality_rank
        + 0.08 * (1.0 - vol_rank)
    ).clip(0.0, 1.0)
    heat_confirmation = (
        0.42 * sector_return_rank
        + 0.34 * sector_diffusion_rank
        + 0.24 * _bounded01(_num(df, "sector_price_positive_breadth"))
    ).clip(0.0, 1.0)
    mid_liquidity = (1.0 - (liquidity_rank - 0.52).abs() / 0.52).clip(0.0, 1.0)

    return {
        "moneyflow_quality": (0.70 * flow_rank + 0.30 * (1.0 - vol_rank)).clip(0.0, 1.0),
        "mid_liquidity": mid_liquidity,
        "large_cap": size_rank.fillna(0.5).clip(0.0, 1.0),
        "industry_tilt_static": industry_tilt,
        "industry_tilt_dynamic": dynamic_tilt,
        "weak_industry_avoid": weak_avoid,
        "low_left_tail_risk": (0.65 * (1.0 - vol_rank) + 0.35 * (1.0 - turnover_rank)).clip(0.0, 1.0),
        "regime_gate": regime_gate,
        "industry_relative_flow": industry_flow_rank.fillna(flow_rank).clip(0.0, 1.0),
        "industry_relative_reversal": reversal.clip(0.0, 1.0),
        "regime_adjusted_momentum": regime_adjusted.clip(0.0, 1.0),
        "sector_cycle_accumulation": sector_accumulation.fillna(0.5),
        "leader_quality": leader_quality.fillna(0.5),
        "sector_flow_persistence": _bounded01(sector_main_persist).fillna(sector_main_rank).fillna(0.5),
        "anti_retail_crowding": anti_crowding.fillna(0.5),
        "sector_heat_confirmation": heat_confirmation.fillna(0.5),
        "tradability": (0.55 * mid_liquidity + 0.45 * (1.0 - vol_rank)).clip(0.0, 1.0),
        "theme_heat_match": theme_heat_rank.fillna(0.5).clip(0.0, 1.0),
    }


def _score_proxy_family(df: pd.DataFrame, score: pd.Series) -> dict[str, Any]:
    payload: dict[str, Any] = {"horizons": {}, "month_stability": {}}
    for h in HORIZONS:
        label = f"forward_{h}d_return"
        if label not in df:
            continue
        payload["horizons"][f"{h}d"] = _proxy_horizon_metrics(score, df[label])

    months = pd.to_datetime(df["as_of_date"]).dt.strftime("%Y-%m") if "as_of_date" in df else pd.Series("all", index=df.index)
    for month, sub_idx in months.groupby(months).groups.items():
        month_payload: dict[str, Any] = {}
        for h in HORIZONS:
            label = f"forward_{h}d_return"
            if label not in df:
                continue
            idx = list(sub_idx)
            month_payload[f"{h}d"] = _proxy_horizon_metrics(score.iloc[idx], df[label].iloc[idx])
        payload["month_stability"][str(month)] = month_payload
    return payload


def _proxy_horizon_metrics(score: pd.Series, label: pd.Series) -> dict[str, Any]:
    mask = score.notna() & label.notna()
    score_v = score[mask].astype(float)
    label_v = label[mask].astype(float)
    if len(score_v) < 30:
        return {"n": int(len(score_v)), "rank_ic": 0.0, "top_bucket_return": 0.0, "top_bucket_win_rate": 0.0, "top_vs_bottom_spread": 0.0}

    top_cut = score_v.quantile(0.80)
    bottom_cut = score_v.quantile(0.20)
    top = label_v[score_v >= top_cut]
    bottom = label_v[score_v <= bottom_cut]
    return {
        "n": int(len(score_v)),
        "rank_ic": round(_rank_ic(score_v, label_v), 6),
        "top_bucket_return": round(float(top.mean() / 100.0) if len(top) else 0.0, 6),
        "top_bucket_win_rate": round(float((top > 0).mean()) if len(top) else 0.0, 6),
        "top_vs_bottom_spread": round(float((top.mean() - bottom.mean()) / 100.0) if len(top) and len(bottom) else 0.0, 6),
    }


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _group_rank(values: pd.Series, group_keys: Any) -> pd.Series:
    return values.groupby(group_keys).rank(pct=True).fillna(0.5)


def _safe_rank(values: pd.Series, group_keys: Any) -> pd.Series:
    return _group_rank(values, group_keys).fillna(0.5).clip(0.0, 1.0)


def _bounded01(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").clip(0.0, 1.0)


def _proxy_cache_path(universe_id: str, as_of_dates: Sequence[dt.date], membership_hash: str) -> Path:
    sorted_dates = sorted(as_of_dates)
    date_sig = f"{sorted_dates[0].isoformat()}_{sorted_dates[-1].isoformat()}_{len(sorted_dates)}"
    suffix = hashlib.sha256(f"{universe_id}|{date_sig}|{membership_hash}|outcome_proxy_v2".encode()).hexdigest()[:12]
    return OUTCOME_PROXY_ROOT / f"{universe_id}__{sorted_dates[0]:%Y%m%d}_{sorted_dates[-1]:%Y%m%d}__{suffix}.parquet"


def _load_proxy_manifest(path: Path) -> OutcomeProxyManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return OutcomeProxyManifest(
        universe_id=str(raw["universe_id"]),
        as_of_dates=[dt.date.fromisoformat(d) for d in raw["as_of_dates"]],
        n_rows=int(raw["n_rows"]),
        requested_rows=int(raw["requested_rows"]),
        failed_rows=int(raw["failed_rows"]),
        cache_path=str(raw["cache_path"]),
        manifest_path=str(raw["manifest_path"]),
        built_at=dt.datetime.fromisoformat(raw["built_at"]),
        runtime_sec=float(raw.get("runtime_sec") or 0.0),
        feature_version=str(raw.get("feature_version") or "outcome_proxy_v1"),
        universe_selection=dict(raw.get("universe_selection") or {}),
        failure_details=list(raw.get("failure_details") or []),
        timing=dict(raw.get("timing") or {}),
    )


def _build_proxy_rows_batch(
    engine: Engine,
    chunks: Sequence[tuple[dt.date, list[str]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    """Batch-build proxy rows for all PIT dates with one query per source table.

    The old path queried daily/basic/member/regime once per PIT date. A 60-date
    proxy panel therefore did hundreds of DB round-trips before any feature work.
    This path pulls the full PIT-safe window once, then performs date/code lookup
    in memory. It preserves the previous semantics: labels use future trading
    rows from `raw_daily`, `daily_basic` and SW membership use latest rows visible
    at each PIT date, and missing anchors remain explicit failures.
    """
    requested = [(as_of, list(dict.fromkeys(codes))) for as_of, codes in chunks if codes]
    if not requested:
        return [], [], {"batch_total_sec": 0.0}

    t_all = time.monotonic()
    as_of_dates = sorted({as_of for as_of, _ in requested})
    union_codes = sorted({code for _, codes in requested for code in codes})
    min_as_of = min(as_of_dates)
    max_as_of = max(as_of_dates)
    daily_start = min_as_of - dt.timedelta(days=100)
    daily_end = max_as_of + dt.timedelta(days=45)
    basic_start = min_as_of - dt.timedelta(days=45)
    min_snapshot = min_as_of.replace(day=1)
    max_snapshot = max_as_of.replace(day=1)

    t0 = time.monotonic()
    with engine.connect() as conn:
        daily = pd.read_sql_query(
            text("""
                SELECT ts_code, trade_date, open, high, low, close, pct_chg, amount
                FROM smartmoney.raw_daily
                WHERE ts_code = ANY(:codes)
                  AND trade_date >= :start AND trade_date <= :end
                ORDER BY ts_code, trade_date
            """),
            conn,
            params={"codes": union_codes, "start": daily_start, "end": daily_end},
        )
        flow = pd.read_sql_query(
            text("""
                SELECT ts_code, trade_date, net_mf_amount
                FROM smartmoney.raw_moneyflow
                WHERE ts_code = ANY(:codes)
                  AND trade_date >= :start AND trade_date <= :end
                ORDER BY ts_code, trade_date
            """),
            conn,
            params={"codes": union_codes, "start": daily_start, "end": max_as_of},
        )
        basic = pd.read_sql_query(
            text("""
                SELECT ts_code, trade_date, total_mv, circ_mv, turnover_rate
                FROM smartmoney.raw_daily_basic
                WHERE ts_code = ANY(:codes)
                  AND trade_date >= :start AND trade_date <= :end
                ORDER BY ts_code, trade_date
            """),
            conn,
            params={"codes": union_codes, "start": basic_start, "end": max_as_of},
        )
        members = pd.read_sql_query(
            text("""
                SELECT ts_code, snapshot_month, l1_code, l1_name, l2_code, l2_name, name
                FROM smartmoney.sw_member_monthly
                WHERE ts_code = ANY(:codes)
                  AND snapshot_month <= :max_snapshot
                ORDER BY ts_code, snapshot_month
            """),
            conn,
            params={"codes": union_codes, "max_snapshot": max_snapshot},
        )
        stock_orderflow = pd.read_sql_query(
            text("""
                SELECT trade_date, ts_code, amount_yuan, main_net_yuan, retail_net_yuan,
                       main_net_ratio, retail_net_ratio, turnover_rate, quality_flag
                FROM sme.sme_stock_orderflow_daily
                WHERE ts_code = ANY(:codes)
                  AND trade_date >= :start AND trade_date <= :end
                ORDER BY ts_code, trade_date
            """),
            conn,
            params={"codes": union_codes, "start": daily_start, "end": max_as_of},
        )
        sector_flow = pd.read_sql_query(
            text("""
                SELECT f.trade_date, f.l2_code, f.l2_name, f.l1_code, f.l1_name,
                       f.sector_amount_yuan, f.sector_return_amount_weight,
                       f.main_net_yuan, f.retail_net_yuan, f.main_net_ratio,
                       f.retail_net_ratio, f.main_positive_breadth,
                       f.retail_positive_breadth, f.price_positive_breadth,
                       f.top5_main_net_share, f.leader_ts_code, f.leader_main_net_yuan,
                       d.diffusion_score, d.diffusion_phase,
                       s.current_state, s.state_score, s.state_confidence, s.risk_flags_json
                FROM sme.sme_sector_orderflow_daily f
                LEFT JOIN sme.sme_sector_diffusion_daily d
                  ON d.trade_date = f.trade_date AND d.l2_code = f.l2_code
                LEFT JOIN sme.sme_sector_state_daily s
                  ON s.trade_date = f.trade_date AND s.l2_code = f.l2_code
                WHERE f.trade_date >= :start AND f.trade_date <= :end
                  AND f.l2_code IN (
                      SELECT DISTINCT l2_code
                      FROM smartmoney.sw_member_monthly
                      WHERE ts_code = ANY(:codes)
                        AND snapshot_month <= :max_snapshot
                        AND l2_code IS NOT NULL
                  )
                ORDER BY f.l2_code, f.trade_date
            """),
            conn,
            params={"codes": union_codes, "start": daily_start, "end": max_as_of, "max_snapshot": max_snapshot},
        )
        regimes = pd.read_sql_query(
            text("""
                SELECT trade_date, regime
                FROM ta.regime_daily
                WHERE trade_date = ANY(:dates)
            """),
            conn,
            params={"dates": as_of_dates},
        )
    query_sec = time.monotonic() - t0

    t0 = time.monotonic()
    for df in (daily, flow, basic):
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    if not members.empty:
        members["snapshot_month"] = pd.to_datetime(members["snapshot_month"]).dt.date
    for df in (stock_orderflow, sector_flow):
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    if not regimes.empty:
        regimes["trade_date"] = pd.to_datetime(regimes["trade_date"]).dt.date

    daily_by_code = _group_sorted_frames(daily, "ts_code", "trade_date")
    flow_by_code = _group_sorted_frames(flow, "ts_code", "trade_date")
    basic_by_code = _group_sorted_frames(basic, "ts_code", "trade_date")
    member_by_code = _group_sorted_frames(members, "ts_code", "snapshot_month")
    stock_orderflow_by_code = _group_sorted_frames(stock_orderflow, "ts_code", "trade_date")
    sector_flow_by_l2 = _group_sorted_frames(sector_flow, "l2_code", "trade_date")
    regime_by_date = {
        row["trade_date"]: str(row["regime"])
        for row in regimes.to_dict(orient="records")
        if row.get("regime")
    } if not regimes.empty else {}
    index_sec = time.monotonic() - t0

    t0 = time.monotonic()
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for as_of, codes in requested:
        snapshot_month = as_of.replace(day=1)
        regime = regime_by_date.get(as_of)
        for code in codes:
            sub = daily_by_code.get(str(code))
            if sub is None or sub.empty:
                failures.append({"ts_code": str(code), "as_of_date": as_of.isoformat(), "reason": "missing_daily_rows"})
                continue
            row = _build_proxy_row_from_frames(
                code=str(code),
                as_of=as_of,
                snapshot_month=snapshot_month,
                regime=regime,
                daily=sub,
                flow=flow_by_code.get(str(code)),
                basic=basic_by_code.get(str(code)),
                members=member_by_code.get(str(code)),
                stock_orderflow=stock_orderflow_by_code.get(str(code)),
                sector_flow_by_l2=sector_flow_by_l2,
            )
            if row is None:
                failures.append({"ts_code": str(code), "as_of_date": as_of.isoformat(), "reason": "missing_forward_anchor"})
            else:
                rows.append(row)
    assemble_sec = time.monotonic() - t0
    return rows, failures, {
        "batch_total_sec": round(time.monotonic() - t_all, 3),
        "query_sec": round(query_sec, 3),
        "index_sec": round(index_sec, 3),
        "assemble_sec": round(assemble_sec, 3),
        "query_daily_rows": float(len(daily)),
        "query_flow_rows": float(len(flow)),
        "query_basic_rows": float(len(basic)),
        "query_member_rows": float(len(members)),
        "query_stock_orderflow_rows": float(len(stock_orderflow)),
        "query_sector_flow_rows": float(len(sector_flow)),
    }


def _group_sorted_frames(df: pd.DataFrame, key: str, sort_col: str) -> dict[str, pd.DataFrame]:
    if df.empty:
        return {}
    return {
        str(code): sub.sort_values(sort_col).reset_index(drop=True)
        for code, sub in df.groupby(key, sort=False)
    }


def _build_proxy_row_from_frames(
    *,
    code: str,
    as_of: dt.date,
    snapshot_month: dt.date,
    regime: str | None,
    daily: pd.DataFrame,
    flow: pd.DataFrame | None,
    basic: pd.DataFrame | None,
    members: pd.DataFrame | None,
    stock_orderflow: pd.DataFrame | None = None,
    sector_flow_by_l2: Mapping[str, pd.DataFrame] | None = None,
) -> dict[str, Any] | None:
    label = _forward_labels_from_daily_frame(daily, as_of)
    if label is None:
        return None
    hist = daily[daily["trade_date"] <= as_of].tail(20)
    if hist.empty:
        return None
    close = float(hist["close"].iloc[-1] or 0.0)
    ret_5 = _window_return_pct(hist["close"], 5)
    ret_20 = _window_return_pct(hist["close"], 20)

    net5 = math.nan
    if flow is not None and not flow.empty:
        flow_hist = flow[flow["trade_date"] <= as_of].tail(5)
        if not flow_hist.empty:
            net5 = float(flow_hist["net_mf_amount"].sum())
    amount5 = float(hist.tail(5)["amount"].sum()) if not hist.empty else math.nan

    b: Mapping[str, Any] = {}
    if basic is not None and not basic.empty:
        b_hist = basic[basic["trade_date"] <= as_of].tail(1)
        if not b_hist.empty:
            b = b_hist.iloc[0].to_dict()

    m: Mapping[str, Any] = {}
    if members is not None and not members.empty:
        m_hist = members[members["snapshot_month"] <= snapshot_month].tail(1)
        if not m_hist.empty:
            m = m_hist.iloc[0].to_dict()
    stock_cycle = _stock_cycle_features(stock_orderflow, as_of)
    sector_cycle = _sector_cycle_features(
        (sector_flow_by_l2 or {}).get(str(m.get("l2_code"))) if m.get("l2_code") else None,
        as_of,
    )

    return {
        "ts_code": code,
        "as_of_date": as_of,
        "name": m.get("name"),
        "l1_code": m.get("l1_code"),
        "l1_name": m.get("l1_name"),
        "l2_code": m.get("l2_code"),
        "l2_name": m.get("l2_name"),
        "regime": regime,
        "entry_close": close,
        "ret_5d_pct": ret_5,
        "ret_20d_pct": ret_20,
        "volatility_20d_pct": float(hist["pct_chg"].std()) if len(hist) >= 5 else math.nan,
        "avg_amount_20d": float(hist["amount"].mean()) if len(hist) else math.nan,
        "moneyflow_net_5d": net5,
        "moneyflow_net_5d_pct_amount": float(net5 / amount5) if amount5 and not math.isnan(net5) else math.nan,
        "total_mv": _float_or_nan(b.get("total_mv")),
        "circ_mv": _float_or_nan(b.get("circ_mv")),
        "turnover_rate": _float_or_nan(b.get("turnover_rate")),
        **stock_cycle,
        **sector_cycle,
        **label,
    }


def _build_proxy_rows_for_date(engine: Engine, as_of: dt.date, codes: Sequence[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start = as_of - dt.timedelta(days=100)
    end = as_of + dt.timedelta(days=45)
    snapshot_month = as_of.replace(day=1)
    with engine.connect() as conn:
        daily = pd.read_sql_query(
            text("""
                SELECT ts_code, trade_date, open, high, low, close, pct_chg, amount
                FROM smartmoney.raw_daily
                WHERE ts_code = ANY(:codes)
                  AND trade_date >= :start AND trade_date <= :end
                ORDER BY ts_code, trade_date
            """),
            conn,
            params={"codes": list(codes), "start": start, "end": end},
        )
        flow = pd.read_sql_query(
            text("""
                SELECT ts_code, trade_date, net_mf_amount
                FROM smartmoney.raw_moneyflow
                WHERE ts_code = ANY(:codes)
                  AND trade_date >= :start AND trade_date <= :as_of
                ORDER BY ts_code, trade_date
            """),
            conn,
            params={"codes": list(codes), "start": start, "as_of": as_of},
        )
        basic = pd.read_sql_query(
            text("""
                SELECT DISTINCT ON (ts_code) ts_code, trade_date, total_mv, circ_mv, turnover_rate
                FROM smartmoney.raw_daily_basic
                WHERE ts_code = ANY(:codes) AND trade_date <= :as_of
                ORDER BY ts_code, trade_date DESC
            """),
            conn,
            params={"codes": list(codes), "as_of": as_of},
        )
        members = pd.read_sql_query(
            text("""
                SELECT DISTINCT ON (ts_code) ts_code, l1_code, l1_name, l2_code, l2_name, name
                FROM smartmoney.sw_member_monthly
                WHERE ts_code = ANY(:codes) AND snapshot_month <= :snapshot_month
                ORDER BY ts_code, snapshot_month DESC
            """),
            conn,
            params={"codes": list(codes), "snapshot_month": snapshot_month},
        )
        regime_row = conn.execute(
            text("SELECT regime FROM ta.regime_daily WHERE trade_date = :d"),
            {"d": as_of},
        ).fetchone()
    regime = str(regime_row[0]) if regime_row and regime_row[0] else None
    if not daily.empty:
        daily["trade_date"] = pd.to_datetime(daily["trade_date"]).dt.date
    if not flow.empty:
        flow["trade_date"] = pd.to_datetime(flow["trade_date"]).dt.date

    basic_by_code = {str(r["ts_code"]): r for r in basic.to_dict(orient="records")} if not basic.empty else {}
    member_by_code = {str(r["ts_code"]): r for r in members.to_dict(orient="records")} if not members.empty else {}
    flow_by_code = {str(code): sub.sort_values("trade_date") for code, sub in flow.groupby("ts_code")} if not flow.empty else {}

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for code, sub in daily.groupby("ts_code") if not daily.empty else []:
        code_s = str(code)
        sub = sub.sort_values("trade_date").reset_index(drop=True)
        label = _forward_labels_from_daily_frame(sub, as_of)
        if label is None:
            failures.append({"ts_code": code_s, "as_of_date": as_of.isoformat(), "reason": "missing_forward_anchor"})
            continue
        hist = sub[sub["trade_date"] <= as_of].tail(20)
        if hist.empty:
            failures.append({"ts_code": code_s, "as_of_date": as_of.isoformat(), "reason": "missing_history"})
            continue
        close = float(hist["close"].iloc[-1] or 0.0)
        ret_5 = _window_return_pct(hist["close"], 5)
        ret_20 = _window_return_pct(hist["close"], 20)
        flow_sub = flow_by_code.get(code_s)
        net5 = float(flow_sub.tail(5)["net_mf_amount"].sum()) if flow_sub is not None and not flow_sub.empty else math.nan
        amount5 = float(hist.tail(5)["amount"].sum()) if not hist.empty else math.nan
        b = basic_by_code.get(code_s, {})
        m = member_by_code.get(code_s, {})
        rows.append({
            "ts_code": code_s,
            "as_of_date": as_of,
            "name": m.get("name"),
            "l1_code": m.get("l1_code"),
            "l1_name": m.get("l1_name"),
            "l2_code": m.get("l2_code"),
            "l2_name": m.get("l2_name"),
            "regime": regime,
            "entry_close": close,
            "ret_5d_pct": ret_5,
            "ret_20d_pct": ret_20,
            "volatility_20d_pct": float(hist["pct_chg"].std()) if len(hist) >= 5 else math.nan,
            "avg_amount_20d": float(hist["amount"].mean()) if len(hist) else math.nan,
            "moneyflow_net_5d": net5,
            "moneyflow_net_5d_pct_amount": float(net5 / amount5) if amount5 and not math.isnan(net5) else math.nan,
            "total_mv": _float_or_nan(b.get("total_mv")),
            "circ_mv": _float_or_nan(b.get("circ_mv")),
            "turnover_rate": _float_or_nan(b.get("turnover_rate")),
            **label,
        })
    seen = {r["ts_code"] for r in rows}
    for code in codes:
        if code not in seen and not any(f["ts_code"] == code for f in failures):
            failures.append({"ts_code": str(code), "as_of_date": as_of.isoformat(), "reason": "missing_daily_rows"})
    return rows, failures


def _stock_cycle_features(stock_orderflow: pd.DataFrame | None, as_of: dt.date) -> dict[str, Any]:
    if stock_orderflow is None or stock_orderflow.empty:
        return {
            "stock_main_net_5d_pct_amount": math.nan,
            "stock_retail_net_5d_pct_amount": math.nan,
            "stock_main_persistence_5d": math.nan,
            "stock_retail_chase_5d": math.nan,
        }
    hist = stock_orderflow[stock_orderflow["trade_date"] <= as_of].tail(5)
    if hist.empty:
        return {
            "stock_main_net_5d_pct_amount": math.nan,
            "stock_retail_net_5d_pct_amount": math.nan,
            "stock_main_persistence_5d": math.nan,
            "stock_retail_chase_5d": math.nan,
        }
    amount = float(pd.to_numeric(hist.get("amount_yuan"), errors="coerce").sum() or 0.0)
    main = pd.to_numeric(hist.get("main_net_yuan"), errors="coerce")
    retail = pd.to_numeric(hist.get("retail_net_yuan"), errors="coerce")
    return {
        "stock_main_net_5d_pct_amount": float(main.sum() / amount) if amount else math.nan,
        "stock_retail_net_5d_pct_amount": float(retail.sum() / amount) if amount else math.nan,
        "stock_main_persistence_5d": float((main > 0).mean()) if main.notna().any() else math.nan,
        "stock_retail_chase_5d": float((retail > 0).mean()) if retail.notna().any() else math.nan,
    }


def _sector_cycle_features(sector_flow: pd.DataFrame | None, as_of: dt.date) -> dict[str, Any]:
    empty = {
        "sector_main_net_5d_pct_amount": math.nan,
        "sector_retail_net_5d_pct_amount": math.nan,
        "sector_main_persistence_5d": math.nan,
        "sector_retail_chase_5d": math.nan,
        "sector_return_5d": math.nan,
        "sector_diffusion_score": math.nan,
        "sector_price_positive_breadth": math.nan,
        "sector_top5_main_net_share": math.nan,
        "sector_state": None,
        "sector_state_score": math.nan,
        "sector_risk_flags": None,
        "sector_cycle_accumulation_score": math.nan,
        "leader_quality_score": math.nan,
        "theme_heat_score": math.nan,
    }
    if sector_flow is None or sector_flow.empty:
        return empty
    hist = sector_flow[sector_flow["trade_date"] <= as_of].tail(10)
    if hist.empty:
        return empty
    recent = hist.tail(5)
    latest = hist.iloc[-1].to_dict()
    amount = float(pd.to_numeric(recent.get("sector_amount_yuan"), errors="coerce").sum() or 0.0)
    main = pd.to_numeric(recent.get("main_net_yuan"), errors="coerce")
    retail = pd.to_numeric(recent.get("retail_net_yuan"), errors="coerce")
    ret = pd.to_numeric(recent.get("sector_return_amount_weight"), errors="coerce")
    main_ratio_5d = float(main.sum() / amount) if amount else math.nan
    retail_ratio_5d = float(retail.sum() / amount) if amount else math.nan
    main_persist = float((main > 0).mean()) if main.notna().any() else math.nan
    retail_chase = float((retail > 0).mean()) if retail.notna().any() else math.nan
    diffusion = _float_or_nan(latest.get("diffusion_score"))
    price_breadth = _float_or_nan(latest.get("price_positive_breadth"))
    top5_share = _float_or_nan(latest.get("top5_main_net_share"))
    sector_return = float(ret.sum()) if ret.notna().any() else math.nan
    risk_flags = latest.get("risk_flags_json")
    risk_text = json.dumps(risk_flags, ensure_ascii=False, default=str) if risk_flags is not None else None
    anti_retail = 1.0 - min(max(retail_chase if not math.isnan(retail_chase) else 0.5, 0.0), 1.0)
    accumulation = _weighted_mean([
        (main_persist, 0.34),
        (_signed_ratio_to_unit(main_ratio_5d), 0.30),
        (anti_retail, 0.18),
        (diffusion, 0.10),
        (price_breadth, 0.08),
    ])
    leader_quality = _weighted_mean([
        (_signed_ratio_to_unit(_float_or_nan(latest.get("leader_main_net_yuan")) / amount if amount else math.nan), 0.40),
        (main_persist, 0.24),
        (price_breadth, 0.18),
        (diffusion, 0.18),
    ])
    return {
        "sector_main_net_5d_pct_amount": main_ratio_5d,
        "sector_retail_net_5d_pct_amount": retail_ratio_5d,
        "sector_main_persistence_5d": main_persist,
        "sector_retail_chase_5d": retail_chase,
        "sector_return_5d": sector_return,
        "sector_diffusion_score": diffusion,
        "sector_price_positive_breadth": price_breadth,
        "sector_top5_main_net_share": top5_share,
        "sector_state": latest.get("current_state"),
        "sector_state_score": _float_or_nan(latest.get("state_score")),
        "sector_risk_flags": risk_text,
        "sector_cycle_accumulation_score": accumulation,
        "leader_quality_score": leader_quality,
        "theme_heat_score": math.nan,
    }


def _signed_ratio_to_unit(value: float) -> float:
    if value != value:
        return math.nan
    return max(0.0, min(1.0, 0.5 + float(value) * 5.0))


def _weighted_mean(items: Sequence[tuple[float, float]]) -> float:
    valid = [(float(v), float(w)) for v, w in items if v == v and w > 0]
    if not valid:
        return math.nan
    total_w = sum(w for _, w in valid)
    return sum(v * w for v, w in valid) / total_w


def _forward_labels_from_daily_frame(df: pd.DataFrame, as_of: dt.date) -> dict[str, Any] | None:
    sub = df[df["trade_date"] >= as_of].reset_index(drop=True)
    if sub.empty or sub["trade_date"].iloc[0] != as_of:
        return None
    entry = float(sub["close"].iloc[0] or 0.0)
    if entry <= 0:
        return None
    out: dict[str, Any] = {"forward_available_days": int(len(sub) - 1)}
    for h in HORIZONS:
        future = sub.iloc[1 : 1 + h]
        if len(future) < h:
            out[f"forward_{h}d_return"] = math.nan
            out[f"forward_{h}d_target_first"] = None
            out[f"forward_{h}d_stop_first"] = None
            out[f"forward_{h}d_max_drawdown"] = math.nan
            out[f"forward_{h}d_mfe"] = math.nan
            continue
        target_pct = {5: 0.05, 10: 0.08, 20: 0.20}[h]
        target = entry * (1 + target_pct)
        stop = entry * 0.92
        first_event: str | None = None
        for rec in future.to_dict(orient="records"):
            hi = float(rec.get("high") or 0.0)
            lo = float(rec.get("low") or 0.0)
            if hi >= target and lo <= stop:
                first_event = "stop"
                break
            if lo <= stop:
                first_event = "stop"
                break
            if hi >= target:
                first_event = "target"
                break
        out[f"forward_{h}d_return"] = round((float(future["close"].iloc[-1]) / entry - 1) * 100.0, 4)
        out[f"forward_{h}d_target_first"] = first_event == "target"
        out[f"forward_{h}d_stop_first"] = first_event == "stop"
        out[f"forward_{h}d_max_drawdown"] = round((float(future["low"].min()) / entry - 1) * 100.0, 4)
        out[f"forward_{h}d_mfe"] = round((float(future["high"].max()) / entry - 1) * 100.0, 4)
    return out


def _window_return_pct(close: pd.Series, window: int) -> float:
    values = close.dropna().astype(float)
    if len(values) < 2:
        return math.nan
    start = values.iloc[-min(window, len(values))]
    end = values.iloc[-1]
    if start <= 0:
        return math.nan
    return float((end / start - 1) * 100.0)


def _cheap_composite_score(df: pd.DataFrame) -> pd.Series:
    parts = []
    for col, sign in [("ret_5d_pct", 1.0), ("ret_20d_pct", 0.5), ("volatility_20d_pct", -0.25), ("moneyflow_net_5d_pct_amount", 0.5)]:
        if col not in df:
            continue
        s = df[col].astype(float)
        ranked = s.rank(pct=True)
        parts.append(ranked * sign)
    if not parts:
        return pd.Series(np.nan, index=df.index)
    return sum(parts) / len(parts)


def _rank_ic(x: pd.Series, y: pd.Series) -> float:
    if len(x) < 30 or len(y) < 30:
        return 0.0
    xr = x.rank(method="average")
    yr = y.rank(method="average")
    if xr.std() <= 1e-12 or yr.std() <= 1e-12:
        return 0.0
    return float(xr.corr(yr))


def _float_or_nan(value: Any) -> float:
    try:
        if value is None:
            return math.nan
        return float(value)
    except (TypeError, ValueError):
        return math.nan
