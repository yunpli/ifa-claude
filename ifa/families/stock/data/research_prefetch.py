"""Stock Edge orchestration for prerequisite Research deep reports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Engine

from ifa.config import Settings
from ifa.families.research.report import ensure_research_report
from ifa.families.stock.context import StockEdgeContext
from ifa.families.stock.data.availability import LoadResult
from ifa.families.stock.data.snapshot import StockEdgeSnapshot


@dataclass(frozen=True)
class ResearchPrefetchPlan:
    ts_codes: list[str]
    analysis_types: list[str]
    tier: str
    llm_for_target: bool
    llm_for_sector_peers: bool
    llm_timeout_seconds: float
    reuse: bool


def ensure_stock_edge_research_prefetch(
    ctx: StockEdgeContext,
    snapshot: StockEdgeSnapshot,
    *,
    engine: Engine,
    settings: Settings | None = None,
) -> LoadResult[dict[str, Any]]:
    """Ensure target and same-sector leader Research deep reports exist."""
    cfg = ctx.params.get("research_prefetch") or {}
    if not cfg.get("enabled", True):
        return LoadResult("research_prefetch", {"items": [], "failures": []}, "disabled", "ok", rows=0, required=False)
    plan = _build_plan(ctx, snapshot, cfg)
    if not plan.ts_codes:
        return LoadResult("research_prefetch", {"items": [], "failures": []}, "missing", "missing", rows=0, required=False, message="No Research prefetch targets.")

    items: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for ts_code in plan.ts_codes:
        for analysis_type in plan.analysis_types:
            try:
                ensured = ensure_research_report(
                    engine,
                    ts_code=ts_code,
                    analysis_type=analysis_type,
                    tier=plan.tier,
                    cutoff_date=ctx.as_of.as_of_trade_date,
                    settings=settings,
                    reuse=plan.reuse,
                    llm=_should_use_llm(ts_code, ctx.request.ts_code, plan),
                    llm_timeout_seconds=plan.llm_timeout_seconds,
                    pdf=False,
                    triggered_by="stock_edge",
                )
                items.append(ensured.to_dict())
            except Exception as exc:  # noqa: BLE001 - Research failure should degrade Stock Edge, not hide the trade plan.
                failures.append({
                    "ts_code": ts_code,
                    "analysis_type": analysis_type,
                    "tier": plan.tier,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                if not bool(cfg.get("fail_soft", True)):
                    raise
    status = "ok" if not failures else ("partial" if items else "missing")
    message = None if not failures else f"Research prefetch failed for {len(failures)} report(s)."
    return LoadResult(
        "research_prefetch",
        {"items": items, "failures": failures, "plan": plan.__dict__},
        "research.report_runs",
        status,
        rows=len(items),
        as_of=ctx.as_of.as_of_trade_date,
        required=False,
        message=message,
    )


def _build_plan(ctx: StockEdgeContext, snapshot: StockEdgeSnapshot, cfg: dict[str, Any]) -> ResearchPrefetchPlan:
    ts_codes: list[str] = []
    if cfg.get("include_target", True):
        ts_codes.append(ctx.request.ts_code)
    if cfg.get("include_sector_leaders", True):
        ts_codes.extend(_sector_leader_ts_codes(snapshot, max_peers=int(cfg.get("max_sector_peers", 4))))
    ts_codes = _dedupe(ts_codes)
    return ResearchPrefetchPlan(
        ts_codes=ts_codes,
        analysis_types=[str(v) for v in (cfg.get("analysis_types") or ["annual", "quarterly"])],
        tier=str(cfg.get("tier", "deep")),
        llm_for_target=bool(cfg.get("llm_for_target", cfg.get("llm", True))),
        llm_for_sector_peers=bool(cfg.get("llm_for_sector_peers", False)),
        llm_timeout_seconds=float(cfg.get("llm_timeout_seconds", 45.0)),
        reuse=bool(cfg.get("reuse", True)),
    )


def _should_use_llm(ts_code: str, target_ts_code: str, plan: ResearchPrefetchPlan) -> bool:
    if ts_code == target_ts_code:
        return plan.llm_for_target
    return plan.llm_for_sector_peers


def _sector_leader_ts_codes(snapshot: StockEdgeSnapshot, *, max_peers: int) -> list[str]:
    leaders = (snapshot.sector_membership.data or {}).get("sector_leaders") or {}
    out: list[str] = []
    for category in ["size", "momentum", "moneyflow", "ta"]:
        for row in leaders.get(category) or []:
            code = row.get("ts_code")
            if code and code != snapshot.ctx.request.ts_code:
                out.append(str(code))
            if len(_dedupe(out)) >= max_peers:
                return _dedupe(out)[:max_peers]
    return _dedupe(out)[:max_peers]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
