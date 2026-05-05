"""Forward labels for Stock Edge replay and parameter tuning."""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class ForwardLabels:
    as_of_trade_date: dt.date
    entry_price: float
    horizons: dict[int, float | None]
    max_drawdown_40d_pct: float | None
    hit_50pct_40d: bool | None
    stop_first: bool | None
    first_event: str | None
    available_forward_days: int
    return_5d_pct: float | None = None
    positive_5d: bool | None = None
    target_first_5d: bool | None = None
    stop_first_5d: bool | None = None
    max_drawdown_5d_pct: float | None = None
    mfe_5d_pct: float | None = None
    mae_5d_pct: float | None = None
    entry_fill_5d: bool | None = None
    adverse_gap_next_open: bool | None = None
    slippage_bucket: str | None = None
    return_10d_pct: float | None = None
    positive_10d: bool | None = None
    target_first_10d: bool | None = None
    stop_first_10d: bool | None = None
    max_drawdown_10d_pct: float | None = None
    mfe_10d_pct: float | None = None
    mae_10d_pct: float | None = None
    moneyflow_persistence_10d: float | None = None
    sector_persistence_10d: float | None = None
    return_20d_pct: float | None = None
    positive_20d: bool | None = None
    target_first_20d: bool | None = None
    stop_first_20d: bool | None = None
    max_drawdown_20d_pct: float | None = None
    mfe_20d_pct: float | None = None
    mae_20d_pct: float | None = None
    position_loss_budget_hit: bool | None = None
    strategy_decay_bucket: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def compute_forward_labels(
    daily_bars: pd.DataFrame,
    *,
    as_of_trade_date: dt.date,
    entry_price: float | None = None,
    stop_price: float | None = None,
    target_return_pct: float = 50.0,
    target_return_by_horizon_pct: dict[int, float] | None = None,
    entry_zone_low: float | None = None,
    entry_zone_high: float | None = None,
    adverse_gap_threshold_pct: float = -3.0,
    position_loss_budget_pct: float = -8.0,
    horizons: Iterable[int] = (5, 10, 20, 40),
) -> ForwardLabels:
    """Compute forward labels from daily bars without looking before `as_of`.

    The caller supplies a PIT `as_of_trade_date`; this function only reads bars
    strictly after that date. It is intentionally pure so replay jobs can run in
    DuckDB/Pandas without touching report code.
    """
    required = {"trade_date", "close", "high", "low"}
    if daily_bars.empty or not required.issubset(daily_bars.columns):
        raise ValueError("daily_bars must include trade_date, close, high, low.")

    df = daily_bars.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df = df.sort_values("trade_date").reset_index(drop=True)
    as_of_rows = df[df["trade_date"] <= as_of_trade_date]
    if as_of_rows.empty and entry_price is None:
        raise ValueError("entry_price is required when no as_of bar exists.")
    entry = float(entry_price if entry_price is not None else as_of_rows.iloc[-1]["close"])
    if entry <= 0:
        raise ValueError("entry_price must be positive.")

    horizon_list = [int(h) for h in horizons]
    max_horizon = max(max(horizon_list, default=40), 40)
    future = df[df["trade_date"] > as_of_trade_date].head(max_horizon).reset_index(drop=True)
    horizon_returns: dict[int, float | None] = {}
    for horizon in horizon_list:
        if len(future) >= horizon:
            horizon_returns[int(horizon)] = round((float(future.iloc[horizon - 1]["close"]) / entry - 1.0) * 100.0, 4)
        else:
            horizon_returns[int(horizon)] = None

    first_40 = future.head(40)
    if first_40.empty:
        base = ForwardLabels(
            as_of_trade_date=as_of_trade_date,
            entry_price=entry,
            horizons=horizon_returns,
            max_drawdown_40d_pct=None,
            hit_50pct_40d=None,
            stop_first=None,
            first_event=None,
            available_forward_days=0,
        )
        return _attach_horizon_labels(base, future, entry, stop_price, target_return_by_horizon_pct or {}, entry_zone_low, entry_zone_high, adverse_gap_threshold_pct, position_loss_budget_pct)

    lows = pd.to_numeric(first_40["low"], errors="coerce")
    max_drawdown = round((float(lows.min()) / entry - 1.0) * 100.0, 4) if lows.notna().any() else None
    target_price = entry * (1.0 + target_return_pct / 100.0)
    hit_target = bool((pd.to_numeric(first_40["high"], errors="coerce") >= target_price).any())

    first_event = None
    stop_first = None
    if stop_price is not None:
        stop = float(stop_price)
        for _, row in first_40.iterrows():
            low = float(row["low"])
            high = float(row["high"])
            hit_stop_today = low <= stop
            hit_target_today = high >= target_price
            if hit_stop_today and hit_target_today:
                first_event = "ambiguous_same_day"
                stop_first = True
                break
            if hit_stop_today:
                first_event = "stop"
                stop_first = True
                break
            if hit_target_today:
                first_event = "target"
                stop_first = False
                break

    base = ForwardLabels(
        as_of_trade_date=as_of_trade_date,
        entry_price=entry,
        horizons=horizon_returns,
        max_drawdown_40d_pct=max_drawdown,
        hit_50pct_40d=hit_target,
        stop_first=stop_first,
        first_event=first_event,
        available_forward_days=len(first_40),
    )
    return _attach_horizon_labels(base, future, entry, stop_price, target_return_by_horizon_pct or {}, entry_zone_low, entry_zone_high, adverse_gap_threshold_pct, position_loss_budget_pct)


def _attach_horizon_labels(
    base: ForwardLabels,
    future: pd.DataFrame,
    entry: float,
    stop_price: float | None,
    target_by_horizon: dict[int, float],
    entry_zone_low: float | None,
    entry_zone_high: float | None,
    adverse_gap_threshold_pct: float,
    position_loss_budget_pct: float,
) -> ForwardLabels:
    values = asdict(base)
    for horizon in (5, 10, 20):
        metrics = _horizon_path_metrics(
            future,
            horizon=horizon,
            entry=entry,
            stop_price=stop_price,
            target_return_pct=float(target_by_horizon.get(horizon, {5: 8.0, 10: 12.0, 20: 20.0}[horizon])),
        )
        values[f"return_{horizon}d_pct"] = base.horizons.get(horizon)
        values[f"positive_{horizon}d"] = None if base.horizons.get(horizon) is None else bool(float(base.horizons[horizon] or 0.0) > 0.0)
        values[f"target_first_{horizon}d"] = metrics["target_first"]
        values[f"stop_first_{horizon}d"] = metrics["stop_first"]
        values[f"max_drawdown_{horizon}d_pct"] = metrics["max_drawdown_pct"]
        values[f"mfe_{horizon}d_pct"] = metrics["mfe_pct"]
        values[f"mae_{horizon}d_pct"] = metrics["mae_pct"]
    values["entry_fill_5d"] = _entry_fill_5d(future, entry, entry_zone_low, entry_zone_high)
    values["adverse_gap_next_open"] = _adverse_gap_next_open(future, entry, adverse_gap_threshold_pct)
    values["slippage_bucket"] = _slippage_bucket(future)
    values["position_loss_budget_hit"] = (
        None
        if values.get("mae_20d_pct") is None
        else bool(float(values["mae_20d_pct"]) <= float(position_loss_budget_pct))
    )
    values["strategy_decay_bucket"] = None
    values["moneyflow_persistence_10d"] = None
    values["sector_persistence_10d"] = None
    return ForwardLabels(**values)


def _horizon_path_metrics(
    future: pd.DataFrame,
    *,
    horizon: int,
    entry: float,
    stop_price: float | None,
    target_return_pct: float,
) -> dict[str, float | bool | None]:
    window = future.head(horizon)
    if len(window) < horizon:
        return {
            "target_first": None,
            "stop_first": None,
            "max_drawdown_pct": None,
            "mfe_pct": None,
            "mae_pct": None,
        }
    highs = pd.to_numeric(window["high"], errors="coerce")
    lows = pd.to_numeric(window["low"], errors="coerce")
    mfe = round((float(highs.max()) / entry - 1.0) * 100.0, 4) if highs.notna().any() else None
    mae = round((float(lows.min()) / entry - 1.0) * 100.0, 4) if lows.notna().any() else None
    target_price = entry * (1.0 + target_return_pct / 100.0)
    first_event = None
    if stop_price is not None:
        stop = float(stop_price)
        for _, row in window.iterrows():
            low = float(row["low"])
            high = float(row["high"])
            hit_stop_today = low <= stop
            hit_target_today = high >= target_price
            if hit_stop_today and hit_target_today:
                first_event = "ambiguous_same_day"
                break
            if hit_stop_today:
                first_event = "stop"
                break
            if hit_target_today:
                first_event = "target"
                break
    elif (pd.to_numeric(window["high"], errors="coerce") >= target_price).any():
        first_event = "target"
    return {
        "target_first": None if first_event is None else first_event == "target",
        "stop_first": None if first_event is None else first_event in {"stop", "ambiguous_same_day"},
        "max_drawdown_pct": mae,
        "mfe_pct": mfe,
        "mae_pct": mae,
    }


def _entry_fill_5d(
    future: pd.DataFrame,
    entry: float,
    entry_zone_low: float | None,
    entry_zone_high: float | None,
) -> bool | None:
    window = future.head(5)
    if window.empty:
        return None
    low_bound = float(entry_zone_low) if entry_zone_low is not None else entry
    high_bound = float(entry_zone_high) if entry_zone_high is not None else entry
    lows = pd.to_numeric(window["low"], errors="coerce")
    highs = pd.to_numeric(window["high"], errors="coerce")
    if not lows.notna().any() or not highs.notna().any():
        return None
    return bool(((lows <= high_bound) & (highs >= low_bound)).any())


def _adverse_gap_next_open(future: pd.DataFrame, entry: float, threshold_pct: float) -> bool | None:
    if future.empty or "open" not in future.columns:
        return None
    next_open = pd.to_numeric(future.iloc[:1]["open"], errors="coerce")
    if next_open.empty or pd.isna(next_open.iloc[0]):
        return None
    gap_pct = (float(next_open.iloc[0]) / entry - 1.0) * 100.0
    return bool(gap_pct <= float(threshold_pct))


def _slippage_bucket(future: pd.DataFrame) -> str:
    if future.empty:
        return "unknown"
    if "amount" not in future.columns:
        return "unknown"
    amounts = pd.to_numeric(future.head(5)["amount"], errors="coerce")
    if not amounts.notna().any():
        return "unknown"
    avg_amount = float(amounts.mean())
    if avg_amount >= 300000.0:
        return "low"
    if avg_amount >= 50000.0:
        return "medium"
    return "high"
