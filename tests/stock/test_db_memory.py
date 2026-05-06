from __future__ import annotations

import datetime as dt

import pytest

from ifa.core.report.timezones import BJT
from ifa.families.stock.context import StockEdgeRequest, build_context
from ifa.families.stock.db.memory import db_analysis_type, find_reusable_analysis
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


class _FakeResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.params: dict | None = None

    def execute(self, _stmt, params: dict):
        self.params = params
        return _FakeResult(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeEngine:
    def __init__(self, rows: list[dict]):
        self.conn = _FakeConnection(rows)

    def connect(self):
        return self.conn


def test_find_reusable_analysis_returns_latest_matching_row_without_name_error():
    rows = [
        {
            "record_id": "r1",
            "ts_code": "001339.SZ",
            "validation_json": {"param_hash": "old"},
            "output_html_path": "/tmp/old.html",
        },
        {
            "record_id": "r2",
            "ts_code": "001339.SZ",
            "validation_json": {"param_hash": "wanted"},
            "output_html_path": "/tmp/wanted.html",
        },
    ]
    engine = _FakeEngine(rows)

    reusable = find_reusable_analysis(
        engine,  # type: ignore[arg-type]
        ts_code="001339.sz",
        mode="quick",
        data_cutoff_at=dt.datetime(2026, 5, 5, 15, 0, tzinfo=BJT),
        param_hash="wanted",
    )

    assert reusable is not None
    assert reusable["record_id"] == "r2"
    assert engine.conn.params is not None
    assert engine.conn.params["ts_code"] == "001339.SZ"
    assert engine.conn.params["analysis_type"] == "fast"
