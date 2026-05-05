"""Simple auditable support/resistance levels for the first Stock Edge pass."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SupportResistanceLevel:
    price: float
    kind: str
    source: str
    strength: float
    distance_pct: float


def build_support_resistance(daily_bars: pd.DataFrame, *, max_levels: int = 8) -> list[SupportResistanceLevel]:
    if daily_bars.empty:
        return []
    df = daily_bars.sort_values("trade_date").reset_index(drop=True)
    close = float(df["close"].iloc[-1])
    candidates: list[SupportResistanceLevel] = []

    recent = df.tail(min(len(df), 20))
    _add_level(candidates, float(recent["low"].min()), "support", "20d_low", 0.80, close)
    _add_level(candidates, float(recent["high"].max()), "resistance", "20d_high", 0.80, close)

    if len(df) >= 5:
        swing = df.tail(min(len(df), 60))
        for idx in range(2, len(swing) - 2):
            row = swing.iloc[idx]
            lows = swing["low"].iloc[idx - 2: idx + 3]
            highs = swing["high"].iloc[idx - 2: idx + 3]
            if row["low"] == lows.min():
                _add_level(candidates, float(row["low"]), "support", "swing_low", 0.65, close)
            if row["high"] == highs.max():
                _add_level(candidates, float(row["high"]), "resistance", "swing_high", 0.65, close)

    for window, strength in [(20, 0.70), (60, 0.60)]:
        if len(df) >= window:
            ma = float(df["close"].tail(window).mean())
            kind = "support" if ma <= close else "resistance"
            _add_level(candidates, ma, kind, f"ma{window}", strength, close)

    return _dedupe_levels(candidates, max_levels=max_levels)


def nearest_support(levels: list[SupportResistanceLevel], close: float) -> SupportResistanceLevel | None:
    supports = [lvl for lvl in levels if lvl.kind == "support" and lvl.price <= close]
    if not supports:
        return None
    return max(supports, key=lambda lvl: lvl.price)


def nearest_resistance(levels: list[SupportResistanceLevel], close: float) -> SupportResistanceLevel | None:
    resistances = [lvl for lvl in levels if lvl.kind == "resistance" and lvl.price >= close]
    if not resistances:
        return None
    return min(resistances, key=lambda lvl: lvl.price)


def _add_level(
    levels: list[SupportResistanceLevel],
    price: float,
    kind: str,
    source: str,
    strength: float,
    close: float,
) -> None:
    if price <= 0 or close <= 0:
        return
    levels.append(
        SupportResistanceLevel(
            price=round(price, 4),
            kind=kind,
            source=source,
            strength=strength,
            distance_pct=round((price / close - 1.0) * 100.0, 4),
        )
    )


def _dedupe_levels(levels: list[SupportResistanceLevel], *, max_levels: int) -> list[SupportResistanceLevel]:
    ordered = sorted(levels, key=lambda lvl: (-lvl.strength, abs(lvl.distance_pct)))
    kept: list[SupportResistanceLevel] = []
    for lvl in ordered:
        if any(abs(lvl.price / old.price - 1.0) < 0.008 for old in kept):
            continue
        kept.append(lvl)
        if len(kept) >= max_levels:
            break
    return sorted(kept, key=lambda lvl: lvl.price)
