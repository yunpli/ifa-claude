"""Stock Edge analysis orchestration for the first functional baseline."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.engine import Engine

from ifa.families.stock.context import StockEdgeContext, StockEdgeRequest, TradingCalendar, build_context
from ifa.families.stock.data import StockEdgeSnapshot, build_local_snapshot
from ifa.families.stock.data.research_prefetch import ensure_stock_edge_research_prefetch
from ifa.families.stock.decision_layer import build_decision_layer
from ifa.families.stock.plan import TradePlan
from ifa.families.stock.strategies import build_rule_baseline_plan


@dataclass(frozen=True)
class StockEdgeAnalysis:
    ctx: StockEdgeContext
    snapshot: StockEdgeSnapshot
    plan: TradePlan
    decision_layer: dict[str, Any] | None = None

    def resolved_decision_layer(self) -> dict[str, Any]:
        return self.decision_layer or build_decision_layer(self.snapshot, self.plan)

    def to_dict(self) -> dict:
        return {
            "request": {
                "ts_code": self.ctx.request.ts_code,
                "mode": self.ctx.request.mode,
                "run_mode": self.ctx.request.run_mode,
                "fresh": self.ctx.request.fresh,
                "has_base_position": self.ctx.request.has_base_position,
            },
            "as_of": {
                "as_of_trade_date": self.ctx.as_of.as_of_trade_date,
                "data_cutoff_at": self.ctx.as_of.data_cutoff_at,
                "data_cutoff_at_bjt": self.ctx.as_of.data_cutoff_at_bjt,
                "rule": self.ctx.as_of.rule,
            },
            "param_hash": self.ctx.param_hash,
            "freshness": self.snapshot.freshness,
            "degraded_reasons": self.snapshot.degraded_reasons,
            "record_status_degraded_reasons": self.snapshot.record_status_degraded_reasons,
            "plan": self.plan.to_dict(),
            "decision_layer": self.resolved_decision_layer(),
        }


def run_rule_baseline_analysis(
    request: StockEdgeRequest,
    *,
    engine: Engine,
    calendar: TradingCalendar | None = None,
    params: dict | None = None,
) -> StockEdgeAnalysis:
    """Run the local-first rule baseline without rendering or persistence."""
    ctx = build_context(request, engine=engine, calendar=calendar, params=params)
    snapshot = build_local_snapshot(ctx, engine=engine)
    prefetch = ensure_stock_edge_research_prefetch(ctx, snapshot, engine=engine)
    if prefetch.rows:
        snapshot = build_local_snapshot(ctx, engine=engine, allow_backfill=False)
    snapshot = replace(snapshot, research_prefetch=prefetch)
    plan = build_rule_baseline_plan(snapshot)
    decision_layer = build_decision_layer(snapshot, plan)
    return StockEdgeAnalysis(ctx=ctx, snapshot=snapshot, plan=plan, decision_layer=decision_layer)
