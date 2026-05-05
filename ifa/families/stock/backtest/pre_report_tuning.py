"""Pre-report per-stock tuning policy for Stock Edge.

The intended production flow is:

1. User requests a report for one stock.
2. Check whether that stock has a fresh tuned parameter artifact.
3. If stale or missing, tune parameters from that stock's local history first.
4. Generate the prediction execution card with the tuned parameter overlay.

The per-stock overlay starts from the latest global preset. That preset can be
trained on the full market or a high-liquidity universe during a scheduled
weekly job. Report-time tuning is not rolling walk-forward optimization; it is
a per-trigger, per-stock gate that searches a continuous parameter overlay
before report generation.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class PreReportTuningPlan:
    ts_code: str
    as_of_trade_date: dt.date
    should_tune: bool
    reason: str
    history_start: dt.date | None
    history_end: dt.date | None
    history_rows: int
    stale_after_days: int
    max_runtime_seconds: int
    output_namespace: str

    def to_dict(self) -> dict:
        return asdict(self)


def plan_pre_report_tuning(
    daily_bars: pd.DataFrame,
    *,
    ts_code: str,
    as_of_trade_date: dt.date,
    last_tuned_at: dt.datetime | None = None,
    reference_datetime: dt.datetime | None = None,
    stale_after_days: int = 10,
    min_history_rows: int = 360,
    max_history_rows: int = 900,
) -> PreReportTuningPlan:
    """Decide whether a stock needs pre-report parameter tuning.

    The planner is intentionally independent from the optimizer. It answers
    whether we should run a tuning job and what local history window should be
    used. The optimizer can then search continuous parameter overlays without
    changing code.
    """
    if last_tuned_at is not None:
        tuned_at = last_tuned_at
        reference = reference_datetime or dt.datetime.combine(as_of_trade_date, dt.time.min, tzinfo=last_tuned_at.tzinfo)
        if reference.tzinfo is None and tuned_at.tzinfo is not None:
            reference = reference.replace(tzinfo=tuned_at.tzinfo)
        elif reference.tzinfo is not None and tuned_at.tzinfo is None:
            tuned_at = tuned_at.replace(tzinfo=reference.tzinfo)
        elif reference.tzinfo is not None and tuned_at.tzinfo is not None:
            reference = reference.astimezone(tuned_at.tzinfo)
        age_days = max(0, (reference - tuned_at).days)
        if age_days < stale_after_days:
            return PreReportTuningPlan(
                ts_code=ts_code,
                as_of_trade_date=as_of_trade_date,
                should_tune=False,
                reason=f"最近 {age_days} 天内已有调参 artifact，直接复用。",
                history_start=None,
                history_end=None,
                history_rows=0,
                stale_after_days=stale_after_days,
                max_runtime_seconds=0,
                output_namespace=_namespace(ts_code, as_of_trade_date),
            )

    required = {"trade_date", "close", "high", "low"}
    if daily_bars.empty or not required.issubset(daily_bars.columns):
        return PreReportTuningPlan(
            ts_code=ts_code,
            as_of_trade_date=as_of_trade_date,
            should_tune=False,
            reason="本地历史数据不足以做报告前调参，使用全局参数和已训练模型先验。",
            history_start=None,
            history_end=None,
            history_rows=0,
            stale_after_days=stale_after_days,
            max_runtime_seconds=0,
            output_namespace=_namespace(ts_code, as_of_trade_date),
        )

    df = daily_bars.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df = df[df["trade_date"] <= as_of_trade_date].sort_values("trade_date").tail(max_history_rows)
    if len(df) < min_history_rows:
        return PreReportTuningPlan(
            ts_code=ts_code,
            as_of_trade_date=as_of_trade_date,
            should_tune=False,
            reason=f"本地历史只有 {len(df)} 根，低于 {min_history_rows} 根调参底线。",
            history_start=df["trade_date"].iloc[0] if not df.empty else None,
            history_end=df["trade_date"].iloc[-1] if not df.empty else None,
            history_rows=len(df),
            stale_after_days=stale_after_days,
            max_runtime_seconds=0,
            output_namespace=_namespace(ts_code, as_of_trade_date),
        )

    return PreReportTuningPlan(
        ts_code=ts_code,
        as_of_trade_date=as_of_trade_date,
        should_tune=True,
        reason="参数 artifact 缺失或超过 TTL；报告生成前先运行单股历史调参。",
        history_start=df["trade_date"].iloc[0],
        history_end=df["trade_date"].iloc[-1],
        history_rows=len(df),
        stale_after_days=stale_after_days,
        max_runtime_seconds=900,
        output_namespace=_namespace(ts_code, as_of_trade_date),
    )


def _namespace(ts_code: str, as_of_trade_date: dt.date) -> str:
    safe = ts_code.replace(".", "_")
    return f"stock_edge/tuning/{safe}/{as_of_trade_date:%Y%m%d}"
