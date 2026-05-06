"""Report-time tuning preparation for Stock Edge."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from ifa.families.stock.context import StockEdgeRequest, TradingCalendar, build_context
from ifa.families.stock.data.tushare_backfill import BackfillResult, backfill_core_stock_window
from ifa.families.stock.params import apply_param_overlay, attach_tuning_runtime, load_params, params_hash

from .data import load_daily_bars_for_tuning
from .optimizer import fit_pre_report_overlay
from .pre_report_tuning import plan_pre_report_tuning
from .tuning_artifact import TuningArtifact, artifact_path, find_latest_tuning_artifact, write_tuning_artifact


@dataclass(frozen=True)
class ReportTuningResult:
    params: dict
    status: str
    reason: str
    artifact: TuningArtifact | None
    artifact_file: str | None
    global_artifact: TuningArtifact | None = None
    global_artifact_file: str | None = None
    single_artifact: TuningArtifact | None = None
    single_artifact_file: str | None = None


def prepare_report_params(
    request: StockEdgeRequest,
    *,
    engine: Engine,
    calendar: TradingCalendar | None = None,
    base_params: dict | None = None,
) -> ReportTuningResult:
    """Prepare params for report generation — single-stock overlay only.

    Architectural simplification (post T1.4): the legacy global_preset JSON overlay
    layer is gone; YAML baseline is the single source of truth for global params,
    updated weekly by the panel-based tuner via auto_promote_if_passing. Only the
    per-stock pre_report_overlay JSON layer remains at runtime.

    With `tuning.enabled: false` this is a no-op except for attaching audit
    metadata. When enabled, checks a fresh pre-report overlay artifact for the
    target stock; if stale or missing, runs bounded single-stock tuning before
    report generation.
    """
    params = base_params or load_params()
    tuning_cfg = params.get("tuning", {})
    if not tuning_cfg.get("enabled", False):
        return ReportTuningResult(
            params=dict(params),
            status="disabled",
            reason="tuning.enabled=false",
            artifact=None,
            artifact_file=None,
        )

    ctx = build_context(request, engine=engine, calendar=calendar, params=params)
    overlay_cfg = tuning_cfg.get("pre_report_overlay", {})
    ttl_days = int(overlay_cfg.get("ttl_days", tuning_cfg.get("per_stock_ttl_days", 10)))
    min_history_rows = int(overlay_cfg.get("min_history_rows", tuning_cfg.get("min_history_rows", 360)))
    max_history_rows = int(overlay_cfg.get("max_history_rows", tuning_cfg.get("max_history_rows", 900)))
    max_candidates = int(overlay_cfg.get("max_candidates", 64))

    base = dict(params)
    base_hash = params_hash(base)

    latest = find_latest_tuning_artifact(ts_code=request.ts_code, kind="pre_report_overlay")
    latest_if_compatible = latest if latest and latest.base_param_hash == base_hash else None
    bars = load_daily_bars_for_tuning(
        engine,
        ts_code=request.ts_code,
        as_of_date=ctx.as_of.as_of_trade_date,
        lookback_rows=max_history_rows,
    )
    plan = plan_pre_report_tuning(
        bars,
        ts_code=request.ts_code,
        as_of_trade_date=ctx.as_of.as_of_trade_date,
        last_tuned_at=latest_if_compatible.created_at if latest_if_compatible else None,
        reference_datetime=request.requested_at,
        stale_after_days=ttl_days,
        min_history_rows=min_history_rows,
        max_history_rows=max_history_rows,
    )
    backfill_result: BackfillResult | None = None
    if _should_backfill_short_history(plan, latest_if_compatible=latest_if_compatible, params=base):
        backfill_result = backfill_core_stock_window(
            engine,
            request.ts_code,
            ctx.as_of.as_of_trade_date,
            daily_rows=max_history_rows,
            basic_rows=max(20, int((params.get("runtime") or {}).get("default_lookback_days", 7))),
            moneyflow_rows=max(20, int((params.get("runtime") or {}).get("default_lookback_days", 7))),
        )
        bars = load_daily_bars_for_tuning(
            engine,
            ts_code=request.ts_code,
            as_of_date=ctx.as_of.as_of_trade_date,
            lookback_rows=max_history_rows,
        )
        plan = plan_pre_report_tuning(
            bars,
            ts_code=request.ts_code,
            as_of_trade_date=ctx.as_of.as_of_trade_date,
            last_tuned_at=latest_if_compatible.created_at if latest_if_compatible else None,
            reference_datetime=request.requested_at,
            stale_after_days=ttl_days,
            min_history_rows=min_history_rows,
            max_history_rows=max_history_rows,
        )
        plan_reason = f"{plan.reason}；{_backfill_note(backfill_result)}"
    else:
        plan_reason = plan.reason
    if not plan.should_tune and latest_if_compatible:
        return _with_artifacts(
            base,
            global_artifact=None,
            single_artifact=latest_if_compatible,
            status="reused",
            reason=plan_reason,
            global_file_path=None,
            single_file_path=str(artifact_path(latest_if_compatible)),
        )
    if not plan.should_tune:
        tuned = attach_tuning_runtime(
            base,
            status="skipped",
            reason=plan_reason,
        )
        return ReportTuningResult(
            params=tuned,
            status="skipped",
            reason=plan_reason,
            artifact=None,
            artifact_file=None,
        )

    artifact = fit_pre_report_overlay(
        bars,
        ts_code=request.ts_code,
        as_of_trade_date=ctx.as_of.as_of_trade_date,
        base_params=base,
        max_candidates=max_candidates,
    )
    file_path = str(write_tuning_artifact(artifact))
    return _with_artifacts(
        base,
        global_artifact=None,
        single_artifact=artifact,
        status="generated",
        reason=plan_reason,
        global_file_path=None,
        single_file_path=file_path,
    )


def _should_backfill_short_history(
    plan,
    *,
    latest_if_compatible: TuningArtifact | None,
    params: dict,
) -> bool:
    if latest_if_compatible is not None:
        return False
    tuning_cfg = params.get("tuning", {})
    overlay_cfg = tuning_cfg.get("pre_report_overlay", {})
    enabled = bool(overlay_cfg.get("backfill_on_short_history", params.get("data", {}).get("tushare_backfill_on_missing", True)))
    return enabled and not plan.should_tune and plan.history_rows < int(overlay_cfg.get("min_history_rows", tuning_cfg.get("min_history_rows", 360)))


def _backfill_note(result: BackfillResult) -> str:
    if not result.attempted:
        return "TuShare backfill 检查后无缺失交易日。"
    counts = ", ".join(f"{key}={value}" for key, value in sorted(result.fetched_counts.items()))
    if result.errors:
        return f"TuShare backfill 已尝试 {len(result.requested_dates)} 个交易日，{counts}，错误 {len(result.errors)} 条。"
    return f"TuShare backfill 已补 {len(result.requested_dates)} 个交易日，{counts}。"


def _compatible_global_artifact(
    base_params: dict,
    *,
    reference_datetime,
    ttl_days: int,
    enabled: bool,
) -> TuningArtifact | None:
    if not enabled:
        return None
    latest = find_latest_tuning_artifact(ts_code="__GLOBAL__", kind="global_preset")
    if latest is None or latest.base_param_hash != params_hash(base_params):
        return None
    tuned_at = latest.created_at
    reference = reference_datetime or dt.datetime.now(tuned_at.tzinfo or dt.timezone.utc)
    if reference.tzinfo is None and tuned_at.tzinfo is not None:
        reference = reference.replace(tzinfo=tuned_at.tzinfo)
    elif reference.tzinfo is not None and tuned_at.tzinfo is None:
        tuned_at = tuned_at.replace(tzinfo=reference.tzinfo)
    elif reference.tzinfo is not None and tuned_at.tzinfo is not None:
        reference = reference.astimezone(tuned_at.tzinfo)
    if max(0, (reference - tuned_at).days) >= ttl_days:
        return None
    return latest


def _with_artifacts(
    params: dict,
    *,
    global_artifact: TuningArtifact | None,
    single_artifact: TuningArtifact,
    status: str,
    reason: str,
    global_file_path: str | None,
    single_file_path: str,
) -> ReportTuningResult:
    overlaid = apply_param_overlay(params, single_artifact.overlay)
    tuned = attach_tuning_runtime(
        overlaid,
        status=status,
        reason=reason,
        artifact_path=single_file_path,
        global_artifact_path=global_file_path,
        single_artifact_path=single_file_path,
        objective_score=single_artifact.objective_score,
        global_objective_score=global_artifact.objective_score if global_artifact else None,
        single_objective_score=single_artifact.objective_score,
        candidate_count=single_artifact.candidate_count,
        global_candidate_count=global_artifact.candidate_count if global_artifact else None,
        single_candidate_count=single_artifact.candidate_count,
    )
    return ReportTuningResult(
        params=tuned,
        status=status,
        reason=reason,
        artifact=single_artifact,
        artifact_file=single_file_path,
        global_artifact=global_artifact,
        global_artifact_file=global_file_path,
        single_artifact=single_artifact,
        single_artifact_file=single_file_path,
    )


def _with_artifact(
    params: dict,
    artifact: TuningArtifact,
    *,
    status: str,
    reason: str,
    file_path: str,
) -> ReportTuningResult:
    overlaid = apply_param_overlay(params, artifact.overlay)
    tuned = attach_tuning_runtime(
        overlaid,
        status=status,
        reason=reason,
        artifact_path=file_path,
        objective_score=artifact.objective_score,
        candidate_count=artifact.candidate_count,
    )
    return ReportTuningResult(params=tuned, status=status, reason=reason, artifact=artifact, artifact_file=file_path)
