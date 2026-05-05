"""Stock Edge local snapshot assembly."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Engine

from ifa.families.stock.context import StockEdgeContext

from .availability import LoadResult
from .gateway import LocalDataGateway
from .tushare_backfill import BackfillResult, backfill_core_stock_window


@dataclass(frozen=True)
class StockEdgeSnapshot:
    ctx: StockEdgeContext
    daily_bars: LoadResult
    daily_basic: LoadResult
    moneyflow: LoadResult
    sector_membership: LoadResult
    ta_context: LoadResult
    research_lineup: LoadResult
    event_context: LoadResult | None = None
    model_context: LoadResult | None = None
    backfill: BackfillResult | None = None
    intraday_5min: LoadResult | None = None
    research_prefetch: LoadResult | None = None

    @property
    def results(self) -> list[LoadResult]:
        results = [
            self.daily_bars,
            self.daily_basic,
            self.moneyflow,
            self.event_context,
            self.sector_membership,
            self.ta_context,
            self.research_lineup,
        ]
        results = [r for r in results if r is not None]
        if self.model_context is not None:
            results.append(self.model_context)
        if self.intraday_5min is not None:
            results.append(self.intraday_5min)
        if self.research_prefetch is not None:
            results.append(self.research_prefetch)
        return results

    @property
    def degraded_reasons(self) -> list[str]:
        return [r.message for r in self.results if r.degraded and r.message]

    @property
    def record_status_degraded_reasons(self) -> list[str]:
        """Degradations that should affect persisted report status.

        Intraday 5min data is optional in the first functional Stock Edge
        release. It remains visible in the freshness table but should not mark
        an otherwise complete daily report as partial.
        """
        return [
            r.message
            for r in self.results
            if r.name not in {"intraday_5min", "model_context"} and r.degraded and r.message
        ]

    @property
    def freshness(self) -> list[dict[str, Any]]:
        return [
            {
                "name": r.name,
                "source": r.source,
                "status": r.status,
                "rows": r.rows,
                "as_of": r.as_of,
                "required": r.required,
                "message": r.message,
            }
            for r in self.results
        ]

    def require_mandatory(self) -> None:
        for result in self.results:
            if result.required:
                result.require()


def build_local_snapshot(
    ctx: StockEdgeContext,
    *,
    engine: Engine | None = None,
    gateway: LocalDataGateway | None = None,
    allow_backfill: bool = True,
) -> StockEdgeSnapshot:
    """Build the first functional local snapshot.

    The default scoring horizon is 7 days. Daily bars may fetch a larger
    technical window so later feature builders can compute MA/ATR/S/R without
    another database roundtrip.
    """
    if gateway is None:
        if engine is None:
            raise ValueError("Either engine or gateway is required to build a Stock Edge snapshot.")
        gateway = LocalDataGateway(engine)

    runtime = ctx.params.get("runtime", {})
    default_window = int(runtime.get("default_lookback_days", 7))
    technical_window = int(ctx.params.get("data", {}).get("technical_lookback_days", 60))
    daily_window = max(default_window, technical_window)
    as_of = ctx.as_of.as_of_trade_date
    ts_code = ctx.request.ts_code

    snapshot = _load_snapshot(ctx, gateway, ts_code=ts_code, as_of=as_of, daily_window=daily_window, default_window=default_window)
    backfill_result: BackfillResult | None = None
    data_params = ctx.params.get("data", {})
    should_backfill = (
        allow_backfill
        and engine is not None
        and bool(data_params.get("tushare_backfill_on_missing", True))
        and (not snapshot.daily_bars.ok or not snapshot.daily_basic.ok)
    )
    if should_backfill:
        backfill_result = backfill_core_stock_window(
            engine,
            ts_code,
            as_of,
            daily_rows=daily_window,
            basic_rows=default_window,
            moneyflow_rows=default_window,
        )
        snapshot = _load_snapshot(ctx, gateway, ts_code=ts_code, as_of=as_of, daily_window=daily_window, default_window=default_window, backfill=backfill_result)

    snapshot.require_mandatory()
    return snapshot


def _load_snapshot(
    ctx: StockEdgeContext,
    gateway: LocalDataGateway,
    *,
    ts_code: str,
    as_of,
    daily_window: int,
    default_window: int,
    backfill: BackfillResult | None = None,
) -> StockEdgeSnapshot:
    intraday = None
    intraday_params = ctx.params.get("intraday", {})
    if intraday_params.get("enabled") != False:  # noqa: E712 - accepts "optional" string
        from .intraday import load_intraday_5min

        days = int(intraday_params.get("default_window_days", default_window))
        intraday = load_intraday_5min(
            ts_code,
            start_date=as_of - dt.timedelta(days=max(days * 2, 14)),
            end_date=as_of,
            required=False,
        )
        if (
            engine := getattr(gateway, "engine", None)
        ) is not None and intraday.degraded and bool(intraday_params.get("backfill_on_missing", False)):
            from .intraday_backfill import IntradayBackfillSpec, backfill_intraday_sweep

            sweep = intraday_params.get("sweep") or {}
            specs = [
                IntradayBackfillSpec(ts_code, "5min", int(sweep.get("5min_days", days))),
                IntradayBackfillSpec(ts_code, "30min", int(sweep.get("30min_days", 60))),
                IntradayBackfillSpec(ts_code, "60min", int(sweep.get("60min_days", 90))),
            ]
            try:
                backfill_intraday_sweep(specs, end_date=as_of, on_log=lambda _m: None)
                intraday = load_intraday_5min(
                    ts_code,
                    start_date=as_of - dt.timedelta(days=max(days * 2, 14)),
                    end_date=as_of,
                    required=False,
                )
            except Exception as exc:  # noqa: BLE001
                intraday = LoadResult(
                    name="intraday_5min",
                    data=None,
                    source="missing",
                    status="missing",
                    rows=0,
                    required=False,
                    message=f"Intraday backfill failed: {type(exc).__name__}: {exc}",
                )
    daily_bars = gateway.load_daily_bars(ts_code, as_of, lookback_rows=daily_window, min_rows=default_window)
    daily_basic = gateway.load_daily_basic(ts_code, as_of, lookback_rows=max(default_window, 7), min_rows=default_window)
    moneyflow = gateway.load_moneyflow(ts_code, as_of, lookback_rows=max(default_window, 7), min_rows=3)
    event_context = (
        gateway.load_event_context(ts_code, as_of)
        if hasattr(gateway, "load_event_context")
        else LoadResult(
            name="event_context",
            data={"top_list": [], "top_inst": [], "kpl": [], "limit_list": []},
            source="missing",
            status="missing",
            rows=0,
            required=False,
            message="Event context loader is not available.",
        )
    )
    sector_membership = gateway.load_sector_membership(ts_code, as_of)
    ta_context = gateway.load_ta_context(ts_code, as_of)
    research_lineup = gateway.load_research_lineup(ts_code)
    if hasattr(gateway, "load_model_context"):
        model_context = gateway.load_model_context(ts_code, as_of, sector_membership.data)
    else:
        model_context = LoadResult(
            name="model_context",
            data=None,
            source="missing",
            status="missing",
            rows=0,
            required=False,
            message="Model context loader is not available.",
        )
    return StockEdgeSnapshot(
        ctx=ctx,
        daily_bars=daily_bars,
        daily_basic=daily_basic,
        moneyflow=moneyflow,
        event_context=event_context,
        sector_membership=sector_membership,
        ta_context=ta_context,
        research_lineup=research_lineup,
        model_context=model_context,
        backfill=backfill,
        intraday_5min=intraday,
    )
