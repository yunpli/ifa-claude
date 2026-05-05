from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from ifa.core.report.timezones import BJT
from ifa.families.stock.context import StockEdgeRequest, build_context
from ifa.families.stock.data.availability import LoadResult
from ifa.families.stock.data.snapshot import build_local_snapshot
from tests.stock.test_context import FakeCalendar


@dataclass(frozen=True)
class FakeGateway:
    missing_daily: bool = False

    def load_daily_bars(self, ts_code, as_of, *, lookback_rows, min_rows, required=True):
        if self.missing_daily:
            return LoadResult("daily_bars", None, "missing", "missing", required=True, message="daily missing")
        assert lookback_rows == 360
        assert min_rows == 7
        return LoadResult("daily_bars", {"ts_code": ts_code}, "postgres", "ok", rows=360, as_of=as_of, required=required)

    def load_daily_basic(self, ts_code, as_of, *, lookback_rows, min_rows, required=True):
        assert lookback_rows == 7
        return LoadResult("daily_basic", {}, "postgres", "ok", rows=7, as_of=as_of, required=required)

    def load_moneyflow(self, ts_code, as_of, *, lookback_rows, min_rows, required=False):
        return LoadResult("moneyflow", None, "missing", "missing", required=False, message="moneyflow missing")

    def load_sector_membership(self, ts_code, as_of):
        return LoadResult("sector_membership", {}, "postgres", "ok", rows=1, as_of=as_of)

    def load_ta_context(self, ts_code, as_of):
        return LoadResult("ta_context", {}, "postgres", "ok", rows=1, as_of=as_of)

    def load_research_lineup(self, ts_code):
        return LoadResult("research_lineup", {}, "postgres", "ok", rows=1)


def _ctx():
    return build_context(
        StockEdgeRequest(ts_code="300042.SZ", requested_at=dt.datetime(2026, 5, 5, 15, 1, tzinfo=BJT)),
        calendar=FakeCalendar({dt.date(2026, 5, 5)}),
    )


def test_build_local_snapshot_allows_optional_degraded_data():
    snapshot = build_local_snapshot(_ctx(), gateway=FakeGateway(), allow_backfill=False)

    assert snapshot.daily_bars.ok is True
    assert snapshot.moneyflow.degraded is True
    assert "moneyflow missing" in snapshot.degraded_reasons
    assert "moneyflow missing" in snapshot.record_status_degraded_reasons
    intraday_messages = [
        r.message
        for r in snapshot.results
        if r.name == "intraday_5min" and r.degraded and r.message
    ]
    for message in intraday_messages:
        assert message not in snapshot.record_status_degraded_reasons
    assert snapshot.freshness[0]["name"] == "daily_bars"


def test_build_local_snapshot_raises_on_missing_mandatory_data():
    with pytest.raises(RuntimeError, match="daily missing"):
        build_local_snapshot(_ctx(), gateway=FakeGateway(missing_daily=True), allow_backfill=False)
