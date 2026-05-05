"""Single-stock historical analog replay for Stock Edge."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class HistoricalReplayStats:
    available: bool
    reason: str
    analog_count: int
    best_key: str | None
    best_label: str | None
    best_probability: float | None
    best_expected_value: float | None
    target_stats: list[dict[str, Any]]
    avg_similarity: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_historical_replay_stats(
    daily_bars: pd.DataFrame,
    *,
    replay_params: dict[str, Any],
    risk_params: dict[str, Any],
) -> HistoricalReplayStats:
    """Find similar historical states and replay their realized forward paths."""
    if not replay_params.get("enabled", True):
        return _missing("historical replay disabled")
    df = _feature_frame(daily_bars)
    min_rows = int(replay_params.get("min_history_rows", 120))
    if len(df) < min_rows:
        return _missing(f"历史样本 {len(df)} 根，低于 replay 底线 {min_rows} 根。")

    targets = _targets(replay_params)
    max_horizon = max((int(t["horizon_days"]) for t in targets), default=40)
    latest = df.iloc[-1]
    candidates = df.iloc[60 : max(60, len(df) - max_horizon)].copy()
    if candidates.empty:
        return _missing("没有足够已实现 forward path 的历史候选。")

    candidates["distance"] = candidates.apply(lambda row: _distance(row, latest, replay_params), axis=1)
    candidates = candidates.sort_values("distance").head(int(replay_params.get("candidate_pool", 96)))
    analogs = candidates.head(int(replay_params.get("max_analogs", 24)))
    min_analogs = int(replay_params.get("min_analogs", 8))
    if len(analogs) < min_analogs:
        return _missing(f"相似样本只有 {len(analogs)} 个，低于 {min_analogs} 个。")

    stop_distance = float(replay_params.get("stop_distance_pct", risk_params.get("max_stop_distance_pct", 12.0))) / 100.0
    stats = [_target_stats(df, analogs, target, stop_distance) for target in targets]
    stats.sort(key=lambda item: (item["expected_value"], item["hit_rate"], -item["stop_first_rate"]), reverse=True)
    best = stats[0] if stats else None
    avg_similarity = float((1.0 / (1.0 + analogs["distance"])).mean()) if not analogs.empty else None
    return HistoricalReplayStats(
        available=True,
        reason="已完成单股历史相似形态 replay。",
        analog_count=len(analogs),
        best_key=best.get("key") if best else None,
        best_label=best.get("label") if best else None,
        best_probability=best.get("hit_rate") if best else None,
        best_expected_value=best.get("expected_value") if best else None,
        target_stats=stats,
        avg_similarity=round(avg_similarity, 4) if avg_similarity is not None else None,
    )


def _feature_frame(daily_bars: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "open", "high", "low", "close"}
    if daily_bars.empty or not required.issubset(daily_bars.columns):
        return pd.DataFrame()
    df = daily_bars.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df = df.sort_values("trade_date").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    df["ret_5"] = df["close"].pct_change(5).fillna(0.0)
    df["ret_20"] = df["close"].pct_change(20).fillna(0.0)
    high_60 = df["high"].rolling(60, min_periods=20).max()
    low_60 = df["low"].rolling(60, min_periods=20).min()
    width = (high_60 - low_60).replace(0, pd.NA)
    df["range_pos"] = ((df["close"] - low_60) / width).fillna(0.5)
    df["drawdown_20"] = (df["close"] / df["close"].rolling(20, min_periods=10).max() - 1.0).fillna(0.0)
    amount = pd.to_numeric(df.get("amount", pd.Series([0.0] * len(df))), errors="coerce").fillna(0.0)
    df["amount_ratio"] = (amount / amount.rolling(20, min_periods=5).mean().replace(0, pd.NA)).fillna(1.0)
    return df


def _targets(replay_params: dict[str, Any]) -> list[dict[str, Any]]:
    raw = replay_params.get("targets") or {}
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
    if out:
        return out
    return [
        {"key": "tactical_15d_20", "label": "15日+20%", "horizon_days": 15, "return_pct": 20.0},
        {"key": "swing_25d_30", "label": "25日+30%", "horizon_days": 25, "return_pct": 30.0},
        {"key": "right_tail_40d_50", "label": "40日+50%", "horizon_days": 40, "return_pct": 50.0},
    ]


def _target_stats(df: pd.DataFrame, analogs: pd.DataFrame, target: dict[str, Any], stop_distance: float) -> dict[str, Any]:
    horizon = int(target["horizon_days"])
    return_pct = float(target["return_pct"]) / 100.0
    hits = 0
    stops = 0
    realized_returns: list[float] = []
    for idx in analogs.index:
        entry = float(df.loc[idx, "close"])
        future = df.iloc[idx + 1 : idx + horizon + 1]
        if future.empty or entry <= 0:
            continue
        target_price = entry * (1.0 + return_pct)
        stop_price = entry * (1.0 - stop_distance)
        hit_target = bool((future["high"] >= target_price).any())
        hit_stop = bool((future["low"] <= stop_price).any())
        first = _first_event(future, target_price=target_price, stop_price=stop_price)
        hits += int(hit_target)
        stops += int(first == "stop" or (hit_stop and not hit_target))
        realized_returns.append(float(future.iloc[-1]["close"]) / entry - 1.0)
    n = max(1, len(realized_returns))
    hit_rate = hits / n
    stop_rate = stops / n
    avg_return = sum(realized_returns) / n
    expected_value = hit_rate * return_pct - stop_rate * stop_distance
    return {
        "key": target["key"],
        "label": target["label"],
        "horizon_days": horizon,
        "return_pct": round(return_pct * 100.0, 2),
        "hit_rate": round(hit_rate, 4),
        "stop_first_rate": round(stop_rate, 4),
        "avg_return": round(avg_return, 4),
        "expected_value": round(expected_value, 4),
        "sample_count": len(realized_returns),
    }


def _first_event(future: pd.DataFrame, *, target_price: float, stop_price: float) -> str | None:
    for _, row in future.iterrows():
        hit_stop = float(row["low"]) <= stop_price
        hit_target = float(row["high"]) >= target_price
        if hit_stop and hit_target:
            return "stop"
        if hit_stop:
            return "stop"
        if hit_target:
            return "target"
    return None


def _distance(row: pd.Series, latest: pd.Series, replay_params: dict[str, Any]) -> float:
    features = replay_params.get("features") or {}
    acc = 0.0
    weight_sum = 0.0
    for key, spec in features.items():
        if key not in row or key not in latest:
            continue
        weight = float((spec or {}).get("weight", 1.0))
        scale = max(float((spec or {}).get("scale", 1.0)), 1e-6)
        diff = (float(row[key]) - float(latest[key])) / scale
        acc += weight * diff * diff
        weight_sum += weight
    if weight_sum <= 0:
        return 999.0
    scale = max(float(replay_params.get("similarity_scale", 2.5)), 1e-6)
    return math.sqrt(acc / weight_sum) * scale


def _label_for_key(key: str) -> str:
    return {
        "tactical_15d_20": "15日+20%",
        "swing_25d_30": "25日+30%",
        "right_tail_40d_50": "40日+50%",
    }.get(key, key)


def _missing(reason: str) -> HistoricalReplayStats:
    return HistoricalReplayStats(
        available=False,
        reason=reason,
        analog_count=0,
        best_key=None,
        best_label=None,
        best_probability=None,
        best_expected_value=None,
        target_stats=[],
        avg_similarity=None,
    )
