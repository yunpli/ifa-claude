"""TA walk-forward backtest engine — independent of report generation."""
from ifa.families.ta.backtest.runner import (
    BacktestResult,
    backtest_window,
    walk_forward,
)
from ifa.families.ta.backtest.tier_perf import TierPerf, analyze_tier_perf

__all__ = [
    "BacktestResult", "backtest_window", "walk_forward",
    "TierPerf", "analyze_tier_perf",
]
