"""Public Stock Edge orchestration entrypoints."""
from __future__ import annotations

from sqlalchemy.engine import Engine

from ifa.families.stock.analysis import StockEdgeAnalysis, run_rule_baseline_analysis
from ifa.families.stock.context import StockEdgeContext, StockEdgeRequest, TradingCalendar, build_context


def prepare_stock_edge_context(
    request: StockEdgeRequest,
    *,
    engine: Engine | None = None,
    calendar: TradingCalendar | None = None,
    params: dict | None = None,
) -> StockEdgeContext:
    return build_context(request, engine=engine, calendar=calendar, params=params)


def build_stock_edge_analysis(
    request: StockEdgeRequest,
    *,
    engine: Engine,
    calendar: TradingCalendar | None = None,
    params: dict | None = None,
) -> StockEdgeAnalysis:
    return run_rule_baseline_analysis(request, engine=engine, calendar=calendar, params=params)
