#!/usr/bin/env python3
"""Compare Stock Edge outcome-proxy feature families on a cached parquet panel."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd

from ifa.families.stock.backtest.outcome_proxy import compare_proxy_candidate_families


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy-cache", required=True, help="Path to an outcome_proxy parquet cache.")
    parser.add_argument(
        "--output-dir",
        default="/Users/neoclaw/claude/ifaenv/manifests/stock_edge_panel_diagnostics",
        help="Directory for comparison JSON artifact.",
    )
    parser.add_argument("--output", help="Optional exact JSON output path.")
    args = parser.parse_args()

    cache_path = Path(args.proxy_cache)
    df = pd.read_parquet(cache_path)
    comparison = compare_proxy_candidate_families(df)
    comparison["input"] = {
        "proxy_cache": str(cache_path),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "note": "Proxy-only diagnostic comparison; no full replay and no production YAML mutation.",
    }

    out_path = Path(args.output) if args.output else _default_output_path(Path(args.output_dir), cache_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"proxy family comparison: {out_path}")
    _print_summary(comparison)
    return 0


def _default_output_path(output_dir: Path, cache_path: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{cache_path.stem}__proxy_family_compare_{stamp}.json"


def _print_summary(comparison: dict[str, Any]) -> None:
    print(f"rows={comparison.get('rows')} dates={comparison.get('date_count')} stocks={comparison.get('stock_count')}")
    print("family                                      5d_ic   10d_ic  20d_ic  10d_top  10d_win  10d_spread  Mar10_ic  Mar20_ic")
    for item in comparison.get("ranking", []):
        name = item["family"]
        payload = comparison["families"][name]
        h = payload["horizons"]
        mar = payload.get("month_stability", {}).get("2026-03", {})
        print(
            f"{name[:42]:42s} "
            f"{h['5d']['rank_ic']:+7.3f} {h['10d']['rank_ic']:+7.3f} {h['20d']['rank_ic']:+7.3f} "
            f"{_pct(h['10d']['top_bucket_return']):>8s} {h['10d']['top_bucket_win_rate']:>8.2f} "
            f"{_pct(h['10d']['top_vs_bottom_spread']):>10s} "
            f"{mar.get('10d', {}).get('rank_ic', 0.0):+8.3f} {mar.get('20d', {}).get('rank_ic', 0.0):+8.3f}"
        )


def _pct(value: float) -> str:
    return f"{float(value) * 100:+.2f}%"


if __name__ == "__main__":
    raise SystemExit(main())
