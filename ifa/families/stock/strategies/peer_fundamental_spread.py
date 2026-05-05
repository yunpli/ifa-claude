"""Same-sector leader fundamental/market spread signal."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any


@dataclass(frozen=True)
class PeerFundamentalSpreadProfile:
    available: bool
    reason: str
    peer_count: int
    target_in_leader_set: bool
    fundamental_percentile: float | None
    quality_percentile: float | None
    growth_percentile: float | None
    cash_percentile: float | None
    leverage_percentile: float | None
    size_percentile: float | None
    momentum_percentile: float | None
    valuation_discount_score: float | None
    research_coverage_score: float
    score: float
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_peer_fundamental_spread_profile(
    sector_membership: dict[str, Any] | None,
    research_lineup: dict[str, Any] | None,
    *,
    ts_code: str,
    params: dict[str, Any],
) -> PeerFundamentalSpreadProfile:
    """Compare target with visible same-SW-L2 peers, led by financial statements."""
    if not params.get("enabled", True):
        return _missing("peer fundamental spread disabled")
    leaders = (sector_membership or {}).get("sector_leaders") or {}
    peers = _unique_peers(leaders)
    min_peers = int(params.get("min_peers", 3))
    if len(peers) < min_peers:
        return _missing(f"同行龙头样本 {len(peers)} 个，低于 {min_peers} 个。")
    target = next((row for row in peers if row.get("ts_code") == ts_code), None)
    if target is None:
        coverage = _research_coverage_score(research_lineup, params)
        return PeerFundamentalSpreadProfile(
            available=True,
            reason="目标股未进入当前同板块多元龙头样本，按相对弱势处理。",
            peer_count=len(peers),
            target_in_leader_set=False,
            fundamental_percentile=None,
            quality_percentile=None,
            growth_percentile=None,
            cash_percentile=None,
            leverage_percentile=None,
            size_percentile=None,
            momentum_percentile=None,
            valuation_discount_score=None,
            research_coverage_score=coverage,
            score=round(-0.12 + 0.10 * coverage, 4),
            evidence={"leaders": _leader_names(peers)},
        )

    financial_rows = _build_financial_rows(peers, (sector_membership or {}).get("peer_fundamentals") or [])
    target_financial = next((row for row in financial_rows if row.get("ts_code") == ts_code), None)
    fundamental_pct = _percentile(financial_rows, target_financial, "fundamental_score") if target_financial else None
    quality_pct = _percentile(financial_rows, target_financial, "quality_score") if target_financial else None
    growth_pct = _percentile(financial_rows, target_financial, "growth_score") if target_financial else None
    cash_pct = _percentile(financial_rows, target_financial, "cash_score") if target_financial else None
    leverage_pct = _percentile(financial_rows, target_financial, "leverage_score") if target_financial else None
    size_pct = _percentile(peers, target, "total_mv")
    momentum_pct = _composite_momentum_percentile(peers, target)
    valuation = _valuation_discount_score(peers, target, params)
    coverage = _research_coverage_score(research_lineup, params)
    fundamental_component = _center(fundamental_pct) if fundamental_pct is not None else 0.0
    size_component = _center(size_pct) if size_pct is not None else 0.0
    momentum_component = _center(momentum_pct) if momentum_pct is not None else 0.0
    valuation_component = valuation if valuation is not None else 0.0
    score = (
        float(params.get("financial_weight", 0.58)) * fundamental_component
        + float(params.get("valuation_weight", 0.16)) * valuation_component
        + float(params.get("research_coverage_weight", 0.12)) * (coverage * 2.0 - 1.0)
        + float(params.get("size_weight", 0.08)) * size_component
        + float(params.get("momentum_weight", 0.06)) * momentum_component
    )
    score = max(-0.42, min(0.42, score))
    return PeerFundamentalSpreadProfile(
        available=True,
        reason="已完成同板块财报质量为主、估值/市值/动量为辅的对比。",
        peer_count=len(peers),
        target_in_leader_set=True,
        fundamental_percentile=round(fundamental_pct, 4) if fundamental_pct is not None else None,
        quality_percentile=round(quality_pct, 4) if quality_pct is not None else None,
        growth_percentile=round(growth_pct, 4) if growth_pct is not None else None,
        cash_percentile=round(cash_pct, 4) if cash_pct is not None else None,
        leverage_percentile=round(leverage_pct, 4) if leverage_pct is not None else None,
        size_percentile=round(size_pct, 4) if size_pct is not None else None,
        momentum_percentile=round(momentum_pct, 4) if momentum_pct is not None else None,
        valuation_discount_score=round(valuation, 4) if valuation is not None else None,
        research_coverage_score=round(coverage, 4),
        score=round(score, 4),
        evidence={
            "target": {
                "ts_code": target.get("ts_code"),
                "name": target.get("name"),
                "total_mv": target.get("total_mv"),
                "pe_ttm": target.get("pe_ttm"),
                "pb": target.get("pb"),
                "return_5d_pct": target.get("return_5d_pct"),
                "return_10d_pct": target.get("return_10d_pct"),
                "return_15d_pct": target.get("return_15d_pct"),
            },
            "financial_rows": financial_rows,
            "leaders": _leader_names(peers),
        },
    )


def _unique_peers(leaders: dict[str, Any]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rows in leaders.values():
        for row in rows or []:
            code = row.get("ts_code")
            if not code:
                continue
            if code not in out:
                out[str(code)] = dict(row)
            else:
                out[str(code)].update({k: v for k, v in row.items() if v is not None})
    return list(out.values())


def _build_financial_rows(peers: list[dict[str, Any]], factor_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    factors: dict[tuple[str, str], dict[str, float]] = {}
    periods: dict[tuple[str, str], str] = {}
    for row in factor_rows:
        code = str(row.get("ts_code") or "")
        period_type = str(row.get("period_type") or "")
        factor = str(row.get("factor_name") or "")
        if not code or period_type not in {"annual", "quarterly"}:
            continue
        key = (code, period_type)
        period = str(row.get("period") or "")
        if period >= periods.get(key, ""):
            periods[key] = period
            factors.setdefault(key, {})[factor] = _float(row.get("value")) or 0.0
    rows = []
    for peer in peers:
        code = str(peer.get("ts_code") or "")
        annual = factors.get((code, "annual"), {})
        quarterly = factors.get((code, "quarterly"), {})
        row = {
            "ts_code": code,
            "quality_score": _avg_present([
                annual.get("ROE"),
                quarterly.get("ROE"),
            ]),
            "growth_score": _avg_present([
                annual.get("营收同比增速"),
                quarterly.get("营收同比增速"),
            ]),
            "cash_score": _avg_present([
                annual.get("CFO/NI"),
                quarterly.get("CFO/NI"),
            ]),
            "leverage_raw": _avg_present([
                annual.get("资产负债率"),
                quarterly.get("资产负债率"),
            ]),
            "pe_ttm": _float(peer.get("pe_ttm")),
            "pb": _float(peer.get("pb")),
        }
        rows.append(row)
    for row in rows:
        row["leverage_score"] = _percentile(rows, row, "leverage_raw", reverse=True)
        row["valuation_score"] = _avg_present([
            _percentile(rows, row, "pe_ttm", reverse=True),
            _percentile(rows, row, "pb", reverse=True),
        ])
    for row in rows:
        quality = _percentile(rows, row, "quality_score")
        growth = _percentile(rows, row, "growth_score")
        cash = _percentile(rows, row, "cash_score")
        leverage = row.get("leverage_score")
        valuation = row.get("valuation_score")
        statement_components = [quality, growth, cash, leverage]
        row["fundamental_score"] = (
            _weighted_avg([
                (quality, 0.32),
                (growth, 0.24),
                (cash, 0.22),
                (leverage, 0.14),
                (valuation, 0.08),
            ])
            if any(value is not None for value in statement_components)
            else None
        )
    return rows


def _percentile(peers: list[dict[str, Any]], target: dict[str, Any] | None, key: str, *, reverse: bool = False) -> float | None:
    if target is None:
        return None
    target_value = _float(target.get(key))
    values = [_float(row.get(key)) for row in peers]
    values = [v for v in values if v is not None]
    if target_value is None or len(values) < 2:
        return None
    rank = sum(1 for value in values if value <= target_value) / len(values)
    return 1.0 - rank + 1.0 / len(values) if reverse else rank


def _composite_momentum_percentile(peers: list[dict[str, Any]], target: dict[str, Any]) -> float | None:
    scores: list[tuple[dict[str, Any], float]] = []
    for row in peers:
        parts = []
        for key, weight in [("return_5d_pct", 0.45), ("return_10d_pct", 0.35), ("return_15d_pct", 0.20)]:
            value = _float(row.get(key))
            if value is not None:
                parts.append((value, weight))
        if parts:
            total_w = sum(w for _, w in parts)
            scores.append((row, sum(v * w for v, w in parts) / total_w))
    target_score = next((score for row, score in scores if row.get("ts_code") == target.get("ts_code")), None)
    if target_score is None or len(scores) < 2:
        return None
    values = [score for _, score in scores]
    return sum(1 for value in values if value <= target_score) / len(values)


def _valuation_discount_score(peers: list[dict[str, Any]], target: dict[str, Any], params: dict[str, Any]) -> float | None:
    components = []
    for key in ["pe_ttm", "pb"]:
        target_value = _float(target.get(key))
        values = [_float(row.get(key)) for row in peers]
        values = [v for v in values if v is not None and v > 0]
        if target_value is None or target_value <= 0 or len(values) < 2:
            continue
        peer_median = median(values)
        discount = math.log(max(peer_median, 1e-6) / max(target_value, 1e-6))
        components.append(max(-1.0, min(1.0, discount / float(params.get("valuation_log_scale", 0.65)))))
    if not components:
        return None
    return sum(components) / len(components)


def _research_coverage_score(research_lineup: dict[str, Any] | None, params: dict[str, Any]) -> float:
    annual = len((research_lineup or {}).get("annual_factors") or [])
    quarterly = len((research_lineup or {}).get("quarterly_factors") or [])
    reports = len((research_lineup or {}).get("recent_research_reports") or [])
    full = max(float(params.get("full_research_items", 18.0)), 1.0)
    return max(0.0, min(1.0, (annual + quarterly + 2 * reports) / full))


def _leader_names(peers: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("name") or row.get("ts_code")) for row in peers[:8]]


def _center(value: float | None) -> float:
    if value is None:
        return 0.0
    return value * 2.0 - 1.0


def _avg_present(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _weighted_avg(items: list[tuple[float | None, float]]) -> float | None:
    present = [(value, weight) for value, weight in items if value is not None]
    if not present:
        return None
    weight_sum = sum(weight for _, weight in present)
    return sum(float(value) * weight for value, weight in present) / weight_sum


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _missing(reason: str) -> PeerFundamentalSpreadProfile:
    return PeerFundamentalSpreadProfile(
        available=False,
        reason=reason,
        peer_count=0,
        target_in_leader_set=False,
        fundamental_percentile=None,
        quality_percentile=None,
        growth_percentile=None,
        cash_percentile=None,
        leverage_percentile=None,
        size_percentile=None,
        momentum_percentile=None,
        valuation_discount_score=None,
        research_coverage_score=0.0,
        score=0.0,
        evidence={},
    )
