"""Historical entry-fill replay for Stock Edge."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class EntryFillReplayStats:
    available: bool
    reason: str
    sample_count: int
    fill_rate: float | None
    clean_fill_rate: float | None
    stop_before_fill_rate: float | None
    avg_days_to_fill: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_entry_fill_replay_stats(daily_bars: pd.DataFrame, *, params: dict[str, Any]) -> EntryFillReplayStats:
    """Replay whether a support/ATR entry zone historically got filled."""
    if not params.get("enabled", True):
        return _missing("entry-fill replay disabled")
    df = _prepare(daily_bars)
    min_rows = int(params.get("min_history_rows", 120))
    if len(df) < min_rows:
        return _missing(f"历史样本 {len(df)} 根，低于 entry-fill replay 底线 {min_rows} 根。")

    horizon = int(params.get("horizon_days", 5))
    support_window = int(params.get("support_window", 20))
    atr_window = int(params.get("atr_window", 14))
    support_mult = float(params.get("entry_low_support_multiplier", 0.995))
    entry_high_atr = float(params.get("entry_high_atr_multiplier", 0.35))
    stop_atr = float(params.get("stop_atr_multiplier", 0.80))
    df["support"] = df["low"].rolling(support_window, min_periods=max(5, support_window // 2)).min()
    df["atr"] = _atr(df, atr_window)

    samples = 0
    fills = 0
    clean_fills = 0
    stop_before_fill = 0
    days_to_fill: list[int] = []
    start = max(support_window, atr_window) + 1
    end = len(df) - horizon
    for idx in range(start, end):
        support = float(df.iloc[idx]["support"])
        atr = float(df.iloc[idx]["atr"])
        if support <= 0 or atr <= 0:
            continue
        entry_low = support * support_mult
        entry_high = min(float(df.iloc[idx]["close"]), support + entry_high_atr * atr)
        if entry_high < entry_low:
            entry_high = entry_low * 1.01
        stop = max(0.01, support - stop_atr * atr)
        future = df.iloc[idx + 1 : idx + horizon + 1]
        if future.empty:
            continue
        samples += 1
        first = _first_event(future, entry_low=entry_low, entry_high=entry_high, stop=stop)
        if first and first[0] == "fill":
            fills += 1
            clean_fills += 1
            days_to_fill.append(first[1])
        elif first and first[0] == "stop":
            stop_before_fill += 1
        else:
            hit_later = _touches_entry(future, entry_low=entry_low, entry_high=entry_high)
            if hit_later:
                fills += 1

    min_samples = int(params.get("min_samples", 20))
    if samples < min_samples:
        return _missing(f"entry-fill replay 有效样本 {samples} 个，低于 {min_samples} 个。")
    fill_rate = fills / samples
    clean_fill_rate = clean_fills / samples
    stop_rate = stop_before_fill / samples
    avg_days = sum(days_to_fill) / len(days_to_fill) if days_to_fill else None
    return EntryFillReplayStats(
        available=True,
        reason="已完成支撑/ATR 入场区间历史成交 replay。",
        sample_count=samples,
        fill_rate=round(fill_rate, 4),
        clean_fill_rate=round(clean_fill_rate, 4),
        stop_before_fill_rate=round(stop_rate, 4),
        avg_days_to_fill=round(avg_days, 2) if avg_days is not None else None,
    )


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


def _atr(df: pd.DataFrame, window: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window, min_periods=max(5, window // 2)).mean().fillna(0.0)


def _first_event(future: pd.DataFrame, *, entry_low: float, entry_high: float, stop: float) -> tuple[str, int] | None:
    for day, (_, row) in enumerate(future.iterrows(), start=1):
        low = float(row["low"])
        high = float(row["high"])
        if low <= stop and high < entry_low:
            return ("stop", day)
        if low <= entry_high and high >= entry_low:
            return ("fill", day)
        if low <= stop:
            return ("stop", day)
    return None


def _touches_entry(future: pd.DataFrame, *, entry_low: float, entry_high: float) -> bool:
    return bool(((future["low"] <= entry_high) & (future["high"] >= entry_low)).any())


def _missing(reason: str) -> EntryFillReplayStats:
    return EntryFillReplayStats(
        available=False,
        reason=reason,
        sample_count=0,
        fill_rate=None,
        clean_fill_rate=None,
        stop_before_fill_rate=None,
        avg_days_to_fill=None,
    )
