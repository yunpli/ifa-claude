from __future__ import annotations

import datetime as dt

import pytest

from ifa.core.report.timezones import BJT
from ifa.families.stock.context import StockEdgeRequest, build_context
from ifa.families.stock.db.memory import db_analysis_type
from tests.stock.test_context import FakeCalendar


def test_db_analysis_type_maps_quick_to_existing_fast_value():
    assert db_analysis_type("quick") == "fast"
    assert db_analysis_type("deep") == "deep"
    assert db_analysis_type("update") == "update"


def test_db_analysis_type_rejects_unknown_mode():
    with pytest.raises(ValueError):
        db_analysis_type("swing")


def test_context_metadata_can_be_built_for_stock_schema_record():
    calendar = FakeCalendar({dt.date(2026, 5, 5)})
    ctx = build_context(
        StockEdgeRequest(
            ts_code="300042.sz",
            requested_at=dt.datetime(2026, 5, 5, 15, 1, tzinfo=BJT),
            mode="quick",
        ),
        calendar=calendar,
    )

    assert ctx.request.ts_code == "300042.SZ"
    assert ctx.as_of.as_of_trade_date == dt.date(2026, 5, 5)
    assert ctx.params["model"]["versions"]["right_tail"] == "prediction_surface_v1"
