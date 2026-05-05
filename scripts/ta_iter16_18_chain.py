"""iter16/17/18 chain — fast_rerank-based ranker variants.

iter16: winrate.floor_ratio 0.4 → 0.45 / 0.35
iter17: regime_boost coefficient 0.50 → 0.40 / 0.60   (yaml: ranker.regime_boost_coef)
iter18: sector_flow weights 0.5/0.3/0.2 → 0.4/0.4/0.2

Uses fast_rerank (no rebuild of contexts) so each variant runs in ~30s.
Yaml is restored on every exit path (success or interrupt).

Output: /tmp/ta_iter16_18_<stamp>.txt with multi-window Tier A/B vs market.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import yaml
from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.families.ta.backtest import analyze_tier_perf
from ifa.families.ta.params import reload_params
from ifa.families.ta.setups.fast_rerank import fast_rerank_window

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

YAML_PATH = Path("ifa/families/ta/params/ta_v2.2.yaml")
BACKUP_DIR = Path("tmp/ta_yaml_backups")

WINDOWS = [
    ("60d",  date(2026, 1, 15), date(2026, 4, 14)),
    ("180d", date(2025, 9, 1),  date(2026, 4, 14)),
    ("360d", date(2024, 12, 15), date(2026, 4, 14)),
]
RERANK_START = date(2024, 12, 15)
RERANK_END   = date(2026, 4, 14)

VARIANTS = [
    # iter16: winrate floor
    {"label": "iter16a_floor_0p45", "patches": [("ranker.winrate.floor_ratio", 0.45)]},
    {"label": "iter16b_floor_0p35", "patches": [("ranker.winrate.floor_ratio", 0.35)]},
    # iter18: sector_flow weights (yaml-only, no code change)
    {"label": "iter18a_flow_4_4_2",
     "patches": [
         ("sector_flow.rank_weight", 0.4),
         ("sector_flow.phase_weight", 0.4),
         ("sector_flow.confidence_weight", 0.2),
     ]},
    {"label": "iter18b_flow_6_2_2",
     "patches": [
         ("sector_flow.rank_weight", 0.6),
         ("sector_flow.phase_weight", 0.2),
         ("sector_flow.confidence_weight", 0.2),
     ]},
]
# iter17 (regime_boost coef) needs ranker.py code edit to read from yaml — skip
# unless yaml-ized. The current code is `boost = max(-0.20, min(0.50, (ratio - 1.0) * 0.50))`
# with the 0.50 hardcoded. We add a yaml-driven variant only if user wants.


def _set_nested(d: dict, path: str, value):
    parts = path.split(".")
    cur = d
    for k in parts[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[parts[-1]] = value


def _apply_patches(patches: list[tuple]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"ta_v2.2_before_{stamp}.yaml"
    shutil.copy(YAML_PATH, backup)
    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    for path, value in patches:
        _set_nested(data, path, value)
    YAML_PATH.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    reload_params()
    return backup


def _restore(backup: Path):
    if backup.exists():
        shutil.copy(backup, YAML_PATH)
        reload_params()


def _measure(eng) -> list[str]:
    lines = []
    for name, s, e in WINDOWS:
        with eng.connect() as c:
            r = c.execute(text(
                "SELECT AVG(return_t15_pct) FROM ta.position_events_daily "
                "WHERE generation_date BETWEEN :s AND :e AND fill_status='filled'"
            ), {"s": s, "e": e}).first()
        mkt = float(r[0]) if r[0] is not None else 0.0
        for tier in ("A", "B"):
            perf = analyze_tier_perf(eng, start=s, end=e, tier=tier)
            delta = perf.avg_realized_return - mkt
            flag = "OK" if delta > 0 else "WORSE"
            lines.append(
                f"  {name:<5} {tier} n={perf.n_positions:>4} "
                f"fill={perf.fill_rate*100:5.1f}% "
                f"realized={perf.avg_realized_return:+6.2f}% "
                f"mkt={mkt:+6.2f}% "
                f"vs={delta:+5.2f}pp {flag}"
            )
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None,
                    help="run only this variant label (substring match)")
    args = ap.parse_args()

    eng = get_engine()
    overall_lines = [
        f"=== iter16/18 chain — {datetime.now().isoformat()} ===",
        "Method: fast_rerank only (no scan/track rebuild). Position prices unchanged.",
        f"Re-rank window: {RERANK_START} → {RERANK_END}",
        "",
    ]
    out = Path(f"/tmp/ta_iter16_18_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")

    variants = [v for v in VARIANTS if args.only is None or args.only in v["label"]]
    print(f"Running {len(variants)} variants → {out}")

    # Run each variant — backup + patch + rerank + measure + restore.
    for v in variants:
        label = v["label"]
        print(f"\n── {label} ──")
        for p, val in v["patches"]:
            print(f"   {p} = {val}")
        backup = _apply_patches(v["patches"])
        try:
            t0 = time.time()
            n = fast_rerank_window(eng, RERANK_START, RERANK_END)
            dt = time.time() - t0
            print(f"   rerank: {n} rows in {dt:.0f}s")
            section = [f"── {label} (rerank {n} rows in {dt:.0f}s) ──"]
            for p, val in v["patches"]:
                section.append(f"   patch: {p} = {val}")
            section.extend(_measure(eng))
            section.append("")
            print("\n".join(section[1:]))
            overall_lines.extend(section)
        finally:
            _restore(backup)
            print(f"   [restored yaml]")

    # Final pass: rerank back to baseline (current iter13c yaml) so the
    # database state matches what's in yaml.
    print("\n── final rerank back to baseline ──")
    t0 = time.time()
    n = fast_rerank_window(eng, RERANK_START, RERANK_END)
    print(f"   {n} rows in {time.time()-t0:.0f}s")

    overall_lines.append("── baseline (iter13c) — restored DB state ──")
    overall_lines.extend(_measure(eng))

    out.write_text("\n".join(overall_lines), encoding="utf-8")
    print(f"\n[saved → {out}]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[interrupted — yaml should be restored by finally clauses]")
        sys.exit(130)
