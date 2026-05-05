from __future__ import annotations

import datetime as dt

import pandas as pd

from ifa.core.report.timezones import BJT
from ifa.families.stock.context import StockEdgeRequest, build_context
from ifa.families.stock.data.availability import LoadResult
from ifa.families.stock.data.research_prefetch import ensure_stock_edge_research_prefetch
from ifa.families.stock.data.snapshot import StockEdgeSnapshot
from tests.stock.test_context import FakeCalendar


def _snapshot(ctx) -> StockEdgeSnapshot:
    leaders = {
        "size": [
            {"ts_code": "300042.SZ", "name": "朗科科技", "is_target": True},
            {"ts_code": "000001.SZ", "name": "同行A"},
            {"ts_code": "000002.SZ", "name": "同行B"},
        ],
        "momentum": [{"ts_code": "000003.SZ", "name": "同行C"}],
        "moneyflow": [{"ts_code": "000004.SZ", "name": "同行D"}],
        "ta": [{"ts_code": "000005.SZ", "name": "同行E"}],
    }
    return StockEdgeSnapshot(
        ctx=ctx,
        daily_bars=LoadResult("daily_bars", pd.DataFrame(), "postgres", "ok", rows=360, required=True),
        daily_basic=LoadResult("daily_basic", pd.DataFrame(), "postgres", "ok", rows=7, required=True),
        moneyflow=LoadResult("moneyflow", pd.DataFrame(), "postgres", "ok", rows=7),
        sector_membership=LoadResult("sector_membership", {"sector_leaders": leaders}, "postgres", "ok", rows=1),
        ta_context=LoadResult("ta_context", {}, "postgres", "ok", rows=1),
        research_lineup=LoadResult("research_lineup", {}, "postgres", "ok", rows=0),
    )


def test_research_prefetch_triggers_target_and_limited_sector_leaders(monkeypatch):
    ctx = build_context(
        StockEdgeRequest(ts_code="300042.SZ", requested_at=dt.datetime(2026, 5, 5, 15, 1, tzinfo=BJT)),
        calendar=FakeCalendar({dt.date(2026, 5, 5)}),
    )
    calls = []

    def fake_ensure(*_args, **kwargs):
        calls.append((kwargs["ts_code"], kwargs["analysis_type"], kwargs["tier"], kwargs["reuse"], kwargs["llm"], kwargs["llm_timeout_seconds"]))

        class Result:
            def to_dict(self):
                return {
                    "ts_code": kwargs["ts_code"],
                    "analysis_type": kwargs["analysis_type"],
                    "tier": kwargs["tier"],
                    "status": "reused",
                    "reused": True,
                    "html_path": "/tmp/report.html",
                }

        return Result()

    monkeypatch.setattr("ifa.families.stock.data.research_prefetch.ensure_research_report", fake_ensure)
    result = ensure_stock_edge_research_prefetch(ctx, _snapshot(ctx), engine=object())  # type: ignore[arg-type]

    ts_codes = [call[0] for call in calls]
    assert ts_codes[:2] == ["300042.SZ", "300042.SZ"]
    assert set(ts_codes) == {"300042.SZ", "000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"}
    assert all(call[1] in {"annual", "quarterly"} for call in calls)
    assert all(call[2] == "deep" for call in calls)
    assert all(call[3] is True for call in calls)
    assert [call[4] for call in calls[:2]] == [True, True]
    assert all(call[4] is False for call in calls[2:])
    assert all(call[5] == 45.0 for call in calls)
    assert result.status == "ok"
    assert result.rows == 10
