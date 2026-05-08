#!/usr/bin/env python
"""Outcome-first Stock Edge panel validation report.

This is a read-only evaluator for cached replay panels. It reports the metrics
that matter for tradable stock selection: 5/10/20d forward returns, rank IC,
top-bucket payoff, top-vs-bottom spread, monotonicity, drawdown proxies, and
stability by month/regime/industry/liquidity/size.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import numpy as np
from sqlalchemy import text

from ifa.core.db import get_engine
from ifa.families.stock.backtest.panel_evaluator import evaluate_overlay_on_panel, panel_matrix_from_rows
from ifa.families.stock.backtest.replay_panel import PANEL_CACHE_ROOT, PanelRow, load_replay_panel
from ifa.families.stock.backtest.tuning_artifact import read_tuning_artifact
from ifa.families.stock.params import load_params


def _latest_panel_path() -> Path:
    panels = sorted(PANEL_CACHE_ROOT.glob("*.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not panels:
        raise SystemExit(f"no cached replay panels found under {PANEL_CACHE_ROOT}")
    return panels[0]


def _load_overlay(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return read_tuning_artifact(path).overlay


def _evaluate(rows: list[PanelRow], overlay: dict[str, Any], base_params: dict[str, Any]) -> dict[str, Any]:
    if not rows:
        return {}
    return evaluate_overlay_on_panel(panel_matrix_from_rows(rows), overlay, base_params)


def _headline(metrics: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for h in (5, 10, 20):
        m = metrics.get(f"objective_{h}d", {}) or {}
        out[f"{h}d"] = {
            "n": int(m.get("sample_count", 0) or 0),
            "forward_return_avg": m.get("avg_return"),
            "rank_ic": m.get("rank_ic"),
            "top_bucket_return": m.get("top_bucket_avg_return"),
            "top_bucket_win_rate": m.get("top_bucket_win_rate"),
            "top_bucket_profit_loss": m.get("top_bucket_profit_loss"),
            "top_bucket_left_tail": m.get("top_bucket_left_tail"),
            "top_bucket_drawdown_proxy": m.get("top_bucket_drawdown_proxy"),
            "top_n_return": m.get("top_n_avg_return"),
            "top_n_win_rate": m.get("top_n_win_rate"),
            "top_vs_bottom_spread": m.get("top_bottom_spread"),
            "monotonicity": m.get("bucket_monotonicity"),
            "bucket_avg_returns": m.get("bucket_avg_returns"),
        }
    return out


def _slice_rows(
    rows: list[PanelRow],
    key_fn: Callable[[PanelRow], str],
    *,
    min_rows: int,
    overlay: dict[str, Any],
    base_params: dict[str, Any],
) -> dict[str, Any]:
    groups: dict[str, list[PanelRow]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row) or "unknown")].append(row)
    out = {}
    for key, items in sorted(groups.items()):
        if len(items) < min_rows:
            out[key] = {"n": len(items), "skipped": f"below min_rows={min_rows}"}
            continue
        out[key] = {"n": len(items), "metrics": _headline(_evaluate(items, overlay, base_params))}
    return out


def _row_metadata(rows: list[PanelRow]) -> dict[tuple[str, dt.date], dict[str, Any]]:
    """Load PIT industry/liquidity/size/tradability labels for panel rows."""
    engine = get_engine()
    by_date: dict[dt.date, list[str]] = defaultdict(list)
    for row in rows:
        by_date[row.as_of_date].append(row.ts_code)
    raw: dict[tuple[str, dt.date], dict[str, Any]] = {}
    with engine.connect() as conn:
        for as_of, codes in by_date.items():
            snapshot_month = as_of.replace(day=1)
            result = conn.execute(text("""
                WITH daily_window AS (
                    SELECT ts_code,
                           AVG(amount) AS avg_amount,
                           COUNT(*) AS n_days
                    FROM smartmoney.raw_daily
                    WHERE ts_code = ANY(:codes)
                      AND trade_date <= :as_of
                      AND trade_date >= :start
                    GROUP BY ts_code
                ),
                latest_daily AS (
                    SELECT DISTINCT ON (ts_code)
                           ts_code, close, pct_chg, amount, high, low
                    FROM smartmoney.raw_daily
                    WHERE ts_code = ANY(:codes) AND trade_date <= :as_of
                    ORDER BY ts_code, trade_date DESC
                ),
                latest_basic AS (
                    SELECT DISTINCT ON (ts_code)
                           ts_code, total_mv, circ_mv
                    FROM smartmoney.raw_daily_basic
                    WHERE ts_code = ANY(:codes) AND trade_date <= :as_of
                    ORDER BY ts_code, trade_date DESC
                ),
                members AS (
                    SELECT DISTINCT ON (ts_code)
                           ts_code, l1_code, l1_name, l2_code, l2_name, name
                    FROM smartmoney.sw_member_monthly
                    WHERE ts_code = ANY(:codes) AND snapshot_month <= :snapshot_month
                    ORDER BY ts_code, snapshot_month DESC
                )
                SELECT c.ts_code,
                       COALESCE(m.l1_name, m.l1_code, 'UNKNOWN') AS industry,
                       COALESCE(m.l2_name, m.l2_code, 'UNKNOWN') AS industry_l2,
                       COALESCE(w.avg_amount, 0) AS avg_amount,
                       COALESCE(b.total_mv, b.circ_mv, 0) AS mv,
                       COALESCE(d.pct_chg, 0) AS latest_pct_chg,
                       COALESCE(d.amount, 0) AS latest_amount,
                       COALESCE(m.name, '') AS name
                FROM unnest(:codes) AS c(ts_code)
                LEFT JOIN daily_window w USING (ts_code)
                LEFT JOIN latest_daily d USING (ts_code)
                LEFT JOIN latest_basic b USING (ts_code)
                LEFT JOIN members m USING (ts_code)
            """), {
                "codes": sorted(set(codes)),
                "as_of": as_of,
                "start": as_of - dt.timedelta(days=120),
                "snapshot_month": snapshot_month,
            }).mappings()
            for r in result:
                raw[(str(r["ts_code"]), as_of)] = dict(r)

    amounts = np.array([float(v.get("avg_amount") or 0.0) for v in raw.values()], dtype=float)
    mvs = np.array([float(v.get("mv") or 0.0) for v in raw.values()], dtype=float)
    amount_q = np.quantile(amounts, [1 / 3, 2 / 3]) if len(amounts) else [0.0, 0.0]
    mv_q = np.quantile(mvs, [1 / 3, 2 / 3]) if len(mvs) else [0.0, 0.0]
    out: dict[tuple[str, dt.date], dict[str, Any]] = {}
    for key, value in raw.items():
        amount = float(value.get("avg_amount") or 0.0)
        mv = float(value.get("mv") or 0.0)
        latest_pct = float(value.get("latest_pct_chg") or 0.0)
        name = str(value.get("name") or "")
        out[key] = {
            **value,
            "liquidity_bucket": _bucket3(amount, amount_q, "liq"),
            "size_bucket": _bucket3(mv, mv_q, "mv"),
            "tradability": _tradability_label(name=name, amount=amount, latest_pct=latest_pct),
        }
    return out


def _bucket3(value: float, q: np.ndarray | list[float], prefix: str) -> str:
    if value <= float(q[0]):
        return f"{prefix}_low"
    if value <= float(q[1]):
        return f"{prefix}_mid"
    return f"{prefix}_high"


def _tradability_label(*, name: str, amount: float, latest_pct: float) -> str:
    flags = []
    if "ST" in name.upper() or "退" in name:
        flags.append("st_or_delist_name")
    if amount <= 0:
        flags.append("no_recent_liquidity")
    elif amount < 50_000:
        flags.append("low_liquidity")
    if latest_pct >= 9.5:
        flags.append("limit_up_buy_risk")
    return "|".join(flags) if flags else "tradable_proxy_ok"


def build_report(
    *,
    panel_path: Path,
    artifact_path: Path | None,
    min_slice_rows: int,
) -> dict[str, Any]:
    rows = load_replay_panel(panel_path)
    if not rows:
        raise SystemExit(f"panel is empty: {panel_path}")
    base_params = load_params()
    overlay = _load_overlay(artifact_path)
    metadata = _row_metadata(rows)

    baseline = _evaluate(rows, {}, base_params)
    candidate = _evaluate(rows, overlay, base_params)
    slice_kwargs = {"min_rows": min_slice_rows, "overlay": overlay, "base_params": base_params}

    return {
        "panel_path": str(panel_path),
        "artifact_path": str(artifact_path) if artifact_path else None,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "coverage": {
            "rows": len(rows),
            "unique_stocks": len({r.ts_code for r in rows}),
            "pit_dates": len({r.as_of_date for r in rows}),
            "date_min": str(min(r.as_of_date for r in rows)),
            "date_max": str(max(r.as_of_date for r in rows)),
        },
        "baseline": _headline(baseline),
        "candidate": _headline(candidate),
        "candidate_minus_baseline": _lift_headline(candidate, baseline),
        "candidate_slices": {
            "month": _slice_rows(rows, lambda r: f"{r.as_of_date:%Y-%m}", **slice_kwargs),
            "regime": _slice_rows(rows, lambda r: r.regime or "unknown", **slice_kwargs),
            "industry_l1": _slice_rows(rows, lambda r: metadata.get((r.ts_code, r.as_of_date), {}).get("industry", "UNKNOWN"), **slice_kwargs),
            "liquidity": _slice_rows(rows, lambda r: metadata.get((r.ts_code, r.as_of_date), {}).get("liquidity_bucket", "unknown"), **slice_kwargs),
            "size": _slice_rows(rows, lambda r: metadata.get((r.ts_code, r.as_of_date), {}).get("size_bucket", "unknown"), **slice_kwargs),
            "tradability": _slice_rows(rows, lambda r: metadata.get((r.ts_code, r.as_of_date), {}).get("tradability", "unknown"), **slice_kwargs),
        },
    }


def _lift_headline(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for h in (5, 10, 20):
        key = f"{h}d"
        c = candidate.get(f"objective_{h}d", {}) or {}
        b = baseline.get(f"objective_{h}d", {}) or {}
        out[key] = {
            "rank_ic_lift": round(float(c.get("rank_ic", 0.0) or 0.0) - float(b.get("rank_ic", 0.0) or 0.0), 6),
            "top_bucket_return_lift": round(float(c.get("top_bucket_avg_return", 0.0) or 0.0) - float(b.get("top_bucket_avg_return", 0.0) or 0.0), 6),
            "top_vs_bottom_spread_lift": round(float(c.get("top_bottom_spread", 0.0) or 0.0) - float(b.get("top_bottom_spread", 0.0) or 0.0), 6),
            "monotonicity_lift": round(float(c.get("bucket_monotonicity", 0.0) or 0.0) - float(b.get("bucket_monotonicity", 0.0) or 0.0), 6),
        }
    return out


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:+.2f}%"
    except Exception:
        return "-"


def _print_table(report: dict[str, Any]) -> None:
    print("=== Stock Edge Outcome Report ===")
    c = report["coverage"]
    print(f"panel: {report['panel_path']}")
    print(f"coverage: rows={c['rows']} stocks={c['unique_stocks']} dates={c['pit_dates']} {c['date_min']}..{c['date_max']}")
    print("\nCandidate headline:")
    print("  horizon  avg_ret  rank_ic  top_ret  top_win  top_vs_bottom  mono  left_tail  dd_proxy")
    for h in ("5d", "10d", "20d"):
        m = report["candidate"][h]
        print(
            f"  {h:>7s}  {_fmt_pct(m['forward_return_avg']):>7s}  "
            f"{float(m['rank_ic'] or 0):+7.3f}  {_fmt_pct(m['top_bucket_return']):>7s}  "
            f"{float(m['top_bucket_win_rate'] or 0):6.2f}  {_fmt_pct(m['top_vs_bottom_spread']):>13s}  "
            f"{float(m['monotonicity'] or 0):+5.2f}  {_fmt_pct(m['top_bucket_left_tail']):>9s}  "
            f"{_fmt_pct(m['top_bucket_drawdown_proxy']):>8s}"
        )
    print("\nCandidate minus baseline:")
    for h, m in report["candidate_minus_baseline"].items():
        print(
            f"  {h}: rank_ic {float(m['rank_ic_lift']):+.4f}, "
            f"top_ret {_fmt_pct(m['top_bucket_return_lift'])}, "
            f"spread {_fmt_pct(m['top_vs_bottom_spread_lift'])}, "
            f"mono {float(m['monotonicity_lift']):+.3f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Create outcome-first Stock Edge panel validation report")
    parser.add_argument("--panel", type=Path, default=None, help="Cached replay panel parquet. Default: newest panel.")
    parser.add_argument("--artifact", type=Path, default=None, help="Optional tuning artifact JSON for candidate overlay.")
    parser.add_argument("--min-slice-rows", type=int, default=30, help="Minimum rows per slice.")
    parser.add_argument("--output", type=Path, default=None, help="Write JSON report to this path.")
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    args = parser.parse_args()

    report = build_report(
        panel_path=args.panel or _latest_panel_path(),
        artifact_path=args.artifact,
        min_slice_rows=args.min_slice_rows,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    else:
        _print_table(report)
        if args.output:
            print(f"\nOutcome artifact: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
