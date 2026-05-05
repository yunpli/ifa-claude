"""End-to-end Stock Edge report runner.

This runner reuses the existing `stock` schema:
  - stock.analysis_record
  - stock.report_sections
  - stock.analysis_lock

It deliberately does not create new tables. Stricter cache keys can be added
later with a minimal migration once the report loop is stable.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from ifa.config import Settings
from ifa.families.stock.analysis import StockEdgeAnalysis, run_rule_baseline_analysis
from ifa.families.stock.backtest import prepare_report_params
from ifa.families.stock.context import StockEdgeRequest
from ifa.families.stock.db.lock import acquire_or_wait, release_lock
from ifa.families.stock.db.memory import (
    create_analysis_record,
    db_analysis_type,
    finalize_analysis_record,
    find_reusable_analysis,
    insert_report_section,
)

from .builder import build_report_model
from .renderer import RenderedStockEdgeReport, render_report_assets


@dataclass(frozen=True)
class StockEdgeRunResult:
    analysis: StockEdgeAnalysis | None
    rendered: RenderedStockEdgeReport | None
    record_id: str | None
    reused: bool = False
    reusable_record: dict | None = None

    @property
    def html_path(self) -> Path | None:
        if self.rendered:
            return self.rendered.html_path
        if self.reusable_record and self.reusable_record.get("output_html_path"):
            return Path(str(self.reusable_record["output_html_path"]))
        return None


def run_stock_edge_report(
    request: StockEdgeRequest,
    *,
    engine: Engine,
    settings: Settings,
) -> StockEdgeRunResult:
    """Run, render, and persist one Stock Edge report."""
    tuning = prepare_report_params(request, engine=engine)
    # Build context once so cache lookup uses the exact production as-of logic.
    analysis = run_rule_baseline_analysis(request, engine=engine, params=tuning.params)
    mode = db_analysis_type(request.mode)

    if not request.fresh:
        reusable = find_reusable_analysis(
            engine,
            ts_code=request.ts_code,
            mode=request.mode,
            data_cutoff_at=analysis.ctx.as_of.data_cutoff_at,
            param_hash=analysis.ctx.param_hash,
        )
        if reusable and reusable.get("output_html_path") and Path(str(reusable["output_html_path"])).exists():
            return StockEdgeRunResult(
                analysis=None,
                rendered=None,
                record_id=str(reusable["record_id"]),
                reused=True,
                reusable_record=reusable,
            )

    lock = acquire_or_wait(
        engine,
        ts_code=request.ts_code,
        analysis_type=mode,
        data_cutoff_date=analysis.ctx.as_of.as_of_trade_date,
        max_wait_sec=30,
    )
    if not lock.is_holder:
        reusable = find_reusable_analysis(
            engine,
            ts_code=request.ts_code,
            mode=request.mode,
            data_cutoff_at=analysis.ctx.as_of.data_cutoff_at,
            param_hash=analysis.ctx.param_hash,
        )
        if reusable and reusable.get("output_html_path") and Path(str(reusable["output_html_path"])).exists():
            return StockEdgeRunResult(
                analysis=None,
                rendered=None,
                record_id=str(reusable["record_id"]),
                reused=True,
                reusable_record=reusable,
            )
        raise RuntimeError(f"Stock Edge lock finished but no reusable report was found for {request.ts_code}.")

    assert lock.record_id is not None
    record_id = create_analysis_record(engine, analysis.ctx, record_id=lock.record_id)
    try:
        rendered = render_report_assets(analysis, settings)
        report = build_report_model(analysis)
        status_degraded_reasons = report["record_status_degraded_reasons"]
        decision_layer = report["decision_layer"]
        user_decision_layer = _user_decision_layer(decision_layer)
        insert_report_section(
            engine,
            record_id=record_id,
            section_key="01_decision_layer",
            section_order=1,
            content=user_decision_layer,
            status=_decision_layer_status(user_decision_layer),
            model_used=analysis.plan.probability.model_version,
            prompt_version="stock_edge_v2_2_decision_layer_v1",
        )
        insert_report_section(
            engine,
            record_id=record_id,
            section_key="02_data_freshness",
            section_order=2,
            content={"freshness": report["freshness"], "degraded_reasons": report["degraded_reasons"]},
            status="ok" if not report["degraded_reasons"] else "partial",
        )
        insert_report_section(
            engine,
            record_id=record_id,
            section_key="03_model_conflicts",
            section_order=3,
            content=decision_layer.get("model_conflicts") or {},
            status="ok",
            model_used="strategy_matrix_structured",
            prompt_version="model_conflict_structured_v1",
        )
        insert_report_section(
            engine,
            record_id=record_id,
            section_key="04_scenario_tree",
            section_order=4,
            content=report["scenario_tree"],
            status="ok" if report["scenario_tree"].get("available") else "partial",
            model_used=report["scenario_tree"].get("model_used"),
            prompt_version="scenario_tree_structured_v1",
        )
        insert_report_section(
            engine,
            record_id=record_id,
            section_key="05_legacy_trade_plan_audit",
            section_order=5,
            content={
                "plan": report["plan"],
                "legacy_40d_audit": decision_layer.get("legacy_40d_audit"),
                "note": "兼容审计数据，不作为 5/10/20 用户主决策。",
            },
            status="ok",
            model_used=analysis.plan.probability.model_version,
            prompt_version="legacy_trade_plan_audit_v1",
        )
        primary = decision_layer["decision_5d"]
        finalize_analysis_record(
            engine,
            record_id=record_id,
            status="partial" if status_degraded_reasons else "succeeded",
            output_html_path=rendered.html_path,
            conclusion_label=_conclusion_label(str(primary.get("decision"))),
            conclusion_text=f"{primary.get('horizon_label')} · {primary.get('user_facing_label')} · {primary.get('decision_summary')}",
            key_levels={"targets": [t for t in report["plan"].get("targets", [])], "entry_zone": report["plan"].get("entry_zone")},
            setup_match={"setup_type": analysis.plan.setup_type, "evidence": report["plan"].get("evidence", [])},
            invalidation={"stop": report["plan"].get("stop"), "vetoes": report["plan"].get("vetoes", [])},
            next_watch={"md_path": str(rendered.md_path), "t0_plan": report["plan"].get("t0_plan")},
            forecast={
                "decision_layer": decision_layer,
                "legacy_probability_audit": report["plan"].get("probability"),
            },
        )
        return StockEdgeRunResult(analysis=analysis, rendered=rendered, record_id=str(record_id), reused=False)
    except Exception as exc:
        finalize_analysis_record(engine, record_id=record_id, status="failed", error_summary=str(exc)[:500])
        raise
    finally:
        release_lock(engine, lock.lock_key)


def _conclusion_label(action: str) -> str:
    if action == "buy":
        return "high_watch"
    if action in {"watch", "wait", "hold"}:
        return "normal_watch"
    if action in {"exit", "sell", "reduce"}:
        return "cautious"
    return "avoid"


def _decision_layer_status(decision_layer: dict) -> str:
    statuses = [
        ((decision_layer.get(key) or {}).get("data_quality") or {}).get("status")
        for key in ("decision_5d", "decision_10d", "decision_20d")
    ]
    if any(status == "missing" for status in statuses):
        return "partial"
    if any(status == "partial" for status in statuses):
        return "partial"
    return "ok"


def _user_decision_layer(decision_layer: dict) -> dict:
    """Return the user-facing 5/10/20 decision section without legacy 40d audit."""
    return {
        key: decision_layer[key]
        for key in ("decision_5d", "decision_10d", "decision_20d", "model_conflicts", "version")
        if key in decision_layer
    }
