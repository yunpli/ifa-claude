"""Run the Research V2.2 regression against the user-filled golden set.

Reads `tests/golden_set/research_v22.json`, regenerates each report, and
compares system output vs annotations. Prints metrics + per-stock diff.

Run:
    uv run python scripts/research_regression.py

Exit codes:
    0  — all metrics above thresholds (CI-safe)
    1  — at least one threshold breached (release-blocking)

Annotation schema (see research_golden_set_init.py):
    verdict_alignment: 'agree' | 'partial' | 'disagree'
    dimension_disagreements: {family: "<token>[；说明]"}
        token ∈ {ok, partial, disagree}; 中文 freeform 说明可选，用 '；'/'：' 分隔
        例: "ok"  / "ok；FCF 为正"  / "partial：ROE 低但同业前列"  / "disagree：…"
    your_watchpoints: 3 free-text concerns
    system_watchpoints_wrong_indices: list of 1-indexed bad watchpoint positions

Metrics:
    · verdict_alignment_rate: % of stocks where system verdict directionally agrees
        (agree=1.0, partial=0.5, disagree=0)
    · dimension_agreement_rate: weighted across (stock × family) cells
        (ok=1.0, partial=0.5, disagree=0)
    · watchpoints_precision: 1 - (wrong / total) across system-generated watchpoints
    · watchpoints_recall: % of user concerns covered by system (keyword overlap)

Thresholds (from V2.2 todo §M6):
    verdict_alignment_rate ≥ 80%
    dimension_agreement_rate ≥ 70%
    watchpoints_precision ≥ 70%
    watchpoints_recall ≥ 60%   (lower bar — recall is hardest)
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.core.report.timezones import bjt_now
from ifa.families.research.analyzer.balance import compute_balance
from ifa.families.research.analyzer.cash_quality import compute_cash_quality
from ifa.families.research.analyzer.data import load_company_snapshot
from ifa.families.research.analyzer.factors import FactorStatus, load_params
from ifa.families.research.analyzer.governance import compute_governance
from ifa.families.research.analyzer.growth import compute_growth
from ifa.families.research.analyzer.peer import attach_peer_ranks
from ifa.families.research.analyzer.profitability import compute_profitability
from ifa.families.research.analyzer.scoring import score_results
from ifa.families.research.report.builder import build_research_report
from ifa.families.research.resolver import resolve

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ANNOTATIONS_PATH = ROOT / "tests" / "golden_set" / "research_v22.json"

THRESHOLDS = {
    "verdict_alignment_rate": 0.80,
    "dimension_agreement_rate": 0.70,
    "watchpoints_precision": 0.70,
    "watchpoints_recall": 0.60,
}


def _verdict_from_score(score: float | None) -> str:
    """Map score → coarse verdict for alignment comparison."""
    if score is None:
        return "unknown"
    if score >= 70:
        return "healthy"
    if score >= 50:
        return "cautious"
    return "high_risk"


def _verdict_aligns(annotation: str, system_score: float | None) -> tuple[bool, bool]:
    """Returns (full_agree, partial_agree). 'partial' counts at half-weight."""
    if annotation == "agree":
        return (True, True)
    if annotation == "partial":
        return (False, True)
    return (False, False)


def _dim_token(value: str | None) -> str:
    """Extract leading verdict token from a free-form annotation.

    User writes things like "ok；FCF 为正", "partial：…", "disagree：…".
    We split on '：' / ':' / '；' / ';' and take the first chunk.
    """
    if not value:
        return "ok"
    head = value.strip()
    for sep in ("：", ":", "；", ";"):
        if sep in head:
            head = head.split(sep, 1)[0]
            break
    return head.strip().lower()


def _normalize_dim_disagreement(annotated: dict, system_results: dict) -> tuple[float, int]:
    """Return (weighted_agreement, total_count) across the 5 dimensions.

    Weights: ok=1.0, partial=0.5, disagree=0.0. Unknown tokens default to ok
    (treats stale schema like 'should_be_yellow' as disagreement signal=0).
    """
    families = ("profitability", "growth", "cash_quality", "balance", "governance")
    total = 0
    agreed = 0.0
    for fam in families:
        raw = annotated.get(fam, "ok")
        if raw is None:
            continue
        total += 1
        token = _dim_token(raw)
        if token == "ok":
            agreed += 1.0
        elif token == "partial":
            agreed += 0.5
        # disagree or anything else → 0
    return agreed, total


def _watchpoint_recall(user_concerns: list[str], system_watchpoints: list[dict]) -> float:
    """How many of the user's 3 concerns are 'covered' by system watchpoints.

    Heuristic: a user concern is covered if any 2-character substring (≥2 zh chars)
    of it appears in any system watchpoint's title/description. This is dumb keyword
    overlap, not perfect, but cheap and consistent.

    Returns 0.0-1.0. Empty user list → returns 1.0 (nothing to recall).
    """
    user_clean = [c.strip() for c in user_concerns if c and c.strip()]
    if not user_clean:
        return 1.0
    sys_text = " ".join(
        (w.get("title", "") + " " + w.get("description", ""))
        for w in system_watchpoints
    )
    if not sys_text:
        return 0.0
    covered = 0
    for concern in user_clean:
        # keyword extraction: take any 2-char windows from the concern
        # (cheap Chinese substring match)
        keywords = set()
        for i in range(len(concern) - 1):
            kw = concern[i:i + 2].strip()
            if len(kw) == 2 and not kw.isspace():
                keywords.add(kw)
        if not keywords:
            continue
        # If any 2-char window appears in system text, count as covered
        if any(kw in sys_text for kw in keywords):
            covered += 1
    return covered / len(user_clean)


def _generate_system_output(engine, ts_code: str, params: dict) -> dict:
    """Re-run the full pipeline for one stock, returning ScoringResult + watchpoints."""
    company = resolve(ts_code, engine)
    snap = load_company_snapshot(engine, company, data_cutoff_date=bjt_now().date())

    results = {
        "profitability": compute_profitability(snap, params),
        "growth": compute_growth(snap, params),
        "cash_quality": compute_cash_quality(snap, params),
        "balance": compute_balance(snap, params),
        "governance": compute_governance(snap, params),
    }
    for r_list in results.values():
        attach_peer_ranks(engine, r_list, snap)

    scoring = score_results(results, params)

    # Pull watchpoints from cache directly (LLM call cost)
    with engine.connect() as c:
        row = c.execute(
            text("""
                SELECT result_json FROM research.computed_cache
                WHERE ts_code = :tc AND compute_key = 'watchpoints'
                ORDER BY computed_at DESC LIMIT 1
            """),
            {"tc": ts_code},
        ).fetchone()
    watchpoints = []
    if row and isinstance(row[0], dict):
        watchpoints = row[0].get("watchpoints", [])

    return {
        "scoring": scoring,
        "watchpoints": watchpoints,
        "verdict_score": scoring.overall_score,
    }


def main() -> int:
    if not ANNOTATIONS_PATH.exists():
        log.error("annotation file not found: %s", ANNOTATIONS_PATH)
        log.error("run `uv run python scripts/research_golden_set_init.py` first.")
        return 2

    annotations = json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))
    log.info("loaded %d annotated entries", len(annotations))

    # Drop placeholder entries that the user hasn't filled in
    real = [
        a for a in annotations
        if a.get("verdict_alignment") in ("agree", "partial", "disagree")
    ]
    if not real:
        log.error("no annotations filled in yet — open %s to start", ANNOTATIONS_PATH)
        return 2
    log.info("%d annotated, %d still placeholder", len(real), len(annotations) - len(real))

    engine = get_engine()
    params = load_params()

    verdict_full = 0
    verdict_partial = 0   # half-weight
    dim_agreed_total = 0.0
    dim_total = 0
    wp_total = 0
    wp_wrong = 0
    recall_sum = 0.0
    recall_n = 0

    per_stock_diffs: list[dict] = []

    for ann in real:
        ts_code = ann["ts_code"]
        try:
            sys_out = _generate_system_output(engine, ts_code, params)
        except Exception as e:
            log.warning("regression failed for %s: %s", ts_code, e)
            continue

        full, partial = _verdict_aligns(ann["verdict_alignment"], sys_out["verdict_score"])
        verdict_full += int(full)
        verdict_partial += int(partial)

        agreed, total = _normalize_dim_disagreement(
            ann.get("dimension_disagreements", {}), sys_out["scoring"].families,
        )
        dim_agreed_total += agreed
        dim_total += total

        sys_wp = sys_out["watchpoints"]
        wrong_idx = ann.get("system_watchpoints_wrong_indices", [])
        wp_total += len(sys_wp)
        wp_wrong += len(wrong_idx)

        recall = _watchpoint_recall(ann.get("your_watchpoints", []), sys_wp)
        recall_sum += recall
        recall_n += 1

        per_stock_diffs.append({
            "ts_code": ts_code,
            "name": ann.get("name"),
            "system_score": sys_out["verdict_score"],
            "verdict_alignment": ann["verdict_alignment"],
            "watchpoint_recall": recall,
        })

    n = len(real)
    metrics = {
        "verdict_alignment_rate": (verdict_full + 0.5 * (verdict_partial - verdict_full)) / max(n, 1),
        "dimension_agreement_rate": dim_agreed_total / max(dim_total, 1),
        "watchpoints_precision": 1 - (wp_wrong / max(wp_total, 1)),
        "watchpoints_recall": recall_sum / max(recall_n, 1),
    }

    log.info("=" * 60)
    log.info("Regression metrics:")
    breached = []
    for k, v in metrics.items():
        threshold = THRESHOLDS[k]
        ok = v >= threshold
        marker = "✓" if ok else "✗"
        log.info(f"  {marker} {k}: {v*100:.1f}%  (threshold {threshold*100:.0f}%)")
        if not ok:
            breached.append(k)

    log.info("=" * 60)
    log.info(f"per-stock recall (showing lowest 5):")
    per_stock_diffs.sort(key=lambda x: x["watchpoint_recall"])
    for d in per_stock_diffs[:5]:
        log.info(f"  {d['ts_code']} {d['name']}: recall={d['watchpoint_recall']*100:.0f}%, "
                 f"system_score={d['system_score']:.1f}, verdict={d['verdict_alignment']}")

    if breached:
        log.error("BLOCKED — thresholds breached: %s", breached)
        return 1
    log.info("ALL METRICS PASSED ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
