from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory

from ifa.core.report.timezones import BJT
from ifa.families.stock.context import StockEdgeRequest, build_context, resolve_as_of_trade_date
from ifa.families.stock.output import output_dir_for_stock_edge
from ifa.families.stock.params import load_params, params_hash, reload_params


@dataclass(frozen=True)
class FakeCalendar:
    open_days: set[dt.date]

    def is_trading_day(self, day: dt.date) -> bool:
        return day in self.open_days

    def prev_trading_day(self, day: dt.date) -> dt.date:
        cur = day - dt.timedelta(days=1)
        while cur not in self.open_days:
            cur -= dt.timedelta(days=1)
        return cur


def _calendar() -> FakeCalendar:
    return FakeCalendar(
        {
            dt.date(2026, 5, 4),
            dt.date(2026, 5, 5),
            dt.date(2026, 5, 6),
            dt.date(2026, 5, 8),
        }
    )


def test_params_load_and_hash_is_stable():
    params = reload_params()
    assert params["runtime"]["default_lookback_days"] == 7
    assert params["model"]["ab_switching"] is False
    assert params_hash(params) == params_hash(load_params())


def test_trading_day_before_1500_uses_previous_trading_day():
    requested_at = dt.datetime(2026, 5, 5, 14, 59, tzinfo=BJT)
    ctx = resolve_as_of_trade_date(requested_at=requested_at, calendar=_calendar())

    assert ctx.as_of_trade_date == dt.date(2026, 5, 4)
    assert ctx.rule == "before_close_cutoff"
    assert ctx.request_date_is_trading_day is True
    assert ctx.data_cutoff_at_bjt == dt.datetime(2026, 5, 4, 15, 0, tzinfo=BJT)


def test_trading_day_at_1500_uses_current_trading_day():
    requested_at = dt.datetime(2026, 5, 5, 15, 0, tzinfo=BJT)
    ctx = resolve_as_of_trade_date(requested_at=requested_at, calendar=_calendar())

    assert ctx.as_of_trade_date == dt.date(2026, 5, 5)
    assert ctx.rule == "after_close_cutoff"
    assert ctx.data_cutoff_at_bjt == dt.datetime(2026, 5, 5, 15, 0, tzinfo=BJT)


def test_trading_day_after_1500_uses_current_trading_day_from_utc_input():
    requested_at = dt.datetime(2026, 5, 5, 7, 30, tzinfo=dt.timezone.utc)
    ctx = resolve_as_of_trade_date(requested_at=requested_at, calendar=_calendar())

    assert ctx.requested_at_bjt == dt.datetime(2026, 5, 5, 15, 30, tzinfo=BJT)
    assert ctx.as_of_trade_date == dt.date(2026, 5, 5)
    assert ctx.rule == "after_close_cutoff"


def test_non_trading_day_uses_latest_completed_trading_day():
    requested_at = dt.datetime(2026, 5, 7, 16, 0, tzinfo=BJT)
    ctx = resolve_as_of_trade_date(requested_at=requested_at, calendar=_calendar())

    assert ctx.as_of_trade_date == dt.date(2026, 5, 6)
    assert ctx.rule == "non_trading_day"
    assert ctx.request_date_is_trading_day is False


def test_naive_requested_at_is_treated_as_bjt_for_cli_calls():
    requested_at = dt.datetime(2026, 5, 5, 14, 0)
    ctx = resolve_as_of_trade_date(requested_at=requested_at, calendar=_calendar())

    assert ctx.requested_at_bjt == dt.datetime(2026, 5, 5, 14, 0, tzinfo=BJT)
    assert ctx.as_of_trade_date == dt.date(2026, 5, 4)


def test_build_context_sets_param_hash():
    request = StockEdgeRequest(ts_code="300042.sz", requested_at=dt.datetime(2026, 5, 5, 15, 1, tzinfo=BJT))
    ctx = build_context(request, calendar=_calendar())

    assert ctx.request.ts_code == "300042.SZ"
    assert ctx.as_of.as_of_trade_date == dt.date(2026, 5, 5)
    assert ctx.param_hash == params_hash(ctx.params)


def test_base_position_requires_share_count():
    try:
        StockEdgeRequest(ts_code="300042.SZ", has_base_position=True)
    except ValueError as exc:
        assert "base_position_shares" in str(exc)
    else:
        raise AssertionError("Expected base position validation to fail")


def test_stock_edge_output_dir_uses_ifaenv_style_layout():
    class RunMode(str, Enum):
        manual = "manual"

    @dataclass(frozen=True)
    class FakeSettings:
        output_root: Path
        run_mode: RunMode = RunMode.manual

    with TemporaryDirectory() as tmp:
        out = output_dir_for_stock_edge(FakeSettings(Path(tmp)), dt.date(2026, 5, 5))  # type: ignore[arg-type]

    assert out.parts[-3:] == ("manual", "20260505", "stock_edge")
