"""Historical path forecaster for Stock Edge.

This module turns a stock's own PIT daily history into executable path
statistics: return quantiles, right-tail touch probability, conformal-style
uncertainty bands, and stop-first risk. It is intentionally model-light for the
first production path, but the outputs match the later ML label contract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PathForecastProfile:
    available: bool
    reason: str
    sample_count: int
    best_key: str | None
    best_label: str | None
    horizon_days: int | None
    return_pct: float | None
    p10_return: float | None
    p50_return: float | None
    p90_return: float | None
    expected_return: float | None
    right_tail_probability: float | None
    stop_first_probability: float | None
    avg_max_drawdown: float | None
    conformal_width: float | None
    rows: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_path_forecast_profile(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> PathForecastProfile:
    if not params.get("enabled", True):
        return _missing("path forecast disabled")
    df = _prepare(daily_bars)
    min_rows = int(params.get("min_history_rows", 140))
    if len(df) < min_rows:
        return _missing(f"历史样本 {len(df)} 根，低于 path forecast 底线 {min_rows} 根。")

    targets = _targets(params)
    max_horizon = max((int(row["horizon_days"]) for row in targets), default=40)
    start = int(params.get("warmup_rows", 60))
    end = len(df) - max_horizon
    if end <= start:
        return _missing("没有足够 forward path 用于收益分位预测。")

    stop_distance = float(params.get("stop_distance_pct", risk_params.get("max_stop_distance_pct", 12.0))) / 100.0
    halflife = max(float(params.get("recency_halflife_rows", 180.0)), 1.0)
    rows = [
        _target_distribution(df, target, start=start, end=end, stop_distance=stop_distance, recency_halflife=halflife)
        for target in targets
    ]
    rows = [row for row in rows if row["sample_count"] > 0]
    min_samples = int(params.get("min_samples", 40))
    if not rows or max(row["sample_count"] for row in rows) < min_samples:
        n = max((row["sample_count"] for row in rows), default=0)
        return _missing(f"path forecast 有效样本 {n} 个，低于 {min_samples} 个。")
    rows.sort(key=lambda row: (row["expected_value"], row["p50_return"], row["right_tail_probability"]), reverse=True)
    best = rows[0]
    return PathForecastProfile(
        available=True,
        reason="已完成历史路径收益分位/风险分布预测。",
        sample_count=int(best["sample_count"]),
        best_key=str(best["key"]),
        best_label=str(best["label"]),
        horizon_days=int(best["horizon_days"]),
        return_pct=float(best["return_pct"]),
        p10_return=float(best["p10_return"]),
        p50_return=float(best["p50_return"]),
        p90_return=float(best["p90_return"]),
        expected_return=float(best["expected_return"]),
        right_tail_probability=float(best["right_tail_probability"]),
        stop_first_probability=float(best["stop_first_probability"]),
        avg_max_drawdown=float(best["avg_max_drawdown"]),
        conformal_width=float(best["conformal_width"]),
        rows=rows,
    )


def _target_distribution(
    df: pd.DataFrame,
    target: dict[str, Any],
    *,
    start: int,
    end: int,
    stop_distance: float,
    recency_halflife: float,
) -> dict[str, Any]:
    horizon = int(target["horizon_days"])
    target_return = float(target["return_pct"]) / 100.0
    returns: list[float] = []
    max_gains: list[float] = []
    max_drawdowns: list[float] = []
    weights: list[float] = []
    stop_weight = 0.0
    target_weight = 0.0
    total_weight = 0.0
    for idx in range(start, end):
        entry = float(df.iloc[idx]["close"])
        if entry <= 0:
            continue
        future = df.iloc[idx + 1 : idx + horizon + 1]
        if future.empty:
            continue
        weight = 0.5 ** ((end - idx) / recency_halflife)
        final_return = float(future.iloc[-1]["close"]) / entry - 1.0
        max_gain = float(future["high"].max()) / entry - 1.0
        max_dd = max(0.0, 1.0 - float(future["low"].min()) / entry)
        event = _first_event(future, target_price=entry * (1.0 + target_return), stop_price=entry * (1.0 - stop_distance))
        returns.append(final_return)
        max_gains.append(max_gain)
        max_drawdowns.append(max_dd)
        weights.append(weight)
        total_weight += weight
        target_weight += weight if max_gain >= target_return else 0.0
        stop_weight += weight if event == "stop" else 0.0

    p10 = _weighted_quantile(returns, weights, 0.10)
    p50 = _weighted_quantile(returns, weights, 0.50)
    p90 = _weighted_quantile(returns, weights, 0.90)
    expected = _weighted_mean(returns, weights)
    avg_dd = _weighted_mean(max_drawdowns, weights)
    hit = target_weight / max(total_weight, 1e-9)
    stop = stop_weight / max(total_weight, 1e-9)
    conformal_width = max(0.0, p90 - p10)
    expected_value = hit * target_return + 0.35 * expected - stop * stop_distance - 0.20 * avg_dd
    return {
        "key": target["key"],
        "label": target["label"],
        "horizon_days": horizon,
        "return_pct": round(target_return * 100.0, 2),
        "p10_return": round(p10, 4),
        "p50_return": round(p50, 4),
        "p90_return": round(p90, 4),
        "expected_return": round(expected, 4),
        "right_tail_probability": round(hit, 4),
        "stop_first_probability": round(stop, 4),
        "avg_max_drawdown": round(avg_dd, 4),
        "conformal_width": round(conformal_width, 4),
        "expected_value": round(expected_value, 4),
        "sample_count": len(returns),
    }


def _prepare(daily_bars: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "open", "high", "low", "close"}
    if daily_bars.empty or not required.issubset(daily_bars.columns):
        return pd.DataFrame()
    df = daily_bars.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df = df.sort_values("trade_date").reset_index(drop=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def _targets(params: dict[str, Any]) -> list[dict[str, Any]]:
    raw = params.get("targets") or {}
    out: list[dict[str, Any]] = []
    for key, spec in raw.items():
        if isinstance(spec, dict):
            out.append(
                {
                    "key": str(key),
                    "label": _label_for_key(str(key)),
                    "horizon_days": int(spec.get("horizon_days", 40)),
                    "return_pct": float(spec.get("return_pct", 50.0)),
                }
            )
    return out or [
        {"key": "tactical_15d_20", "label": "15日+20%", "horizon_days": 15, "return_pct": 20.0},
        {"key": "swing_25d_30", "label": "25日+30%", "horizon_days": 25, "return_pct": 30.0},
        {"key": "right_tail_40d_50", "label": "40日+50%", "horizon_days": 40, "return_pct": 50.0},
    ]


def _first_event(future: pd.DataFrame, *, target_price: float, stop_price: float) -> str | None:
    for _, row in future.iterrows():
        hit_stop = float(row["low"]) <= stop_price
        hit_target = float(row["high"]) >= target_price
        if hit_stop and hit_target:
            open_price = float(row.get("open", 0.0))
            return "target" if abs(target_price - open_price) < abs(open_price - stop_price) else "stop"
        if hit_stop:
            return "stop"
        if hit_target:
            return "target"
    return None


def _weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    if not values:
        return 0.0
    pairs = sorted(zip(values, weights, strict=False), key=lambda row: row[0])
    total = sum(max(weight, 0.0) for _, weight in pairs)
    if total <= 0:
        return float(pairs[min(max(int(q * (len(pairs) - 1)), 0), len(pairs) - 1)][0])
    acc = 0.0
    for value, weight in pairs:
        acc += max(weight, 0.0)
        if acc / total >= q:
            return float(value)
    return float(pairs[-1][0])


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    total = sum(max(weight, 0.0) for weight in weights)
    if total <= 0:
        return sum(values) / len(values)
    return sum(value * max(weight, 0.0) for value, weight in zip(values, weights, strict=False)) / total


def _label_for_key(key: str) -> str:
    return {
        "tactical_15d_20": "15日+20%",
        "swing_25d_30": "25日+30%",
        "right_tail_40d_50": "40日+50%",
    }.get(key, key)


def _missing(reason: str) -> PathForecastProfile:
    return PathForecastProfile(
        available=False,
        reason=reason,
        sample_count=0,
        best_key=None,
        best_label=None,
        horizon_days=None,
        return_pct=None,
        p10_return=None,
        p50_return=None,
        p90_return=None,
        expected_return=None,
        right_tail_probability=None,
        stop_first_probability=None,
        avg_max_drawdown=None,
        conformal_width=None,
        rows=[],
    )
