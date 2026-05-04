"""TA regression — score system outputs against the user-annotated golden set.

Reads tests/golden_set/ta_v22.json (filled in by the user; annotation token
format mirrors research_regression: 'ok' | 'partial：…' | 'should_be_<x>' /
'disagree：…').

Metrics:
  · regime_accuracy_rate     stocks where annotation token == 'ok' or 'partial' / total
  · top_pick_intersection     fraction of top_5_picks judged 'ok' / 'partial' across days
  · rejected_setups_recall    of setups annotated as rejected, fraction NOT in
                              today's in_top_watchlist (i.e. ranker honored rejection)

Thresholds (V2.2 todo §M9):
    regime_accuracy_rate ≥ 80%
    top_pick_intersection ≥ 60%
    rejected_setups_recall ≥ 80%

Exit codes:
    0 — all metrics ≥ threshold
    1 — at least one threshold breached
    2 — annotations not filled in yet
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from sqlalchemy import text

from ifa.core.db import get_engine

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ANN_PATH = ROOT / "tests" / "golden_set" / "ta_v22.json"
THRESHOLDS = {
    "regime_accuracy_rate": 0.80,
    "top_pick_intersection": 0.60,
    "rejected_setups_recall": 0.80,
}


def _token(value: str | None) -> str:
    if not value:
        return "ok"
    head = value.strip()
    for sep in ("：", ":", "；", ";"):
        if sep in head:
            head = head.split(sep, 1)[0]
            break
    head = head.strip().lower()
    if head.startswith("should_be"):
        return "disagree"
    return head


def main() -> int:
    if not ANN_PATH.exists():
        log.error("missing %s — run scripts/ta_golden_set_init.py first", ANN_PATH)
        return 2
    annotations = json.loads(ANN_PATH.read_text(encoding="utf-8"))

    real = [a for a in annotations if a.get("trade_date")]
    if not real:
        log.error("annotation file is empty")
        return 2

    engine = get_engine()

    regime_scores: list[float] = []
    pick_scores: list[float] = []
    rejected_recall_num = 0
    rejected_recall_den = 0

    for ann in real:
        # 1. regime accuracy
        token = _token(ann.get("your_regime"))
        if token == "ok":
            regime_scores.append(1.0)
        elif token == "partial":
            regime_scores.append(0.5)
        else:
            regime_scores.append(0.0)

        # 2. top pick intersection
        reviews = ann.get("your_top_pick_review", [])
        if reviews:
            day_score = 0.0
            for r in reviews:
                vt = _token(r.get("verdict"))
                if vt == "ok":
                    day_score += 1.0
                elif vt == "partial":
                    day_score += 0.5
            pick_scores.append(day_score / len(reviews))

        # 3. rejected setups recall
        rejected = ann.get("rejected_setups") or []
        if rejected:
            with engine.connect() as conn:
                in_top = conn.execute(
                    text("""SELECT setup_name FROM ta.candidates_daily
                            WHERE trade_date = :d AND in_top_watchlist
                            GROUP BY setup_name"""),
                    {"d": ann["trade_date"]},
                ).fetchall()
            top_setups = {r[0] for r in in_top}
            for s in rejected:
                rejected_recall_den += 1
                if s not in top_setups:
                    rejected_recall_num += 1

    metrics = {
        "regime_accuracy_rate": sum(regime_scores) / max(len(regime_scores), 1),
        "top_pick_intersection": sum(pick_scores) / max(len(pick_scores), 1)
                                 if pick_scores else 1.0,
        "rejected_setups_recall": (rejected_recall_num / rejected_recall_den)
                                  if rejected_recall_den else 1.0,
    }

    log.info("=" * 60)
    log.info("TA Regression Metrics:")
    breached: list[str] = []
    for k, v in metrics.items():
        thr = THRESHOLDS[k]
        ok = v >= thr
        log.info(f"  {'✓' if ok else '✗'} {k}: {v*100:.1f}%  (threshold {thr*100:.0f}%)")
        if not ok:
            breached.append(k)
    log.info("=" * 60)

    if breached:
        log.error("BLOCKED — thresholds breached: %s", breached)
        return 1
    log.info("ALL METRICS PASSED ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
