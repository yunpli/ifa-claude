"""TA perspective adapter for Stock Edge diagnostic reports."""
from __future__ import annotations

from typing import Any

from ifa.families.stock.diagnostic.models import EvidencePoint, PerspectiveEvidence

from .common import freshness_from_points, timed, to_float


def collect(*, snapshot: Any) -> PerspectiveEvidence:
    return timed("ta", lambda: _collect(snapshot))


def _collect(snapshot: Any) -> PerspectiveEvidence:
    data = snapshot.ta_context.data or {}
    candidates = data.get("candidates") or []
    warnings = data.get("warnings") or []
    regime = data.get("regime") or {}
    metrics = data.get("setup_metrics") or []
    points: list[EvidencePoint] = []
    setup_names = sorted({str(row.get("setup_label") or row.get("setup_name")) for row in candidates if row.get("setup_label") or row.get("setup_name")})
    warning_names = sorted({str(row.get("setup_label") or row.get("setup_name")) for row in warnings if row.get("setup_label") or row.get("setup_name")})
    if candidates or warnings:
        latest_signal_date = max(
            [str(row.get("trade_date")) for row in [*candidates, *warnings] if row.get("trade_date")],
            default=None,
        )
        points.append(EvidencePoint(
            "TA rollup",
            {"candidate_count": len(candidates), "warning_count": len(warnings), "metric_count": len(metrics)},
            "ta.candidates_daily/ta.warnings_daily/ta.setup_metrics_daily",
            latest_signal_date,
            note=f"setups={', '.join(setup_names[:5]) or '-'} warnings={', '.join(warning_names[:5]) or '-'}",
        ))
    for row in candidates[:5]:
        points.append(EvidencePoint(
            row.get("setup_label") or row.get("setup_name"),
            row.get("final_score"),
            "ta.candidates_daily",
            str(row.get("trade_date")),
            note=f"rank={row.get('rank')} stars={row.get('star_rating')} entry={row.get('entry_price')} stop={row.get('stop_loss')}",
        ))
    for row in warnings[:3]:
        points.append(EvidencePoint(
            row.get("setup_label") or row.get("setup_name"),
            row.get("score"),
            "ta.warnings_daily",
            str(row.get("trade_date")),
            note="risk warning",
        ))
    if regime:
        points.append(EvidencePoint("market TA regime", regime.get("regime"), "ta.regime_daily", str(regime.get("trade_date")), note=f"confidence={regime.get('confidence')}"))
    for row in metrics[:5]:
        points.append(EvidencePoint(
            f"{row.get('setup_name')} 60d edge",
            to_float(row.get("combined_score_60d")) if row.get("combined_score_60d") is not None else to_float(row.get("winrate_60d")),
            "ta.setup_metrics_daily",
            str(row.get("trade_date")),
            note=(
                f"winrate_60d={row.get('winrate_60d')} avg_return_60d={row.get('avg_return_60d')} "
                f"pl_ratio_60d={row.get('pl_ratio_60d')} decay={row.get('decay_score')}"
            ),
        ))

    view = "neutral"
    if candidates:
        view = "positive"
    if warnings and not candidates:
        view = "risk"
    if not points:
        return PerspectiveEvidence("ta", "TA", "unavailable", "unknown", "未找到目标股近期 TA setup；按中性/信号不足处理。", missing=["ta.candidates_daily", "ta.warnings_daily"])
    summary = "TA 有近期多头 setup。"
    if candidates and metrics:
        summary = "TA 有近期多头 setup，并已补充 setup_metrics_daily 历史 edge。"
    elif not candidates:
        summary = "TA 未见多头 setup，但存在风险形态。"
    return PerspectiveEvidence("ta", "TA", "available", view, summary, points=points, freshness=freshness_from_points(points))  # type: ignore[arg-type]
