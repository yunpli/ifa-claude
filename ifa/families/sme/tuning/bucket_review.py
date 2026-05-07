"""Tuning review for SME market-structure buckets."""
from __future__ import annotations

import datetime as dt
from statistics import mean
from typing import Any

from ifa.families.sme.analysis.strategy_eval import summarize_strategy_eval


CORE_HORIZONS = (1, 3, 5, 10)


def _score(row: dict[str, Any]) -> float:
    return float(row.get("avg_signal_score") or 0.0)


def _success(row: dict[str, Any]) -> float:
    return float(row.get("avg_success_rate") or 0.0)


def _days(row: dict[str, Any]) -> int:
    return int(row.get("sample_days") or 0)


def build_bucket_review(
    engine,
    *,
    start: dt.date,
    end: dt.date,
    min_sample_days: int = 60,
) -> dict[str, Any]:
    """Build an actionable tuning artifact from realized bucket outcomes.

    This is deliberately outcome-first. It does not report how many rules or
    models exist; it reports which persisted decisions actually worked across
    mature labels and which definitions need to be reweighted or rebuilt.
    """
    rows = summarize_strategy_eval(engine, start=start, end=end)
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_bucket.setdefault(str(row["bucket"]), []).append(row)

    bucket_scores: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    for bucket, bucket_rows in sorted(by_bucket.items()):
        mature = [r for r in bucket_rows if int(r["horizon"]) in CORE_HORIZONS and _days(r) >= min_sample_days]
        thin = [r for r in bucket_rows if _days(r) < min_sample_days]
        if mature:
            avg_score = mean(_score(r) for r in mature)
            avg_success = mean(_success(r) for r in mature)
            best = max(mature, key=_score)
            worst = min(mature, key=_score)
        else:
            avg_score = 0.0
            avg_success = 0.0
            best = None
            worst = None
        direction = (mature[0].get("direction") if mature else (bucket_rows[0].get("direction") if bucket_rows else "unknown"))
        bucket_scores.append({
            "bucket": bucket,
            "direction": direction,
            "mature_horizons": [int(r["horizon"]) for r in mature],
            "thin_horizons": [int(r["horizon"]) for r in thin],
            "avg_signal_score": avg_score,
            "avg_success_rate": avg_success,
            "best_horizon": int(best["horizon"]) if best else None,
            "best_score": _score(best) if best else None,
            "worst_horizon": int(worst["horizon"]) if worst else None,
            "worst_score": _score(worst) if worst else None,
        })

        if not mature:
            recommendations.append({
                "bucket": bucket,
                "action": "hold_out",
                "reason": f"样本不足，未达到 {min_sample_days} 个成熟交易日。",
            })
        elif avg_score > 0.15 and avg_success >= 0.50:
            recommendations.append({
                "bucket": bucket,
                "action": "increase_weight",
                "reason": "多周期方向归一化分数为正且胜率达标。",
            })
        elif avg_score < -0.10:
            recommendations.append({
                "bucket": bucket,
                "action": "decrease_or_rebuild",
                "reason": "多周期方向归一化分数为负，当前定义与未来收益不匹配。",
            })
        else:
            recommendations.append({
                "bucket": bucket,
                "action": "keep_watch",
                "reason": "信号有边际信息但稳定性不足，暂不作为主优化目标。",
            })

    bucket_scores.sort(key=lambda x: x["avg_signal_score"], reverse=True)
    ready_horizons = sorted({
        int(row["horizon"])
        for row in rows
        if _days(row) >= min_sample_days
    })
    top_positive = [b for b in bucket_scores if b["avg_signal_score"] > 0]
    top_negative = [b for b in bucket_scores if b["avg_signal_score"] < 0]
    return {
        "status": "ok" if ready_horizons else "degraded",
        "start": start,
        "end": end,
        "min_sample_days": min_sample_days,
        "ready_horizons": ready_horizons,
        "bucket_scores": bucket_scores,
        "recommendations": recommendations,
        "next_tuning_decision": {
            "promote": [b["bucket"] for b in top_positive[:3]],
            "demote_or_rebuild": [b["bucket"] for b in top_negative[-3:]],
            "rationale": "优先调 bucket 排序/阈值，让正向桶进入客户重点方向，让负向桶进入风险或回避方向。",
        },
    }
