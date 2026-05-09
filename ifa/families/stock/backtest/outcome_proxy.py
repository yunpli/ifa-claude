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
    feature_version: str = "outcome_proxy_v1"
    universe_selection: dict[str, Any] = field(default_factory=dict)
    failure_details: list[dict[str, Any]] = field(default_factory=list)

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
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for as_of, codes in chunks:
        if not codes:
            continue
        date_rows, date_failures = _build_proxy_rows_for_date(engine, as_of, codes)
        rows.extend(date_rows)
        failures.extend(date_failures)

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
    )
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return df, manifest


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

    features = _proxy_candidate_features(df)
    families: dict[str, pd.Series] = {
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
    }

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

    liquidity_rank = _group_rank(amount, date_key)
    size_rank = _group_rank(mv, date_key)
    vol_rank = _group_rank(vol, date_key)
    turnover_rank = _group_rank(turnover, date_key)
    flow_rank = _group_rank(flow.clip(lower=-0.12, upper=0.12), date_key)

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

    return {
        "moneyflow_quality": (0.70 * flow_rank + 0.30 * (1.0 - vol_rank)).clip(0.0, 1.0),
        "mid_liquidity": (1.0 - (liquidity_rank - 0.52).abs() / 0.52).clip(0.0, 1.0),
        "large_cap": size_rank.fillna(0.5).clip(0.0, 1.0),
        "industry_tilt_static": industry_tilt,
        "industry_tilt_dynamic": dynamic_tilt,
        "weak_industry_avoid": weak_avoid,
        "low_left_tail_risk": (0.65 * (1.0 - vol_rank) + 0.35 * (1.0 - turnover_rank)).clip(0.0, 1.0),
        "regime_gate": regime_gate,
        "industry_relative_flow": industry_flow_rank.fillna(flow_rank).clip(0.0, 1.0),
        "industry_relative_reversal": reversal.clip(0.0, 1.0),
        "regime_adjusted_momentum": regime_adjusted.clip(0.0, 1.0),
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


def _proxy_cache_path(universe_id: str, as_of_dates: Sequence[dt.date], membership_hash: str) -> Path:
    sorted_dates = sorted(as_of_dates)
    date_sig = f"{sorted_dates[0].isoformat()}_{sorted_dates[-1].isoformat()}_{len(sorted_dates)}"
    suffix = hashlib.sha256(f"{universe_id}|{date_sig}|{membership_hash}|outcome_proxy_v1".encode()).hexdigest()[:12]
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
    )


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
