"""TA walk-forward backtest engine — independent of report generation."""
from ifa.families.ta.backtest.runner import (
    BacktestResult,
    backtest_window,
    walk_forward,
)

__all__ = ["BacktestResult", "backtest_window", "walk_forward"]
