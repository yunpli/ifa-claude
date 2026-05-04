"""M2.3d · 5-dimension radar scoring.

Aggregates each factor family's results into a single 0-100 score per dimension,
suitable for radar charts and the overall verdict line.

Algorithm (L0+L1, deterministic):
  1. For each factor in a family, convert (status, peer_percentile) → factor_score (0-100):
       · Base score from status:  GREEN=80, YELLOW=50, RED=20, UNKNOWN=skip
       · Peer adjustment:         if peer_percentile present, blend 50/50:
                                    score = 0.5 × base + 0.5 × peer_percentile
         (so a GREEN factor in the bottom-quintile of peers settles at ~50,
          while a YELLOW factor in top-decile lifts to ~70 — gives industry
          context without losing the absolute red flag)
  2. Per-family score = weighted average of factor_scores using yaml `scoring:`
     weights, normalized over factors that *have* a score (so missing factors
     don't dilute by counting as 0). If <50% of weights present → score=None.
  3. Overall verdict (5-dim) = simple average of the 5 family scores,
     ignoring None. Status thresholds:
       · ≥ 70 → GREEN  (健康)
       · 50-70 → YELLOW (谨慎)
       · < 50 → RED    (高风险)

Why this shape:
  · Status carries a hard absolute floor — a RED factor can't be hidden by
    being merely "less bad than peers".
  · Peer percentile adds industry context — necessary because GPM thresholds
    that work for software don't work for steel.
  · 50/50 blend keeps both forces visible without complex tuning.
  · Ignoring missing factors during normalization avoids penalizing companies
    that legitimately don't have, e.g., pledge data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ifa.families.research.analyzer.factors import FactorResult, FactorStatus

# Family names matching SPECS / yaml scoring keys
FAMILY_ORDER = ["profitability", "growth", "cash_quality", "balance", "governance"]

FAMILY_LABEL_ZH: dict[str, str] = {
    "profitability": "盈利",
    "growth": "增长",
    "cash_quality": "现金",
    "balance": "结构",
    "governance": "治理",
}

# Status → base score
STATUS_BASE_SCORE: dict[FactorStatus, float | None] = {
    FactorStatus.GREEN: 80.0,
    FactorStatus.YELLOW: 50.0,
    FactorStatus.RED: 20.0,
    FactorStatus.UNKNOWN: None,
}

# Aggregate score → verdict status
def _verdict_status(score: float | None) -> FactorStatus:
    if score is None:
        return FactorStatus.UNKNOWN
    if score >= 70:
        return FactorStatus.GREEN
    if score >= 50:
        return FactorStatus.YELLOW
    return FactorStatus.RED


VERDICT_LABEL_ZH: dict[FactorStatus, str] = {
    FactorStatus.GREEN: "健康",
    FactorStatus.YELLOW: "谨慎",
    FactorStatus.RED: "高风险",
    FactorStatus.UNKNOWN: "数据不足",
}


@dataclass
class FactorScore:
    name: str
    base_score: float | None       # from status
    peer_pct: float | None         # 0-100 if available
    final_score: float | None      # blended
    weight: float                  # from yaml
    contributed: bool              # whether it counted toward family score


@dataclass
class FamilyScore:
    family: str
    score: float | None            # 0-100
    status: FactorStatus           # GREEN / YELLOW / RED / UNKNOWN
    label_zh: str
    weight_coverage: float         # 0-1, how much of the family's weight was filled
    factor_scores: list[FactorScore] = field(default_factory=list)


@dataclass
class ScoringResult:
    overall_score: float | None    # avg of 5 family scores
    overall_status: FactorStatus
    overall_label_zh: str
    families: dict[str, FamilyScore]   # keyed by family name
    radar: dict[str, float | None]     # for chart: family_label_zh → score

    def to_dict(self) -> dict:
        return {
            "overall_score": self.overall_score,
            "overall_status": self.overall_status.value,
            "overall_label_zh": self.overall_label_zh,
            "radar": dict(self.radar),
            "families": {
                fam: {
                    "score": fs.score,
                    "status": fs.status.value,
                    "label_zh": fs.label_zh,
                    "weight_coverage": fs.weight_coverage,
                    "factors": [
                        {
                            "name": f.name,
                            "base": f.base_score,
                            "peer_pct": f.peer_pct,
                            "final": f.final_score,
                            "weight": f.weight,
                            "contributed": f.contributed,
                        }
                        for f in fs.factor_scores
                    ],
                }
                for fam, fs in self.families.items()
            },
        }


# ─── Public API ───────────────────────────────────────────────────────────────

def score_results(
    results_by_family: dict[str, list[FactorResult]],
    params: dict,
    *,
    min_weight_coverage: float = 0.5,
) -> ScoringResult:
    """Aggregate factor results into 5-dim scores + overall verdict.

    Args:
        results_by_family: e.g. {"profitability": [...], "growth": [...], ...}
        params: parsed research_v2.2.yaml
        min_weight_coverage: skip a family if usable factor weight < this fraction.

    Returns:
        ScoringResult ready for radar rendering / report.
    """
    weights_cfg = params.get("scoring", {})
    families: dict[str, FamilyScore] = {}

    for fam in FAMILY_ORDER:
        results = results_by_family.get(fam, [])
        weight_map = weights_cfg.get(fam, {})
        families[fam] = _score_family(fam, results, weight_map, min_weight_coverage)

    family_scores = [fs.score for fs in families.values() if fs.score is not None]
    if family_scores:
        overall = sum(family_scores) / len(family_scores)
    else:
        overall = None

    overall_status = _verdict_status(overall)
    return ScoringResult(
        overall_score=overall,
        overall_status=overall_status,
        overall_label_zh=VERDICT_LABEL_ZH[overall_status],
        families=families,
        radar={FAMILY_LABEL_ZH[f]: families[f].score for f in FAMILY_ORDER},
    )


# ─── Internals ────────────────────────────────────────────────────────────────

def _score_family(
    family: str,
    results: list[FactorResult],
    weight_map: dict,
    min_weight_coverage: float,
) -> FamilyScore:
    factor_scores: list[FactorScore] = []
    weighted_sum = 0.0
    weight_used = 0.0
    weight_total = 0.0

    # Index by lowered name for matching against yaml keys (yaml uses snake_case
    # like "gpm", "npm", "revenue_yoy_pct" — we lowercase the FactorSpec.name).
    for r in results:
        name_key = _yaml_key_for(r.spec.name, family)
        weight = float(weight_map.get(name_key, 0.0))
        weight_total += weight
        base = STATUS_BASE_SCORE.get(r.status)
        peer_pct = r.peer_percentile

        if base is None and peer_pct is None:
            factor_scores.append(FactorScore(
                name=r.spec.name, base_score=None, peer_pct=None,
                final_score=None, weight=weight, contributed=False,
            ))
            continue

        if base is not None and peer_pct is not None:
            final = 0.5 * base + 0.5 * peer_pct
        elif base is not None:
            final = base
        else:
            final = peer_pct  # type: ignore[assignment]

        factor_scores.append(FactorScore(
            name=r.spec.name, base_score=base, peer_pct=peer_pct,
            final_score=final, weight=weight,
            contributed=weight > 0,
        ))
        if weight > 0:
            weighted_sum += weight * final
            weight_used += weight

    coverage = (weight_used / weight_total) if weight_total > 0 else 0.0

    if weight_used <= 0 or coverage < min_weight_coverage:
        score: float | None = None
    else:
        score = weighted_sum / weight_used

    status = _verdict_status(score)
    return FamilyScore(
        family=family,
        score=score,
        status=status,
        label_zh=FAMILY_LABEL_ZH.get(family, family),
        weight_coverage=coverage,
        factor_scores=factor_scores,
    )


def _yaml_key_for(factor_name: str, family: str) -> str:
    """Map FactorSpec.name → yaml weight key.

    The yaml uses snake_case, factor names use UPPER_CASE. Most are direct
    lowercase, but a handful need explicit mapping (e.g. DUPONT_NPM_GAP isn't
    weighted; FORECAST_ACH → forecast_achievement_pct).
    """
    direct = factor_name.lower()
    overrides: dict[tuple[str, str], str] = {
        ("growth", "revenue_yoy"):     "revenue_yoy_pct",
        ("growth", "n_income_yoy"):    "n_income_yoy_pct",
        ("growth", "revenue_cagr"):    "revenue_cagr_3y_pct",
        ("growth", "forecast_ach"):    "forecast_achievement_pct",
        ("cash_quality", "fcf"):       "fcf_yuan",
        ("cash_quality", "ar_growth_rev"):    "ar_growth_to_revenue_growth",
        ("cash_quality", "inv_growth_cost"):  "inventory_growth_to_cost_growth",
        ("cash_quality", "ccc_change"):       "ccc_days_yoy_change",
        ("balance", "debt_to_assets"):  "debt_to_assets_pct",
        ("balance", "goodwill_eq"):     "goodwill_to_equity_pct",
        ("balance", "pledge_ratio"):    "pledge_ratio_pct",
        ("governance", "holdertrade_count"): "holdertrade_decreasing_count_12m",
        ("governance", "holdertrade_share"): "holdertrade_decreasing_share_pct",
        ("governance", "audit_standard"):    "audit_non_standard_critical",
        ("governance", "manager_turnover"):  "manager_turnover_12m_pct",
        ("governance", "irm_reply_rate"):    "irm_no_reply_rate_pct",
        ("governance", "disclosure_delay"):  "disclosure_delay_days",
    }
    return overrides.get((family, direct), direct)
