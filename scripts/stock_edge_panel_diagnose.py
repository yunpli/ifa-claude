#!/usr/bin/env python
"""Diagnose Stock Edge replay-panel quality before spending tuning budget.

This is intentionally read-only: it loads an existing cached replay panel and
reports sample coverage, label noise, cohort breadth, and baseline rank-IC
stability. It does not write tuning artifacts and does not touch YAML params.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from ifa.families.stock.backtest.panel_evaluator import evaluate_overlay_on_panel, panel_matrix_from_rows
from ifa.families.stock.backtest.replay_panel import PANEL_CACHE_ROOT, PanelRow, load_replay_panel
from ifa.families.stock.params import load_params


def _latest_panel_path() -> Path:
    panels = sorted(PANEL_CACHE_ROOT.glob("*.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not panels:
        raise SystemExit(f"no cached replay panels found under {PANEL_CACHE_ROOT}")
    return panels[0]


def _manifest_for_panel(panel_path: Path) -> dict[str, Any]:
    manifest_path = panel_path.with_suffix(".manifest.json")
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _rank_ic(rows: list[PanelRow], horizon: int, base_params: dict[str, Any]) -> dict[str, Any]:
    if len(rows) < 3:
        return {"n": len(rows), "rank_ic": None, "avg_return_pct": None, "std_return_pct": None, "positive_rate": None}
    panel = panel_matrix_from_rows(rows)
    metrics = evaluate_overlay_on_panel(panel, {}, base_params).get(f"objective_{horizon}d", {})
    returns = np.array([getattr(r, f"forward_{horizon}d_return") for r in rows], dtype=float)
    returns = returns[~np.isnan(returns)]
    return {
        "n": int(metrics.get("sample_count", len(rows))),
        "rank_ic": metrics.get("rank_ic"),
        "avg_return_pct": round(float(np.mean(returns)), 4) if len(returns) else None,
        "std_return_pct": round(float(np.std(returns)), 4) if len(returns) else None,
        "positive_rate": metrics.get("positive_return_rate"),
        "target_first_rate": metrics.get("target_first_rate"),
        "stop_first_rate": metrics.get("stop_first_rate"),
    }


def _slice_metrics(rows: list[PanelRow], key_fn, *, min_rows: int, base_params: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[PanelRow]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row) or "unknown")].append(row)
    out = {}
    for key, items in sorted(groups.items()):
        if len(items) < min_rows:
            out[key] = {"n": len(items), "skipped": f"below min_rows={min_rows}"}
            continue
        out[key] = {f"{h}d": _rank_ic(items, h, base_params) for h in (5, 10, 20)}
        out[key]["n"] = len(items)
    return out


def _label_correlations(rows: list[PanelRow]) -> dict[str, Any]:
    series = {}
    for h in (5, 10, 20):
        vals = []
        for row in rows:
            value = getattr(row, f"forward_{h}d_return")
            vals.append(np.nan if value is None else float(value))
        series[h] = np.array(vals, dtype=float)
    out = {}
    for a, b in ((5, 10), (10, 20), (5, 20)):
        mask = ~np.isnan(series[a]) & ~np.isnan(series[b])
        if int(mask.sum()) < 3 or np.std(series[a][mask]) <= 1e-9 or np.std(series[b][mask]) <= 1e-9:
            corr = None
        else:
            corr = round(float(np.corrcoef(series[a][mask], series[b][mask])[0, 1]), 6)
        out[f"{a}d_{b}d"] = {"n": int(mask.sum()), "corr": corr}
    return out


def _signal_coverage(rows: list[PanelRow], *, top: int = 20) -> dict[str, Any]:
    active_counts = Counter()
    missing_counts = Counter()
    for row in rows:
        for key, sig in row.signals.items():
            if sig.get("status") == "missing":
                missing_counts[key] += 1
            else:
                active_counts[key] += 1
    n = max(1, len(rows))
    sparse = [
        {"signal": key, "active_rate": round(count / n, 4)}
        for key, count in sorted(active_counts.items(), key=lambda kv: kv[1])
    ][:top]
    most_missing = [
        {"signal": key, "missing_rate": round(count / n, 4)}
        for key, count in missing_counts.most_common(top)
    ]
    return {"sparsest_active_signals": sparse, "most_missing_signals": most_missing}


def _diagnosis_flags(rows: list[PanelRow], manifest: dict[str, Any]) -> list[str]:
    flags = []
    dates = sorted({r.as_of_date for r in rows})
    stocks = {r.ts_code for r in rows}
    regimes = {r.regime or "unknown" for r in rows}
    if len(rows) < 500:
        flags.append("panel_rows_lt_500: too small for stable 5/10/20d global parameter promotion")
    if len(dates) < 12:
        flags.append("pit_dates_lt_12: weak time/regime coverage; K-fold variance will dominate")
    if len(regimes - {"unknown"}) < 2:
        flags.append("regime_count_lt_2: regime gate cannot test cross-regime robustness")
    if len(stocks) < 50:
        flags.append("unique_stocks_lt_50: single-stock idiosyncratic noise remains high")
    total_pairs = int(manifest.get("total_pairs") or 0)
    failed_rows = int(manifest.get("failed_rows") or 0)
    if total_pairs and failed_rows / total_pairs > 0.02:
        flags.append("failure_rate_gt_2pct: panel build quality can bias tuning sample")
    mode = (manifest.get("universe_mode") or "legacy").lower()
    if mode == "latest":
        flags.append("latest_universe_mode: possible current-liquidity cohort bias; compare pit-local or stratified cohorts")
    return flags


def diagnose(panel_path: Path, *, min_slice_rows: int) -> dict[str, Any]:
    rows = load_replay_panel(panel_path)
    if not rows:
        raise SystemExit(f"panel is empty: {panel_path}")
    manifest = _manifest_for_panel(panel_path)
    base_params = load_params()
    full_panel = panel_matrix_from_rows(rows)
    baseline = evaluate_overlay_on_panel(full_panel, {}, base_params)
    dates = sorted({r.as_of_date for r in rows})
    stocks = sorted({r.ts_code for r in rows})
    regimes = Counter(r.regime or "unknown" for r in rows)
    rows_by_date = Counter(str(r.as_of_date) for r in rows)

    return {
        "panel_path": str(panel_path),
        "manifest_path": str(panel_path.with_suffix(".manifest.json")),
        "manifest": {
            "universe_id": manifest.get("universe_id"),
            "universe_mode": manifest.get("universe_mode", "legacy"),
            "universe_size": manifest.get("universe_size"),
            "n_rows": manifest.get("n_rows"),
            "total_pairs": manifest.get("total_pairs"),
            "failed_rows": manifest.get("failed_rows"),
            "failure_rate": manifest.get("failure_rate"),
            "as_of_dates": manifest.get("as_of_dates"),
            "universe_selection": manifest.get("universe_selection", {}),
        },
        "coverage": {
            "rows": len(rows),
            "unique_stocks": len(stocks),
            "pit_dates": len(dates),
            "date_min": str(dates[0]),
            "date_max": str(dates[-1]),
            "rows_by_date": dict(sorted(rows_by_date.items())),
            "regime_counts": dict(regimes),
        },
        "baseline_rank_ic": {
            f"{h}d": baseline.get(f"objective_{h}d", {})
            for h in (5, 10, 20)
        },
        "label_correlations": _label_correlations(rows),
        "by_date": _slice_metrics(rows, lambda r: r.as_of_date, min_rows=min_slice_rows, base_params=base_params),
        "by_regime": _slice_metrics(rows, lambda r: r.regime or "unknown", min_rows=min_slice_rows, base_params=base_params),
        "signal_coverage": _signal_coverage(rows),
        "diagnosis_flags": _diagnosis_flags(rows, manifest),
    }


def write_diagnosis_artifact(report: dict[str, Any], *, output_dir: Path | None = None) -> Path:
    """Persist panel diagnosis next to tuning manifests for audit reuse."""
    panel_path = Path(str(report["panel_path"]))
    root = output_dir or Path("/Users/neoclaw/claude/ifaenv/manifests/stock_edge_panel_diagnostics")
    root.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = panel_path.stem.replace("/", "_")
    path = root / f"{stem}__diagnosis_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return path


def _print_text(report: dict[str, Any]) -> None:
    coverage = report["coverage"]
    manifest = report["manifest"]
    print("=== Stock Edge Panel Diagnosis ===")
    print(f"panel: {report['panel_path']}")
    print(
        "coverage: "
        f"rows={coverage['rows']} stocks={coverage['unique_stocks']} "
        f"dates={coverage['pit_dates']} {coverage['date_min']}..{coverage['date_max']} "
        f"regimes={coverage['regime_counts']}"
    )
    print(
        "manifest: "
        f"universe={manifest.get('universe_id')} mode={manifest.get('universe_mode')} "
        f"pairs={manifest.get('total_pairs')} failed={manifest.get('failed_rows')} "
        f"failure_rate={manifest.get('failure_rate')}"
    )
    print("\nBaseline rank IC:")
    for h in ("5d", "10d", "20d"):
        m = report["baseline_rank_ic"][h]
        print(
            f"  {h}: n={m.get('sample_count')} rank_ic={float(m.get('rank_ic', 0.0)):+.4f} "
            f"avg_ret={float(m.get('avg_return', 0.0))*100:+.2f}% "
            f"pos={float(m.get('positive_return_rate', 0.0)):.2f}"
        )
    print("\nLabel horizon correlations:")
    for key, value in report["label_correlations"].items():
        print(f"  {key}: n={value['n']} corr={value['corr']}")
    print("\nDate slices:")
    for key, value in report["by_date"].items():
        if value.get("skipped"):
            print(f"  {key}: n={value['n']} skipped")
            continue
        print(
            f"  {key}: n={value['n']} "
            f"5d={value['5d']['rank_ic']:+.3f} "
            f"10d={value['10d']['rank_ic']:+.3f} "
            f"20d={value['20d']['rank_ic']:+.3f}"
        )
    print("\nRegime slices:")
    for key, value in report["by_regime"].items():
        if value.get("skipped"):
            print(f"  {key}: n={value['n']} skipped")
            continue
        print(
            f"  {key}: n={value['n']} "
            f"5d={value['5d']['rank_ic']:+.3f} "
            f"10d={value['10d']['rank_ic']:+.3f} "
            f"20d={value['20d']['rank_ic']:+.3f}"
        )
    print("\nDiagnosis flags:")
    for flag in report["diagnosis_flags"] or ["none"]:
        print(f"  - {flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose a cached Stock Edge replay panel")
    parser.add_argument("--panel", type=Path, default=None, help="Cached replay panel parquet. Default: newest panel.")
    parser.add_argument("--min-slice-rows", type=int, default=30, help="Minimum rows for date/regime IC slices.")
    parser.add_argument("--output", type=Path, default=None, help="Write JSON diagnosis artifact to this path or directory.")
    parser.add_argument("--json", action="store_true", help="Emit full JSON report.")
    args = parser.parse_args()

    panel_path = args.panel or _latest_panel_path()
    report = diagnose(panel_path, min_slice_rows=args.min_slice_rows)
    written_path = None
    if args.output:
        output = args.output
        if output.suffix.lower() == ".json":
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
            written_path = output
        else:
            written_path = write_diagnosis_artifact(report, output_dir=output)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    else:
        _print_text(report)
        if written_path:
            print(f"\nDiagnosis artifact: {written_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
