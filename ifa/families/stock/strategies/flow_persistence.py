"""Money-flow persistence and decay signal for Stock Edge."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FlowPersistenceProfile:
    available: bool
    reason: str
    sample_count: int
    net_sum_wan: float | None
    weighted_net_wan: float | None
    positive_day_share: float | None
    latest_3d_vs_prior_pct: float | None
    same_sign_streak_days: int
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_flow_persistence_profile(moneyflow: pd.DataFrame | None, *, params: dict[str, Any]) -> FlowPersistenceProfile:
    """Measure whether main money flow is persistent or decaying."""
    if not params.get("enabled", True):
        return _missing("flow persistence disabled")
    if moneyflow is None or moneyflow.empty or "net_mf_amount" not in moneyflow.columns:
        return _missing("主力净流字段不可用。")
    df = moneyflow.copy()
    if "trade_date" in df.columns:
        df = df.sort_values("trade_date")
    net = pd.to_numeric(df["net_mf_amount"], errors="coerce").dropna()
    window = int(params.get("window_days", 10))
    min_rows = int(params.get("min_rows", 5))
    tail = net.tail(window)
    if len(tail) < min_rows:
        return _missing(f"资金流样本 {len(tail)} 条，低于 {min_rows} 条。")
    weights = _recency_weights(len(tail), float(params.get("recency_decay", 0.82)))
    net_sum = float(tail.sum())
    weighted_net = float((tail.reset_index(drop=True) * weights).sum() / max(float(weights.sum()), 1e-9))
    positive_share = float((tail > 0).sum() / len(tail))
    latest3 = float(tail.tail(3).mean())
    prior = float(tail.iloc[:-3].mean()) if len(tail) > 3 else 0.0
    latest_3d_vs_prior = _signed_change_pct(latest3, prior)
    streak = _same_sign_streak(tail)
    scale = max(float(params.get("net_scale_wan", 12000.0)), 1e-6)
    persistence = positive_share * 2.0 - 1.0
    decay_component = -0.18 * math.tanh(max(-latest_3d_vs_prior, 0.0) / 60.0) if weighted_net > 0 else 0.18 * math.tanh(max(latest_3d_vs_prior, 0.0) / 60.0)
    score = (
        0.36 * math.tanh(weighted_net / scale)
        + 0.26 * persistence
        + 0.18 * math.tanh(streak / 5.0) * (1.0 if tail.iloc[-1] >= 0 else -1.0)
        + decay_component
    )
    score = max(-0.45, min(0.45, score))
    return FlowPersistenceProfile(
        available=True,
        reason="已完成主力资金持续性/衰减分析。",
        sample_count=len(tail),
        net_sum_wan=round(net_sum, 4),
        weighted_net_wan=round(weighted_net, 4),
        positive_day_share=round(positive_share, 4),
        latest_3d_vs_prior_pct=round(latest_3d_vs_prior, 4),
        same_sign_streak_days=streak,
        score=round(score, 4),
    )


def _recency_weights(n: int, decay: float) -> pd.Series:
    decay = max(0.05, min(decay, 0.99))
    values = [decay ** (n - i - 1) for i in range(n)]
    return pd.Series(values)


def _signed_change_pct(latest: float, prior: float) -> float:
    denom = max(abs(prior), 1.0)
    return (latest - prior) / denom * 100.0


def _same_sign_streak(values: pd.Series) -> int:
    if values.empty:
        return 0
    latest_sign = 1 if float(values.iloc[-1]) >= 0 else -1
    streak = 0
    for value in reversed(values.tolist()):
        sign = 1 if float(value) >= 0 else -1
        if sign != latest_sign:
            break
        streak += 1
    return streak


def _missing(reason: str) -> FlowPersistenceProfile:
    return FlowPersistenceProfile(
        available=False,
        reason=reason,
        sample_count=0,
        net_sum_wan=None,
        weighted_net_wan=None,
        positive_day_share=None,
        latest_3d_vs_prior_pct=None,
        same_sign_streak_days=0,
        score=0.0,
    )
