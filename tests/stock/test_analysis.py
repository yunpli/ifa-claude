from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from ifa.core.report.timezones import BJT
from ifa.families.stock.analysis import StockEdgeAnalysis
from ifa.families.stock.context import StockEdgeRequest, build_context
from ifa.families.stock.data.availability import LoadResult
from ifa.families.stock.data.snapshot import StockEdgeSnapshot
from ifa.families.stock.strategies import build_rule_baseline_plan
from tests.stock.test_context import FakeCalendar


def _daily() -> pd.DataFrame:
    rows = []
    for i in range(60):
        close = 10 + i * 0.1
        rows.append({
            "trade_date": dt.date(2026, 1, 1) + dt.timedelta(days=i),
            "open": close - 0.05,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "amount": 100000,
        })
    return pd.DataFrame(rows)


def test_stock_edge_analysis_serializes_core_fields():
    ctx = build_context(
        StockEdgeRequest(ts_code="300042.SZ", requested_at=dt.datetime(2026, 5, 5, 15, 1, tzinfo=BJT)),
        calendar=FakeCalendar({dt.date(2026, 5, 5)}),
    )
    snapshot = StockEdgeSnapshot(
        ctx=ctx,
        daily_bars=LoadResult("daily_bars", _daily(), "postgres", "ok", rows=60, as_of=ctx.as_of.as_of_trade_date, required=True),
        daily_basic=LoadResult("daily_basic", {}, "postgres", "ok", rows=7, as_of=ctx.as_of.as_of_trade_date, required=True),
        moneyflow=LoadResult("moneyflow", pd.DataFrame({"net_mf_amount": [10.0] * 7}), "postgres", "ok", rows=7),
        sector_membership=LoadResult("sector_membership", {}, "postgres", "ok", rows=1),
        ta_context=LoadResult("ta_context", {"candidates": [], "warnings": [], "regime": {}}, "postgres", "ok", rows=1),
        research_lineup=LoadResult("research_lineup", {}, "postgres", "ok", rows=1),
    )
    plan = build_rule_baseline_plan(snapshot)
    analysis = StockEdgeAnalysis(ctx=ctx, snapshot=snapshot, plan=plan)

    payload = analysis.to_dict()
    assert payload["request"]["ts_code"] == "300042.SZ"
    assert payload["as_of"]["as_of_trade_date"] == dt.date(2026, 5, 5)
    assert payload["plan"]["probability"]["model_version"] == "prediction_surface_v1"
    assert payload["plan"]["probability"]["prob_hit_20_40d"] is not None
    decision_layer = payload["decision_layer"]
    for key in ("decision_5d", "decision_10d", "decision_20d"):
        decision = decision_layer[key]
        assert decision["decision"]
        assert decision["score"] is not None
        assert decision["risk_level"]
        assert decision["confidence_level"]
        assert decision["buy_zone"]["low"] is not None
        assert decision["stop_loss"]["price"] is not None
        assert decision["first_take_profit"]["low"] is not None
        assert "不能当作确定性上涨概率" in decision["probability_display_warning"]
    assert decision_layer["decision_5d"]["data_quality"]["status"] == "partial"
    assert decision_layer["decision_10d"]["data_quality"]["status"] == "ok"
    assert decision_layer["decision_20d"]["data_quality"]["status"] == "ok"
    json.dumps(decision_layer)
