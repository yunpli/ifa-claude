"""Target/stop first-event replay for Stock Edge."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class TargetStopReplayStats:
    available: bool
    reason: str
    sample_count: int
    best_key: str | None
    best_label: str | None
    target_stats: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_target_stop_replay_stats(
    daily_bars: pd.DataFrame,
    *,
    params: dict[str, Any],
    risk_params: dict[str, Any],
) -> TargetStopReplayStats:
    """Replay whether target or stop would have fired first for each opportunity."""
    if not params.get("enabled", True):
        return _missing("target/stop replay disabled")
    df = _prepare(daily_bars)
    min_rows = int(params.get("min_history_rows", 120))
    if len(df) < min_rows:
        return _missing(f"历史样本 {len(df)} 根，低于 target/stop replay 底线 {min_rows} 根。")
    targets = _targets(params)
    max_horizon = max((int(row["horizon_days"]) for row in targets), default=40)
    start = int(params.get("warmup_rows", 60))
    end = len(df) - max_horizon
    if end <= start:
        return _missing("没有足够 forward path 用于目标/止损先触发 replay。")

    stop_distance = float(params.get("stop_distance_pct", risk_params.get("max_stop_distance_pct", 12.0))) / 100.0
    recency_halflife = float(params.get("recency_halflife_rows", 180.0))
    stats = [
        _target_path_stats(
            df,
            target,
            start=start,
            end=end,
            stop_distance=stop_distance,
            recency_halflife=max(recency_halflife, 1.0),
        )
        for target in targets
    ]
    stats = [row for row in stats if row["sample_count"] > 0]
    min_samples = int(params.get("min_samples", 40))
    if not stats or max(row["sample_count"] for row in stats) < min_samples:
        n = max((row["sample_count"] for row in stats), default=0)
        return _missing(f"目标/止损 replay 有效样本 {n} 个，低于 {min_samples} 个。")
    stats.sort(key=lambda row: (row["expected_value"], row["target_first_rate"], -row["stop_first_rate"]), reverse=True)
    best = stats[0]
    return TargetStopReplayStats(
        available=True,
        reason="已完成目标/止损先触发路径 replay。",
        sample_count=max(row["sample_count"] for row in stats),
        best_key=best["key"],
        best_label=best["label"],
        target_stats=stats,
    )


def _target_path_stats(
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
    target_weight = 0.0
    stop_weight = 0.0
    neither_weight = 0.0
    weighted_return = 0.0
    weighted_days_to_target = 0.0
    weighted_days_to_stop = 0.0
    target_days_weight = 0.0
    stop_days_weight = 0.0
    total_weight = 0.0
    samples = 0
    for idx in range(start, end):
        entry = float(df.iloc[idx]["close"])
        if entry <= 0:
            continue
        future = df.iloc[idx + 1 : idx + horizon + 1]
        if future.empty:
            continue
        weight = 0.5 ** ((end - idx) / recency_halflife)
        target_price = entry * (1.0 + target_return)
        stop_price = entry * (1.0 - stop_distance)
        event, event_day = _first_event(future, target_price=target_price, stop_price=stop_price)
        realized = float(future.iloc[-1]["close"]) / entry - 1.0
        samples += 1
        total_weight += weight
        weighted_return += weight * realized
        if event == "target":
            target_weight += weight
            weighted_days_to_target += weight * float(event_day or horizon)
            target_days_weight += weight
        elif event == "stop":
            stop_weight += weight
            weighted_days_to_stop += weight * float(event_day or horizon)
            stop_days_weight += weight
        else:
            neither_weight += weight

    denom = max(total_weight, 1e-9)
    target_rate = target_weight / denom
    stop_rate = stop_weight / denom
    neither_rate = neither_weight / denom
    avg_return = weighted_return / denom
    expected_value = target_rate * target_return - stop_rate * stop_distance
    return {
        "key": target["key"],
        "label": target["label"],
        "horizon_days": horizon,
        "return_pct": round(target_return * 100.0, 2),
        "target_first_rate": round(target_rate, 4),
        "stop_first_rate": round(stop_rate, 4),
        "neither_rate": round(neither_rate, 4),
        "avg_return": round(avg_return, 4),
        "expected_value": round(expected_value, 4),
        "avg_days_to_target": round(weighted_days_to_target / target_days_weight, 2) if target_days_weight > 0 else None,
        "avg_days_to_stop": round(weighted_days_to_stop / stop_days_weight, 2) if stop_days_weight > 0 else None,
        "sample_count": samples,
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


def _first_event(future: pd.DataFrame, *, target_price: float, stop_price: float) -> tuple[str | None, int | None]:
    for day, (_, row) in enumerate(future.iterrows(), start=1):
        hit_stop = float(row["low"]) <= stop_price
        hit_target = float(row["high"]) >= target_price
        if hit_stop and hit_target:
            return ("stop", day)
        if hit_stop:
            return ("stop", day)
        if hit_target:
            return ("target", day)
    return (None, None)


def _label_for_key(key: str) -> str:
    return {
        "tactical_15d_20": "15日+20%",
        "swing_25d_30": "25日+30%",
        "right_tail_40d_50": "40日+50%",
    }.get(key, key)


def _missing(reason: str) -> TargetStopReplayStats:
    return TargetStopReplayStats(
        available=False,
        reason=reason,
        sample_count=0,
        best_key=None,
        best_label=None,
        target_stats=[],
    )
