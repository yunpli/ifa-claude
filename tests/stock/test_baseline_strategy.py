from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from ifa.core.report.timezones import BJT
from ifa.families.stock.context import StockEdgeRequest, build_context
from ifa.families.stock.data.availability import LoadResult
from ifa.families.stock.data.snapshot import StockEdgeSnapshot
from ifa.families.stock.strategies import build_rule_baseline_plan
from tests.stock.test_context import FakeCalendar


def _daily(amount: float = 100000.0) -> pd.DataFrame:
    rows = []
    for i in range(60):
        close = 10 + i * 0.1
        rows.append(
            {
                "trade_date": dt.date(2026, 1, 1) + dt.timedelta(days=i),
                "open": close - 0.05,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "amount": amount,
            }
        )
    return pd.DataFrame(rows)


def _ctx(has_base: bool = False):
    return build_context(
        StockEdgeRequest(
            ts_code="300042.SZ",
            requested_at=dt.datetime(2026, 5, 5, 15, 1, tzinfo=BJT),
            has_base_position=has_base,
            base_position_shares=1000 if has_base else None,
        ),
        calendar=FakeCalendar({dt.date(2026, 5, 5)}),
    )


def _snapshot(*, amount: float = 100000.0, has_base: bool = False) -> StockEdgeSnapshot:
    daily = _daily(amount)
    moneyflow = pd.DataFrame({
        "net_mf_amount": [100.0] * 7,
        "buy_elg_amount": [80.0] * 7,
        "sell_elg_amount": [30.0] * 7,
        "buy_lg_amount": [60.0] * 7,
        "sell_lg_amount": [20.0] * 7,
    })
    ctx = _ctx(has_base=has_base)
    return StockEdgeSnapshot(
        ctx=ctx,
        daily_bars=LoadResult("daily_bars", daily, "postgres", "ok", rows=len(daily), as_of=ctx.as_of.as_of_trade_date, required=True),
        daily_basic=LoadResult(
            "daily_basic",
            pd.DataFrame({
                "trade_date": [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(7)],
                "turnover_rate_f": [3.0] * 7,
                "volume_ratio": [1.2] * 7,
                "pe_ttm": [30.0] * 7,
                "pb": [3.0] * 7,
            }),
            "postgres",
            "ok",
            rows=7,
            as_of=ctx.as_of.as_of_trade_date,
            required=True,
        ),
        moneyflow=LoadResult("moneyflow", moneyflow, "postgres", "ok", rows=7, as_of=ctx.as_of.as_of_trade_date),
        sector_membership=LoadResult("sector_membership", {}, "postgres", "ok", rows=1),
        ta_context=LoadResult("ta_context", {"candidates": [{"setup_name": "T1"}], "warnings": [], "regime": {}}, "postgres", "ok", rows=1),
        research_lineup=LoadResult("research_lineup", {"annual_factors": [{}], "quarterly_factors": []}, "postgres", "ok", rows=1),
    )


def test_rule_baseline_builds_auditable_trade_plan():
    plan = build_rule_baseline_plan(_snapshot())

    assert plan.action in {"buy", "watch"}
    assert plan.entry_zone is not None
    assert plan.stop is not None
    assert plan.targets
    assert plan.probability.model_version == "prediction_surface_v1"
    assert plan.probability.prob_hit_20_40d is not None
    assert plan.probability.opportunities
    assert plan.probability.best_opportunity is not None
    assert plan.probability.calibrated is False
    assert 0.0 <= plan.position_size.budget_fraction <= 0.35
    assert plan.position_size.reason
    assert any(item.key == "策略矩阵总分" for item in plan.evidence)


def test_rule_baseline_vetoes_low_liquidity():
    plan = build_rule_baseline_plan(_snapshot(amount=1.0))

    assert plan.action == "avoid"
    assert plan.vetoes
    assert plan.position_size.budget_fraction == 0.0


def test_t0_plan_requires_base_position():
    no_base = build_rule_baseline_plan(_snapshot(has_base=False))
    with_base = build_rule_baseline_plan(_snapshot(has_base=True))

    assert no_base.t0_plan is not None
    assert no_base.t0_plan.eligible is False
    assert with_base.t0_plan is not None
    assert with_base.t0_plan.eligible is True
