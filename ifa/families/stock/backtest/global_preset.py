"""Global preset refresh policy for Stock Edge tuning."""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GlobalPresetPlan:
    should_refresh: bool
    reason: str
    universe: str
    min_stocks: int
    max_stocks: int
    artifact_namespace: str
    refresh_after_days: int

    def to_dict(self) -> dict:
        return asdict(self)


def plan_global_preset_refresh(
    *,
    as_of_date: dt.date,
    last_trained_at: dt.datetime | None = None,
    universe: str = "top_liquidity_500",
    min_stocks: int = 300,
    max_stocks: int = 800,
    refresh_after_days: int = 7,
) -> GlobalPresetPlan:
    """Decide whether the shared Stock Edge preset should be refreshed.

    The global preset is a scheduled training artifact, typically refreshed on
    the weekend from a high-liquidity universe. It provides the baseline that
    pre-report single-stock overlays personalize from.
    """
    namespace = f"stock_edge/global_preset/{universe}/{as_of_date:%Y%m%d}"
    if last_trained_at is not None:
        age_days = (dt.datetime.combine(as_of_date, dt.time.min, tzinfo=last_trained_at.tzinfo) - last_trained_at).days
        if age_days < refresh_after_days:
            return GlobalPresetPlan(
                should_refresh=False,
                reason=f"全局 preset 最近 {age_days} 天内已训练，继续复用。",
                universe=universe,
                min_stocks=min_stocks,
                max_stocks=max_stocks,
                artifact_namespace=namespace,
                refresh_after_days=refresh_after_days,
            )

    return GlobalPresetPlan(
        should_refresh=True,
        reason="全局 preset 缺失或超过刷新周期；应启动周末/overnight 训练。",
        universe=universe,
        min_stocks=min_stocks,
        max_stocks=max_stocks,
        artifact_namespace=namespace,
        refresh_after_days=refresh_after_days,
    )
