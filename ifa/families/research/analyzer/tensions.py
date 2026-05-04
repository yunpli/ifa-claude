"""§09 — Narrative consistency / cross-cutting tensions.

The 5 family scoring sections each tell a local story. This module looks
across those stories and flags **tensions** — combinations that are individually
fine but jointly suspicious. Examples:

  · Profit margins are GREEN but CFO is RED → profit not turning into cash
  · Revenue growing fast AND inventory growing even faster → channel stuffing risk
  · ROE is RED but peer P95 → leading a struggling industry (vs. failing)
  · NPM normal but扣非 NPM 49% lower → profit relies on non-recurring items

Why this is a separate section, not just bigger watchpoints:
  · Watchpoints come from individual factors and their notes; tensions are
    cross-factor combinations that no single factor can flag.
  · Tensions are deterministic (rule-based); watchpoints are LLM-synthesized.
  · A reader who only has 30 seconds wants to see "the headline risk that
    five sections together are saying" — that's tensions.

Output is a list of `Tension` rows, sorted by severity. Builder packages them
into a `research_tensions` section. Plain text only (no LLM dependency).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ifa.families.research.analyzer.factors import FactorResult, FactorStatus

Severity = Literal["high", "medium", "low"]


@dataclass
class Tension:
    code: str                  # stable id like 'profit_quality_mismatch'
    severity: Severity
    title: str                 # short headline (≤16 chars)
    description: str           # 50-150 chars; explains the WHY
    evidence: list[str] = field(default_factory=list)  # factor names cited

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "evidence": list(self.evidence),
        }


def detect_tensions(
    results_by_family: dict[str, list[FactorResult]],
) -> list[Tension]:
    """Run all rule-based tension detectors. Returns severity-sorted list."""
    by_name: dict[str, FactorResult] = {
        r.spec.name: r
        for results in results_by_family.values()
        for r in results
    }

    tensions: list[Tension] = []
    for fn in (
        _t_profit_quality_mismatch,
        _t_growth_funded_by_debt,
        _t_forecast_volatility,
        _t_inventory_outpaces_sales,
        _t_earnings_via_dedt_gap,
        _t_industry_leader_in_decline,
        _t_industry_laggard_in_strength,
        _t_insider_selling_during_burn,
    ):
        try:
            t = fn(by_name)
            if t is not None:
                tensions.append(t)
        except Exception:
            # A bad detector shouldn't kill the whole pass.
            continue

    sev_rank = {"high": 0, "medium": 1, "low": 2}
    tensions.sort(key=lambda t: (sev_rank[t.severity], t.code))
    return tensions


# ─── Detectors ────────────────────────────────────────────────────────────────

def _val(r: FactorResult | None) -> float | None:
    if r is None or r.value is None:
        return None
    try:
        return float(r.value)
    except (TypeError, ValueError):
        return None


def _fmt_ratio(unit_map: dict[str, str], factor_code: str, val: float) -> str:
    """Format a factor value with the right unit (% / x / pp / 元 / etc)."""
    unit = unit_map.get(factor_code, "")
    if unit == "%":
        return f"{val:.1f}%"
    if unit == "pp":
        return f"{val:+.1f}pp"
    if unit == "x":
        return f"{val:.2f}x"
    if unit == "天":
        return f"{val:.1f} 天"
    if unit == "次":
        return f"{int(val)} 次"
    if unit == "元":
        if abs(val) >= 1e8:
            return f"{val/1e8:.2f} 亿元"
        return f"{val:.0f} 元"
    return f"{val:.2f}"


def _t_profit_quality_mismatch(b: dict) -> Tension | None:
    """NPM GREEN + CFO_TO_NI RED → profit not turning into cash."""
    npm = b.get("NPM")
    cfo_ni = b.get("CFO_TO_NI")
    if npm is None or cfo_ni is None:
        return None
    if npm.status == FactorStatus.GREEN and cfo_ni.status == FactorStatus.RED:
        v_npm = _val(npm)
        v_cfo = _val(cfo_ni)
        return Tension(
            code="profit_quality_mismatch",
            severity="high",
            title="利润未转现",
            description=(
                f"净利率 {v_npm:.1f}% 处绿灯，但经营现金流/净利 {v_cfo:.2f}x "
                "为红灯。账面利润未有效转化为现金，可能依赖应收账款或库存放大。"
            ),
            evidence=["NPM", "CFO_TO_NI"],
        )
    return None


def _t_growth_funded_by_debt(b: dict) -> Tension | None:
    """High revenue growth + debt ratio rising significantly → leverage-fueled."""
    rev_yoy = b.get("REVENUE_YOY")
    ibd_yoy = b.get("IBD_SHARE_YOY")
    debt = b.get("DEBT_TO_ASSETS")
    if rev_yoy is None or _val(rev_yoy) is None:
        return None
    rev_v = _val(rev_yoy)
    if rev_v < 20:
        return None  # only flag fast growth
    ibd_v = _val(ibd_yoy)
    debt_v = _val(debt)
    if (ibd_v is not None and ibd_v > 5) or (debt_v is not None and debt_v > 65):
        return Tension(
            code="growth_funded_by_debt",
            severity="medium",
            title="增长靠杠杆",
            description=(
                f"营收同比 {rev_v:.1f}% 较快，但杠杆同步上升"
                + (f"（资产负债率同比+{ibd_v:.1f}pp）" if ibd_v else "")
                + (f"，当前资产负债率 {debt_v:.1f}%" if debt_v else "")
                + "。增长可持续性需关注现金回笼与债务到期节奏。"
            ),
            evidence=[n for n in ["REVENUE_YOY", "IBD_SHARE_YOY", "DEBT_TO_ASSETS"]
                      if b.get(n) is not None],
        )
    return None


def _t_forecast_volatility(b: dict) -> Tension | None:
    """Forecast achievement way off → guidance reliability concern."""
    fc = b.get("FORECAST_ACH")
    if fc is None or _val(fc) is None:
        return None
    v = _val(fc)
    if v < 70 or v > 150:
        direction = "明显低于预告" if v < 70 else "明显高于预告"
        return Tension(
            code="forecast_volatility",
            severity="medium" if (60 <= v <= 200) else "high",
            title="预告偏离大",
            description=(
                f"业绩预告达成率 {v:.0f}%，{direction}。"
                "可能反映经营波动或前期预判失准；下次预告与实际差距是关键观察点。"
            ),
            evidence=["FORECAST_ACH"],
        )
    return None


def _t_inventory_outpaces_sales(b: dict) -> Tension | None:
    """Inventory growing significantly faster than cost → channel stuffing risk."""
    inv = b.get("INV_GROWTH_COST")
    if inv is None or _val(inv) is None:
        return None
    v = _val(inv)
    if v < 1.5:
        return None
    rev_yoy = _val(b.get("REVENUE_YOY"))
    return Tension(
        code="inventory_outpaces_sales",
        severity="high" if v >= 2.5 else "medium",
        title="库存压货",
        description=(
            f"存货增速/成本增速 {v:.2f}x"
            + (f"，营收同比仅 {rev_yoy:.1f}%" if rev_yoy is not None else "")
            + "。库存扩张明显快于消化速度，可能埋下减值或去库压力。"
        ),
        evidence=[n for n in ["INV_GROWTH_COST", "REVENUE_YOY"]
                  if b.get(n) is not None],
    )


def _t_earnings_via_dedt_gap(b: dict) -> Tension | None:
    """NPM and扣非 NPM diverge >30% → reliance on non-recurring."""
    npm = b.get("NPM")
    npm_dedt = b.get("NPM_DEDT")
    if npm is None or npm_dedt is None:
        return None
    v_n = _val(npm)
    v_d = _val(npm_dedt)
    if v_n is None or v_d is None or v_n == 0:
        return None
    gap_pct = abs(v_n - v_d) / abs(v_n) * 100
    if gap_pct < 30:
        return None
    severity: Severity = "high" if gap_pct >= 70 else "medium"
    return Tension(
        code="earnings_via_dedt_gap",
        severity=severity,
        title="非经常性依赖",
        description=(
            f"净利率 {v_n:.1f}% 与扣非净利率 {v_d:.1f}% 差距 {gap_pct:.0f}%。"
            "盈利对非经常性损益（政府补贴、资产处置、投资收益）依赖偏高，"
            "扣除后的可持续盈利能力较表观为弱。"
        ),
        evidence=["NPM", "NPM_DEDT"],
    )


def _t_industry_leader_in_decline(b: dict) -> Tension | None:
    """Many factors RED absolute but P >= 70 in cohort → leader of a struggling sector."""
    name_to_unit = {n: r.spec.unit for n, r in b.items()}
    candidates = []
    for r in b.values():
        if (r.status == FactorStatus.RED
                and r.peer_percentile is not None
                and r.peer_percentile >= 70
                and r.spec.industry_sensitive):
            candidates.append((r.spec.name, r.spec.display_name_zh,
                               _val(r), float(r.peer_percentile)))
    if len(candidates) < 2:
        return None
    candidates.sort(key=lambda x: -x[3])  # most striking first (highest peer_pct)
    examples = candidates[:2]
    ex_str = "、".join(
        f"{name}{_fmt_ratio(name_to_unit, code, val)}（同业 P{int(p)}）"
        for code, name, val, p in examples
    )
    return Tension(
        code="industry_leader_in_decline",
        severity="medium",
        title="行业承压公司领先",
        description=(
            f"多项因子绝对水平偏弱但同业领先：{ex_str}。"
            "更可能是行业整体性问题，而非公司个体劣势；行业修复时该公司或先回血。"
        ),
        evidence=[c[0] for c in candidates[:3]],
    )


def _t_industry_laggard_in_strength(b: dict) -> Tension | None:
    """Many factors GREEN absolute but P <= 30 in cohort → laggard in strong sector."""
    name_to_unit = {n: r.spec.unit for n, r in b.items()}
    candidates = []
    for r in b.values():
        if (r.status == FactorStatus.GREEN
                and r.peer_percentile is not None
                and r.peer_percentile <= 30
                and r.spec.industry_sensitive):
            candidates.append((r.spec.name, r.spec.display_name_zh,
                               _val(r), float(r.peer_percentile)))
    if len(candidates) < 2:
        return None
    candidates.sort(key=lambda x: x[3])  # most striking first (lowest peer_pct)
    examples = candidates[:2]
    ex_str = "、".join(
        f"{name}{_fmt_ratio(name_to_unit, code, val)}（同业 P{int(p)}）"
        for code, name, val, p in examples
    )
    return Tension(
        code="industry_laggard_in_strength",
        severity="medium",
        title="同业相对垫底",
        description=(
            f"多项因子绝对水平尚可但同业末段：{ex_str}。"
            "虽达健康阈值，但在同业中相对靠后；"
            "若行业修复，该公司难以独享贝塔，需观察是否存在结构性能力短板。"
        ),
        evidence=[c[0] for c in candidates[:3]],
    )


def _t_insider_selling_during_burn(b: dict) -> Tension | None:
    """Significant 12-month decreasing holders + negative CFO → red flag combination."""
    ht_share = b.get("HOLDERTRADE_SHARE")
    cfo_ni = b.get("CFO_TO_NI")
    if ht_share is None or cfo_ni is None:
        return None
    v_share = _val(ht_share)
    v_cfo = _val(cfo_ni)
    if v_share is None or v_share < 3:
        return None
    if v_cfo is None or v_cfo >= 0:
        return None
    return Tension(
        code="insider_selling_during_burn",
        severity="high",
        title="减持叠加失血",
        description=(
            f"12 月内重要股东减持占总股本 {v_share:.1f}%，"
            f"同期 CFO/NI {v_cfo:.2f}x（经营现金流为负）。"
            "在公司现金压力期间出现明显减持，是治理与基本面共振的信号。"
        ),
        evidence=["HOLDERTRADE_SHARE", "CFO_TO_NI"],
    )
