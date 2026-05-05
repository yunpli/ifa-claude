"""Financial-statement versus price dislocation model for Stock Edge."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FundamentalDislocationProfile:
    available: bool
    reason: str
    factor_count: int
    latest_annual_period: str | None
    latest_quarterly_period: str | None
    fundamental_strength: float | None
    price_extension: float | None
    peer_relative_return_15d: float | None
    dislocation_score: float
    score: float
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_fundamental_dislocation_profile(
    *,
    research_lineup: dict[str, Any] | None,
    daily_bars: pd.DataFrame | None,
    daily_basic: pd.DataFrame | None,
    sector_membership: dict[str, Any] | None,
    params: dict[str, Any],
) -> FundamentalDislocationProfile:
    if not params.get("enabled", True):
        return _missing("fundamental dislocation disabled")
    factors, annual_period, quarterly_period = _latest_factor_values(research_lineup or {})
    if not factors:
        return _missing("本地 Research 财报因子不足，无法计算财报-价格错配。")
    strength_parts: list[tuple[float | None, float]] = [
        (_scale_pct(factors.get("quarterly", {}).get("ROE"), center=8.0, width=14.0), 0.18),
        (_scale_pct(factors.get("annual", {}).get("ROE"), center=8.0, width=14.0), 0.14),
        (_scale_pct(factors.get("quarterly", {}).get("营收同比增速"), center=18.0, width=35.0), 0.18),
        (_scale_pct(factors.get("annual", {}).get("营收同比增速"), center=12.0, width=30.0), 0.12),
        (_scale_num(factors.get("quarterly", {}).get("CFO/NI"), center=0.8, width=1.6), 0.13),
        (_scale_num(factors.get("annual", {}).get("CFO/NI"), center=0.8, width=1.6), 0.10),
        (_scale_pct(factors.get("quarterly", {}).get("资产负债率"), center=55.0, width=25.0, invert=True), 0.08),
        (_scale_pct(factors.get("annual", {}).get("资产负债率"), center=55.0, width=25.0, invert=True), 0.07),
    ]
    fundamental_strength = _weighted_avg(strength_parts)
    if fundamental_strength is None:
        return _missing("Research 财报因子存在，但 ROE/成长/现金/负债字段不足。")
    price_context = _price_context(daily_bars, daily_basic, sector_membership)
    price_extension = price_context["price_extension"]
    peer_rel_15d = price_context["peer_relative_return_15d"]
    dislocation = _clip(fundamental_strength - 0.65 * price_extension, -1.0, 1.0)
    if peer_rel_15d is not None:
        dislocation += _clip(-peer_rel_15d / 35.0, -0.22, 0.22) * max(fundamental_strength, 0.0)
    dislocation = _clip(dislocation, -1.0, 1.0)
    score = _clip(0.44 * fundamental_strength + 0.42 * dislocation - 0.14 * max(price_extension, 0.0), -0.42, 0.42)
    return FundamentalDislocationProfile(
        available=True,
        reason="已完成财报质量、估值与价格反映程度的错配评分。",
        factor_count=sum(len(v) for v in factors.values()),
        latest_annual_period=annual_period,
        latest_quarterly_period=quarterly_period,
        fundamental_strength=round(fundamental_strength, 4),
        price_extension=round(price_extension, 4),
        peer_relative_return_15d=round(peer_rel_15d, 4) if peer_rel_15d is not None else None,
        dislocation_score=round(dislocation, 4),
        score=round(score, 4),
        evidence={
            "annual": factors.get("annual", {}),
            "quarterly": factors.get("quarterly", {}),
            **price_context,
        },
    )


def _latest_factor_values(lineup: dict[str, Any]) -> tuple[dict[str, dict[str, float]], str | None, str | None]:
    out: dict[str, dict[str, float]] = {"annual": {}, "quarterly": {}}
    periods: dict[str, str | None] = {"annual": None, "quarterly": None}
    for period_type, key in [("annual", "annual_factors"), ("quarterly", "quarterly_factors")]:
        rows = [row for row in lineup.get(key) or [] if row.get("period_type") == period_type or key.startswith(period_type)]
        if not rows:
            rows = list(lineup.get(key) or [])
        latest = max((str(row.get("period") or "") for row in rows), default="")
        periods[period_type] = latest or None
        for row in rows:
            if latest and str(row.get("period") or "") != latest:
                continue
            name = str(row.get("factor_name") or "")
            value = _float(row.get("value"))
            if name and value is not None:
                out[period_type][name] = value
    return out, periods["annual"], periods["quarterly"]


def _price_context(
    daily_bars: pd.DataFrame | None,
    daily_basic: pd.DataFrame | None,
    sector_membership: dict[str, Any] | None,
) -> dict[str, Any]:
    ret_20 = _return_pct(daily_bars, 20)
    ret_60 = _return_pct(daily_bars, 60)
    pe_ttm, pb = _latest_basic(daily_basic)
    valuation_extension = 0.0
    if pe_ttm is not None and pe_ttm > 0:
        valuation_extension += 0.50 * _sigmoid((pe_ttm - 70.0) / 25.0)
    if pb is not None and pb > 0:
        valuation_extension += 0.50 * _sigmoid((pb - 8.0) / 3.0)
    momentum_extension = 0.0
    if ret_20 is not None:
        momentum_extension += 0.55 * math.tanh(ret_20 / 24.0)
    if ret_60 is not None:
        momentum_extension += 0.45 * math.tanh(ret_60 / 40.0)
    peer_rel_15d = _peer_relative_15d(sector_membership)
    if peer_rel_15d is not None:
        momentum_extension += 0.22 * math.tanh(peer_rel_15d / 22.0)
    return {
        "return_20d_pct": ret_20,
        "return_60d_pct": ret_60,
        "pe_ttm": pe_ttm,
        "pb": pb,
        "peer_relative_return_15d": peer_rel_15d,
        "price_extension": _clip(0.62 * momentum_extension + 0.38 * valuation_extension, -1.0, 1.0),
    }


def _return_pct(daily: pd.DataFrame | None, window: int) -> float | None:
    if daily is None or daily.empty or "close" not in daily.columns or len(daily) <= window:
        return None
    close = pd.to_numeric(daily.sort_values("trade_date")["close"], errors="coerce").dropna()
    if len(close) <= window:
        return None
    base = float(close.iloc[-1 - window])
    latest = float(close.iloc[-1])
    if base <= 0:
        return None
    return (latest / base - 1.0) * 100.0


def _latest_basic(daily_basic: pd.DataFrame | None) -> tuple[float | None, float | None]:
    if daily_basic is None or daily_basic.empty:
        return None, None
    latest = daily_basic.sort_values("trade_date").iloc[-1] if "trade_date" in daily_basic.columns else daily_basic.iloc[-1]
    return _float(latest.get("pe_ttm")), _float(latest.get("pb"))


def _peer_relative_15d(sector_membership: dict[str, Any] | None) -> float | None:
    peers = (sector_membership or {}).get("sector_peers") or []
    target = next((row for row in peers if row.get("is_target")), None)
    target_ret = _float((target or {}).get("return_15d_pct"))
    peer_values = [_float(row.get("return_15d_pct")) for row in peers if not row.get("is_target")]
    peer_values = [v for v in peer_values if v is not None]
    if target_ret is None or len(peer_values) < 2:
        return None
    return target_ret - median(peer_values)


def _scale_pct(value: Any, *, center: float, width: float, invert: bool = False) -> float | None:
    v = _float(value)
    if v is None:
        return None
    score = math.tanh((v - center) / max(width, 1e-6))
    return -score if invert else score


def _scale_num(value: Any, *, center: float, width: float) -> float | None:
    v = _float(value)
    if v is None:
        return None
    return math.tanh((v - center) / max(width, 1e-6))


def _weighted_avg(items: list[tuple[float | None, float]]) -> float | None:
    present = [(value, weight) for value, weight in items if value is not None]
    if not present:
        return None
    total = sum(weight for _, weight in present)
    return sum(float(value) * weight for value, weight in present) / total


def _sigmoid(x: float) -> float:
    if x >= 40:
        return 1.0
    if x <= -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _clip(value: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0
    return max(low, min(high, value))


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _missing(reason: str) -> FundamentalDislocationProfile:
    return FundamentalDislocationProfile(
        available=False,
        reason=reason,
        factor_count=0,
        latest_annual_period=None,
        latest_quarterly_period=None,
        fundamental_strength=None,
        price_extension=None,
        peer_relative_return_15d=None,
        dislocation_score=0.0,
        score=0.0,
        evidence={},
    )
