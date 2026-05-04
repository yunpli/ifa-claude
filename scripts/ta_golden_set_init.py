"""TA golden-set init — pick 30 diverse trade days, prefill system outputs
into a JSON template the user manually corrects.

Diversity: stratified by regime (covers each regime present in
ta.regime_daily); within regime, sampled across the full date range so
behavior is tested across market phases. Output:

    tests/golden_set/ta_v22.json   — annotation template

Run:
    uv run python scripts/ta_golden_set_init.py [--n 30]
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text

from ifa.core.db import get_engine

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = ROOT / "tests" / "golden_set"
ANN_PATH = GOLDEN_DIR / "ta_v22.json"


def _stratified_pick(engine, n: int) -> list[dict]:
    sql = text("""
        SELECT trade_date, regime, confidence
        FROM ta.regime_daily
        WHERE trade_date >= '2024-01-01'
        ORDER BY trade_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    if not rows:
        return []

    # Group by regime
    by_regime: dict[str, list] = defaultdict(list)
    for r in rows:
        by_regime[r[1]].append(r)

    # Allocate: equal share, dominant regime fills remainder
    regimes = list(by_regime.keys())
    base = max(1, n // len(regimes))
    plan = {r: base for r in regimes}
    remainder = n - sum(plan.values())
    if remainder > 0:
        # give remainder to most populous regimes
        for r in sorted(regimes, key=lambda x: -len(by_regime[x])):
            if remainder == 0:
                break
            plan[r] += 1
            remainder -= 1

    picks: list[dict] = []
    for regime, want in plan.items():
        bucket = by_regime[regime]
        if not bucket:
            continue
        # Evenly spaced within bucket so dates span the whole range
        if len(bucket) <= want:
            chosen = bucket
        else:
            step = len(bucket) // want
            chosen = bucket[::step][:want]
        for d, regime_, conf in chosen:
            picks.append({
                "trade_date": d.isoformat(),
                "system_regime": regime_,
                "system_confidence": float(conf) if conf is not None else None,
            })

    picks.sort(key=lambda p: p["trade_date"])
    return picks[:n]


def _enrich(engine, pick: dict) -> dict:
    with engine.connect() as conn:
        top5 = conn.execute(text("""
            SELECT ts_code, setup_name, final_score
            FROM ta.candidates_daily
            WHERE trade_date = :d AND star_rating = 5 AND in_top_watchlist
            ORDER BY rank LIMIT 5
        """), {"d": pick["trade_date"]}).fetchall()
        n_total = conn.execute(text("""
            SELECT COUNT(*) FROM ta.candidates_daily WHERE trade_date = :d
        """), {"d": pick["trade_date"]}).scalar() or 0
    pick["system_top_5_picks"] = [
        {"ts_code": r[0], "setup_name": r[1], "score": float(r[2])}
        for r in top5
    ]
    pick["system_total_candidates"] = int(n_total)
    return pick


def _annotation_skeleton(pick: dict) -> dict:
    return {
        **pick,
        "your_regime": "ok",  # ok / should_be_<regime> / partial：reason
        "your_top_pick_review": [
            {"slot": i + 1, "verdict": "ok"}
            for i in range(len(pick["system_top_5_picks"]))
        ],
        "rejected_setups": [],   # setups you'd reject for this day, e.g. ["C2_CHIP_LOOSE"]
        "your_notes": "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()

    engine = get_engine()
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    picks = _stratified_pick(engine, args.n)
    if not picks:
        log.error("no regime_daily data to sample from")
        return
    enriched = [_enrich(engine, p) for p in picks]
    skeleton = [_annotation_skeleton(p) for p in enriched]

    ANN_PATH.write_text(
        json.dumps(skeleton, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("wrote %d picks to %s", len(skeleton), ANN_PATH)
    by_regime = defaultdict(int)
    for p in enriched:
        by_regime[p["system_regime"]] += 1
    log.info("regime distribution: %s", dict(by_regime))


if __name__ == "__main__":
    main()
