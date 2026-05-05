"""Core Stock Edge request context and as-of-date routing.

Stock Edge must be reproducible. The first decision in every run is therefore
which completed A-share trading day the analysis is allowed to see.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy.engine import Engine

from ifa.core.calendar import is_trading_day, prev_trading_day
from ifa.core.report.timezones import BJT
from ifa.families.stock.params import load_params, params_hash

AnalysisMode = Literal["quick", "deep", "update"]
RunMode = Literal["manual", "production", "test"]
AsOfRule = Literal["before_close_cutoff", "after_close_cutoff", "non_trading_day"]


class TradingCalendar(Protocol):
    """Small protocol used to unit-test date routing without a real database."""

    def is_trading_day(self, day: dt.date) -> bool:
        ...

    def prev_trading_day(self, day: dt.date) -> dt.date:
        ...


@dataclass(frozen=True)
class SqlTradingCalendar:
    """Trading calendar backed by `smartmoney.trade_cal`."""

    engine: Engine
    exchange: str = "SSE"

    def is_trading_day(self, day: dt.date) -> bool:
        return is_trading_day(self.engine, day, exchange=self.exchange)

    def prev_trading_day(self, day: dt.date) -> dt.date:
        return prev_trading_day(self.engine, day, exchange=self.exchange)


@dataclass(frozen=True)
class AsOfContext:
    """Resolved data cutoff for one Stock Edge request."""

    requested_at: dt.datetime
    requested_at_bjt: dt.datetime
    as_of_trade_date: dt.date
    data_cutoff_at: dt.datetime
    rule: AsOfRule
    exchange: str = "SSE"
    request_date_is_trading_day: bool = True

    @property
    def data_cutoff_at_bjt(self) -> dt.datetime:
        return self.data_cutoff_at.astimezone(BJT)


@dataclass(frozen=True)
class StockEdgeRequest:
    ts_code: str
    requested_at: dt.datetime | None = None
    mode: AnalysisMode = "quick"
    run_mode: RunMode = "manual"
    has_base_position: bool = False
    base_position_shares: int | None = None
    fresh: bool = False

    def __post_init__(self) -> None:
        ts_code = self.ts_code.strip().upper()
        if not ts_code:
            raise ValueError("StockEdgeRequest.ts_code is required.")
        if self.mode not in ("quick", "deep", "update"):
            raise ValueError(f"Unsupported Stock Edge mode: {self.mode!r}")
        if self.run_mode not in ("manual", "production", "test"):
            raise ValueError(f"Unsupported Stock Edge run_mode: {self.run_mode!r}")
        if self.base_position_shares is not None and self.base_position_shares < 0:
            raise ValueError("base_position_shares cannot be negative.")
        if self.has_base_position and not self.base_position_shares:
            raise ValueError("base_position_shares is required when has_base_position=True.")
        object.__setattr__(self, "ts_code", ts_code)


@dataclass(frozen=True)
class StockEdgeContext:
    request: StockEdgeRequest
    as_of: AsOfContext
    params: dict[str, Any] = field(default_factory=load_params)
    param_hash: str = ""

    def __post_init__(self) -> None:
        if not self.param_hash:
            object.__setattr__(self, "param_hash", params_hash(self.params))


def build_context(
    request: StockEdgeRequest,
    *,
    engine: Engine | None = None,
    calendar: TradingCalendar | None = None,
    params: dict[str, Any] | None = None,
) -> StockEdgeContext:
    """Build the minimal context needed by later Stock Edge phases."""
    loaded_params = params or load_params()
    as_of = resolve_as_of_trade_date(
        requested_at=request.requested_at,
        engine=engine,
        calendar=calendar,
        cutoff_time=_parse_cutoff(loaded_params),
    )
    return StockEdgeContext(
        request=request,
        as_of=as_of,
        params=loaded_params,
        param_hash=params_hash(loaded_params),
    )


def resolve_as_of_trade_date(
    *,
    requested_at: dt.datetime | None = None,
    engine: Engine | None = None,
    calendar: TradingCalendar | None = None,
    cutoff_time: dt.time = dt.time(15, 0),
    exchange: str = "SSE",
) -> AsOfContext:
    """Resolve the completed trade date Stock Edge may use.

    Rule:
    - trading day before 15:00 BJT: use T-1
    - trading day at/after 15:00 BJT: use T
    - non-trading day: use latest completed trading day

    Naive `requested_at` values are treated as Beijing time. This keeps CLI and
    tests natural for A-share users while all persisted timestamps remain
    timezone-aware UTC.
    """
    if calendar is None:
        if engine is None:
            raise ValueError("Either engine or calendar is required to resolve Stock Edge as-of date.")
        calendar = SqlTradingCalendar(engine=engine, exchange=exchange)

    requested_bjt = _to_bjt_requested_at(requested_at)
    request_date = requested_bjt.date()
    is_open = calendar.is_trading_day(request_date)

    if not is_open:
        as_of = calendar.prev_trading_day(request_date)
        rule: AsOfRule = "non_trading_day"
    elif requested_bjt.time() < cutoff_time:
        as_of = calendar.prev_trading_day(request_date)
        rule = "before_close_cutoff"
    else:
        as_of = request_date
        rule = "after_close_cutoff"

    cutoff_bjt = dt.datetime.combine(as_of, cutoff_time, tzinfo=BJT)
    return AsOfContext(
        requested_at=requested_bjt.astimezone(dt.timezone.utc),
        requested_at_bjt=requested_bjt,
        as_of_trade_date=as_of,
        data_cutoff_at=cutoff_bjt.astimezone(dt.timezone.utc),
        rule=rule,
        exchange=exchange,
        request_date_is_trading_day=is_open,
    )


def _to_bjt_requested_at(value: dt.datetime | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(tz=BJT)
    if value.tzinfo is None:
        return value.replace(tzinfo=BJT)
    return value.astimezone(BJT)


def _parse_cutoff(params: dict[str, Any]) -> dt.time:
    raw = str(params.get("runtime", {}).get("market_close_cutoff", "15:00"))
    try:
        hour, minute = raw.split(":", 1)
        return dt.time(int(hour), int(minute))
    except Exception as exc:
        raise ValueError(f"Invalid stock_edge runtime.market_close_cutoff: {raw!r}") from exc
