#!/usr/bin/env python
"""Out-of-Sample (OOS) Validation for SmartMoney Factors.

Compares factor metrics between an in-sample (IS) training window and an
out-of-sample (OOS) holdout window.  Flags potential overfit when OOS
degrades by more than 50 % relative to IS on key metrics.

Usage
-----
    python scripts/oos_validate.py \\
        --train-start 2021-01-01 --train-end 2025-10-31 \\
        --val-start   2025-11-01 --val-end   2026-04-30

Optional flags
--------------
    --param-version v2026_05   # label to echo in the report header
    --window 1                  # forward-return window in trading days (default 1)
    --topn   5                  # top-N hit rate threshold
    --overfit-threshold 0.5     # OOS/IS ratio below which a metric is flagged
    --plot                      # save IC time-series plot to /tmp/oos_ic_<date>.png
    --markdown FILE             # also write a markdown summary to FILE

Exit codes
----------
    0  — all factors pass (or no severe overfit detected)
    1  — at least one factor shows severe overfit (OOS IC IR < overfit_threshold × IS)
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── DB / factor imports ───────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ifa.core.db.engine import get_engine
from ifa.families.smartmoney.backtest.metrics import compute_factor_metrics
from sqlalchemy import text
from sqlalchemy.engine import Engine

SCHEMA = "smartmoney"
FACTOR_COLS = ["heat_score", "trend_score", "persistence_score", "crowding_score"]


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_factor_panel(engine: Engine, start: dt.date, end: dt.date) -> pd.DataFrame:
    sql = text(f"""
        SELECT trade_date, sector_code, sector_source,
               heat_score, trend_score, persistence_score, crowding_score
        FROM {SCHEMA}.factor_daily
        WHERE trade_date BETWEEN :start AND :end
        ORDER BY trade_date, sector_code, sector_source
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"start": start, "end": end}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "trade_date", "sector_code", "sector_source",
        "heat_score", "trend_score", "persistence_score", "crowding_score",
    ])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for c in FACTOR_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _load_sector_returns(engine: Engine, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Load daily sector pct_change from all three sources and deduplicate."""
    frames: list[pd.DataFrame] = []

    queries = [
        (f"SELECT trade_date, ts_code AS sector_code, 'sw' AS sector_source, pct_change AS pct_chg FROM {SCHEMA}.raw_sw_daily WHERE trade_date BETWEEN :start AND :end", "raw_sw_daily"),
        (f"SELECT trade_date, ts_code AS sector_code, 'dc' AS sector_source, pct_change AS pct_chg FROM {SCHEMA}.raw_moneyflow_ind_dc WHERE trade_date BETWEEN :start AND :end", "raw_moneyflow_ind_dc"),
        (f"SELECT trade_date, ts_code AS sector_code, 'ths' AS sector_source, pct_change AS pct_chg FROM {SCHEMA}.raw_moneyflow_ind_ths WHERE trade_date BETWEEN :start AND :end", "raw_moneyflow_ind_ths"),
    ]
    for sql_str, tbl in queries:
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(sql_str), {"start": start, "end": end}).fetchall()
            if rows:
                frames.append(pd.DataFrame(rows, columns=["trade_date", "sector_code", "sector_source", "pct_chg"]))
                log.info("[load] %s: %d rows", tbl, len(rows))
        except Exception as exc:  # noqa: BLE001
            log.warning("[load] %s failed: %s", tbl, exc)

    if not frames:
        return pd.DataFrame()

    ret = pd.concat(frames, ignore_index=True)
    ret["trade_date"] = pd.to_datetime(ret["trade_date"]).dt.date
    ret["pct_chg"] = pd.to_numeric(ret["pct_chg"], errors="coerce")
    ret.sort_values(["trade_date", "sector_code", "sector_source"], inplace=True)
    return ret


def _build_panel(
    factor_df: pd.DataFrame,
    ret_df: pd.DataFrame,
    window: int,
) -> pd.DataFrame:
    """Join factor scores to *next-day* (or next-window) sector returns.

    For each date D, the forward return is the pct_chg observed `window` trading
    days later (i.e. at date D+window in the sorted date list).

    Returns a merged DataFrame with columns:
        trade_date, sector_code, sector_source, <factor_cols>, fwd_return
    """
    if factor_df.empty or ret_df.empty:
        return pd.DataFrame()

    # Build a sorted list of unique trading dates from the return panel
    all_dates = sorted(ret_df["trade_date"].unique())
    date_idx = {d: i for i, d in enumerate(all_dates)}

    # Map each date → the date that is `window` steps forward
    def _fwd_date(d: dt.date) -> dt.date | None:
        i = date_idx.get(d)
        if i is None:
            return None
        fwd_i = i + window
        return all_dates[fwd_i] if fwd_i < len(all_dates) else None

    factor_df = factor_df.copy()
    factor_df["fwd_date"] = factor_df["trade_date"].map(_fwd_date)
    factor_df = factor_df.dropna(subset=["fwd_date"])

    # Merge: factor on trade_date, return on fwd_date
    ret_df = ret_df.rename(columns={"trade_date": "fwd_date", "pct_chg": "fwd_return"})
    panel = factor_df.merge(ret_df, on=["fwd_date", "sector_code", "sector_source"], how="inner")
    panel = panel.dropna(subset=["fwd_return"])
    return panel


# ── Metric comparison ─────────────────────────────────────────────────────────

SCALAR_METRICS = [
    ("ic_mean", "IC Mean"),
    ("ic_std", "IC Std"),
    ("ic_ir", "IC IR"),
    ("ic_positive_rate", "IC Pos Rate"),
    ("rank_ic_mean", "RankIC Mean"),
    ("rank_ic_std", "RankIC Std"),
    ("rank_ic_ir", "RankIC IR"),
    ("topn_hit_rate_mean", "TopN Hit Rate"),
]

GROUP_LABELS = ["Q1", "Q2", "Q3", "Q4", "Q5"]


def _ratio(oos_val: float, is_val: float) -> float | None:
    """Return OOS/IS ratio for positive IS values; None if not meaningful."""
    if not math.isfinite(is_val) or not math.isfinite(oos_val):
        return None
    if abs(is_val) < 1e-9:
        return None
    # For metrics where sign matters (IC IR etc.) use absolute ratio when IS < 0
    return oos_val / abs(is_val)


def _is_overfit(metric_key: str, is_val: float, oos_val: float, threshold: float) -> bool:
    """True if OOS degrades more than `threshold` relative to IS on this metric."""
    # Only flag key IC/RankIC metrics, not std
    if metric_key not in ("ic_ir", "rank_ic_ir", "ic_mean", "rank_ic_mean"):
        return False
    r = _ratio(oos_val, is_val)
    if r is None:
        return False
    # If IS is positive (good direction), OOS should remain >= threshold × IS
    if is_val > 0 and r < threshold:
        return True
    # If IS is negative (factor works inversely) accept OOS being < threshold
    return False


def _fmt(v: float | None, pct: bool = False) -> str:
    if v is None or not math.isfinite(v):
        return "  n/a "
    if pct:
        return f"{v * 100:6.1f}%"
    return f"{v:+.4f}"


# ── Rich table output ─────────────────────────────────────────────────────────

def _print_results(
    results: dict[str, tuple[dict, dict]],
    overfit_threshold: float,
    window: int,
    train_label: str,
    val_label: str,
) -> int:
    """Print side-by-side IS vs OOS table.  Returns count of overfit factors."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        console = Console()
        use_rich = True
    except ImportError:
        use_rich = False

    overfit_count = 0

    for factor, (is_m, oos_m) in results.items():
        overfit_flags: set[str] = set()
        for key, _ in SCALAR_METRICS:
            if _is_overfit(key, is_m.get(key, float("nan")), oos_m.get(key, float("nan")), overfit_threshold):
                overfit_flags.add(key)
        if overfit_flags:
            overfit_count += 1

        if use_rich:
            status_icon = "⚠️  OVERFIT" if overfit_flags else "✅ OK"
            title = f"[bold]{factor}[/bold]  (window={window}d)  {status_icon}  |  IS: {train_label}   OOS: {val_label}"
            tbl = Table(title=title, box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta")
            tbl.add_column("Metric", style="dim", width=18)
            tbl.add_column(f"IS {train_label}", justify="right", width=14)
            tbl.add_column(f"OOS {val_label}", justify="right", width=14)
            tbl.add_column("OOS/IS", justify="right", width=10)
            tbl.add_column("Flag", justify="center", width=6)

            for key, label in SCALAR_METRICS:
                is_v = is_m.get(key, float("nan"))
                oos_v = oos_m.get(key, float("nan"))
                pct = key in ("ic_positive_rate", "topn_hit_rate_mean")
                r = _ratio(oos_v, is_v)
                r_str = f"{r:.2f}" if r is not None else "n/a"
                flag = "⚠️" if key in overfit_flags else ""
                row_style = "red" if key in overfit_flags else ""
                tbl.add_row(label, _fmt(is_v, pct), _fmt(oos_v, pct), r_str, flag, style=row_style)

            # Group returns
            tbl.add_row("", "", "", "", "", style="dim")
            for grp in GROUP_LABELS:
                is_g = is_m.get("group_returns", {}).get(grp, float("nan"))
                oos_g = oos_m.get("group_returns", {}).get(grp, float("nan"))
                r = _ratio(oos_g, is_g)
                r_str = f"{r:.2f}" if r is not None else "n/a"
                tbl.add_row(f"  Return {grp}", _fmt(is_g, True), _fmt(oos_g, True), r_str, "")

            tbl.add_row("Dates", str(is_m.get("n_dates", 0)), str(oos_m.get("n_dates", 0)), "", "", style="dim")
            tbl.add_row("Samples", str(is_m.get("n_samples", 0)), str(oos_m.get("n_samples", 0)), "", "", style="dim")
            console.print(tbl)
            console.print()
        else:
            # Plain text fallback
            status = "OVERFIT" if overfit_flags else "OK"
            print(f"\n{'=' * 70}")
            print(f"Factor: {factor}  (window={window}d)  [{status}]  IS: {train_label}  OOS: {val_label}")
            print(f"{'Metric':<22}{'IS':>12}{'OOS':>12}{'OOS/IS':>10}{'Flag':>6}")
            print("-" * 64)
            for key, label in SCALAR_METRICS:
                is_v = is_m.get(key, float("nan"))
                oos_v = oos_m.get(key, float("nan"))
                pct = key in ("ic_positive_rate", "topn_hit_rate_mean")
                r = _ratio(oos_v, is_v)
                r_str = f"{r:.2f}" if r is not None else "n/a"
                flag = " ⚠" if key in overfit_flags else ""
                print(f"{label:<22}{_fmt(is_v, pct):>12}{_fmt(oos_v, pct):>12}{r_str:>10}{flag}")
            print()
            for grp in GROUP_LABELS:
                is_g = is_m.get("group_returns", {}).get(grp, float("nan"))
                oos_g = oos_m.get("group_returns", {}).get(grp, float("nan"))
                r = _ratio(oos_g, is_g)
                r_str = f"{r:.2f}" if r is not None else "n/a"
                print(f"  Return {grp:<15}{_fmt(is_g, True):>12}{_fmt(oos_g, True):>12}{r_str:>10}")
            print(f"  Dates:  IS={is_m.get('n_dates', 0)}  OOS={oos_m.get('n_dates', 0)}")

    return overfit_count


# ── IC time-series plot ───────────────────────────────────────────────────────

def _plot_ic_series(
    results: dict[str, tuple[dict, dict]],
    train_start: dt.date, train_end: dt.date,
    val_start: dt.date, val_end: dt.date,
    output_path: Path,
) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        log.warning("[plot] matplotlib not available; skipping plot")
        return

    n_factors = len(results)
    fig, axes = plt.subplots(n_factors, 1, figsize=(14, 4 * n_factors), sharex=False)
    if n_factors == 1:
        axes = [axes]

    for ax, (factor, (is_m, oos_m)) in zip(axes, results.items()):
        is_ic = is_m.get("per_date_ic", pd.Series(dtype=float))
        oos_ic = oos_m.get("per_date_ic", pd.Series(dtype=float))

        if not is_ic.empty:
            is_dates = [dt.datetime.combine(d, dt.time()) if isinstance(d, dt.date) else d for d in is_ic.index]
            ax.plot(is_dates, is_ic.values, color="steelblue", alpha=0.7, linewidth=0.8, label="IS IC")
            # Rolling mean
            roll = is_ic.rolling(20, min_periods=5).mean()
            ax.plot(is_dates, roll.values, color="navy", linewidth=1.5, label="IS IC(20d MA)")

        if not oos_ic.empty:
            oos_dates = [dt.datetime.combine(d, dt.time()) if isinstance(d, dt.date) else d for d in oos_ic.index]
            ax.plot(oos_dates, oos_ic.values, color="tomato", alpha=0.7, linewidth=0.8, label="OOS IC")
            roll = oos_ic.rolling(20, min_periods=5).mean()
            ax.plot(oos_dates, roll.values, color="darkred", linewidth=1.5, label="OOS IC(20d MA)")

        ax.axhline(0, color="black", linewidth=0.5, linestyle="--")
        ax.axvline(dt.datetime.combine(val_start, dt.time()), color="orange", linewidth=1.5, linestyle="--", label="IS/OOS split")

        is_mean = is_m.get("ic_mean", float("nan"))
        oos_mean = oos_m.get("ic_mean", float("nan"))
        is_ir = is_m.get("ic_ir", float("nan"))
        oos_ir = oos_m.get("ic_ir", float("nan"))
        ax.set_title(
            f"{factor}  |  IS IC={is_mean:+.4f} IR={is_ir:.2f}  "
            f"OOS IC={oos_mean:+.4f} IR={oos_ir:.2f}",
            fontsize=11,
        )
        ax.set_ylabel("IC")
        ax.legend(loc="upper right", fontsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

    fig.suptitle(
        f"SmartMoney Factor IC: In-Sample ({train_start} → {train_end}) vs OOS ({val_start} → {val_end})",
        fontsize=13, fontweight="bold", y=1.01,
    )
    fig.tight_layout()
    fig.savefig(output_path, dpi=120, bbox_inches="tight")
    log.info("[plot] saved IC time-series to %s", output_path)
    plt.close(fig)


# ── Markdown export ───────────────────────────────────────────────────────────

def _write_markdown(
    results: dict[str, tuple[dict, dict]],
    overfit_threshold: float,
    window: int,
    train_label: str,
    val_label: str,
    output_path: Path,
) -> None:
    lines: list[str] = [
        "# SmartMoney OOS Validation Report",
        "",
        f"- **IS window**: {train_label}",
        f"- **OOS window**: {val_label}",
        f"- **Forward return window**: {window}d",
        f"- **Overfit threshold**: OOS/IS ratio < {overfit_threshold}",
        f"- **Generated**: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    for factor, (is_m, oos_m) in results.items():
        overfit_flags: set[str] = set()
        for key, _ in SCALAR_METRICS:
            if _is_overfit(key, is_m.get(key, float("nan")), oos_m.get(key, float("nan")), overfit_threshold):
                overfit_flags.add(key)
        status = "⚠️ OVERFIT" if overfit_flags else "✅ OK"
        lines += [
            f"## {factor}  {status}",
            "",
            f"| Metric | IS ({train_label}) | OOS ({val_label}) | OOS/IS | Flag |",
            "|--------|------------------|------------------|--------|------|",
        ]
        for key, label in SCALAR_METRICS:
            is_v = is_m.get(key, float("nan"))
            oos_v = oos_m.get(key, float("nan"))
            pct = key in ("ic_positive_rate", "topn_hit_rate_mean")
            r = _ratio(oos_v, is_v)
            r_str = f"{r:.2f}" if r is not None else "n/a"
            flag = "⚠️" if key in overfit_flags else ""
            lines.append(f"| {label} | {_fmt(is_v, pct).strip()} | {_fmt(oos_v, pct).strip()} | {r_str} | {flag} |")

        lines.append("")
        lines.append("**Group Returns (Q1=lowest, Q5=highest score):**")
        lines.append("")
        lines.append("| Group | IS | OOS | OOS/IS |")
        lines.append("|-------|-----|-----|--------|")
        for grp in GROUP_LABELS:
            is_g = is_m.get("group_returns", {}).get(grp, float("nan"))
            oos_g = oos_m.get("group_returns", {}).get(grp, float("nan"))
            r = _ratio(oos_g, is_g)
            r_str = f"{r:.2f}" if r is not None else "n/a"
            lines.append(f"| {grp} | {_fmt(is_g, True).strip()} | {_fmt(oos_g, True).strip()} | {r_str} |")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("[md] saved markdown report to %s", output_path)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--train-start", required=True, type=dt.date.fromisoformat, metavar="YYYY-MM-DD")
    p.add_argument("--train-end",   required=True, type=dt.date.fromisoformat, metavar="YYYY-MM-DD")
    p.add_argument("--val-start",   required=True, type=dt.date.fromisoformat, metavar="YYYY-MM-DD")
    p.add_argument("--val-end",     required=True, type=dt.date.fromisoformat, metavar="YYYY-MM-DD")
    p.add_argument("--param-version", default=None, help="Param version label for display only")
    p.add_argument("--window",      type=int, default=1, help="Forward return window in trading days (default 1)")
    p.add_argument("--topn",        type=int, default=5, help="Top-N hit rate threshold (default 5)")
    p.add_argument("--overfit-threshold", type=float, default=0.5,
                   help="OOS/IS ratio below which a key metric is flagged as overfit (default 0.5)")
    p.add_argument("--plot",        action="store_true", help="Save IC time-series plot as PNG")
    p.add_argument("--markdown",    type=Path, default=None, metavar="FILE",
                   help="Also write a markdown summary to this file")
    p.add_argument("--factors",     nargs="+", default=FACTOR_COLS,
                   help="Which factors to evaluate (default: all 4)")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    train_label = f"{args.train_start} → {args.train_end}"
    val_label   = f"{args.val_start} → {args.val_end}"
    pver_note   = f"  (param_version={args.param_version})" if args.param_version else ""

    log.info("=" * 65)
    log.info("SmartMoney OOS Validation%s", pver_note)
    log.info("  IS : %s", train_label)
    log.info("  OOS: %s", val_label)
    log.info("  window=%dd  topn=%d  overfit_threshold=%.2f",
             args.window, args.topn, args.overfit_threshold)
    log.info("=" * 65)

    engine = get_engine()

    # ── Load data ─────────────────────────────────────────────────────────────
    log.info("[data] loading IS factor panel …")
    is_factors = _load_factor_panel(engine, args.train_start, args.train_end)
    log.info("[data] IS factor panel: %d rows", len(is_factors))

    log.info("[data] loading OOS factor panel …")
    oos_factors = _load_factor_panel(engine, args.val_start, args.val_end)
    log.info("[data] OOS factor panel: %d rows", len(oos_factors))

    # Returns need to extend one window beyond the factor dates
    is_ret_end  = args.train_end + dt.timedelta(days=30)   # generous buffer
    oos_ret_end = args.val_end   + dt.timedelta(days=30)

    log.info("[data] loading IS sector returns …")
    is_returns = _load_sector_returns(engine, args.train_start, is_ret_end)

    log.info("[data] loading OOS sector returns …")
    oos_returns = _load_sector_returns(engine, args.val_start, oos_ret_end)

    if is_factors.empty:
        log.error("[data] IS factor panel is empty — nothing to evaluate")
        return 1
    if oos_factors.empty:
        log.warning("[data] OOS factor panel is empty — skipping OOS evaluation")

    # ── Build panels ──────────────────────────────────────────────────────────
    log.info("[panel] building IS panel (window=%dd) …", args.window)
    is_panel = _build_panel(is_factors, is_returns, window=args.window)
    log.info("[panel] IS panel: %d rows", len(is_panel))

    log.info("[panel] building OOS panel (window=%dd) …", args.window)
    oos_panel = _build_panel(oos_factors, oos_returns, window=args.window)
    log.info("[panel] OOS panel: %d rows", len(oos_panel))

    # ── Compute metrics per factor ────────────────────────────────────────────
    results: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    for factor in args.factors:
        if factor not in is_panel.columns:
            log.warning("[metrics] factor '%s' not found in panel; skipping", factor)
            continue

        log.info("[metrics] %s — computing IS metrics …", factor)
        is_sub = is_panel[["trade_date", factor, "fwd_return"]].rename(columns={factor: "_score", "fwd_return": "_ret"})
        is_sub.columns = ["trade_date", "_score", "_ret"]
        is_m = compute_factor_metrics(is_sub, factor_col="_score", return_col="_ret", topn=args.topn)

        oos_m: dict[str, Any] = {}
        if not oos_panel.empty and factor in oos_panel.columns:
            log.info("[metrics] %s — computing OOS metrics …", factor)
            oos_sub = oos_panel[["trade_date", factor, "fwd_return"]].rename(columns={factor: "_score", "fwd_return": "_ret"})
            oos_sub.columns = ["trade_date", "_score", "_ret"]
            oos_m = compute_factor_metrics(oos_sub, factor_col="_score", return_col="_ret", topn=args.topn)
        else:
            log.warning("[metrics] %s — no OOS data; filling with nan", factor)
            for key, _ in SCALAR_METRICS:
                oos_m[key] = float("nan")
            oos_m["group_returns"] = {g: float("nan") for g in GROUP_LABELS}
            oos_m["per_date_ic"] = pd.Series(dtype=float)
            oos_m["per_date_rank_ic"] = pd.Series(dtype=float)
            oos_m["n_dates"] = 0
            oos_m["n_samples"] = 0

        results[factor] = (is_m, oos_m)

    if not results:
        log.error("[metrics] no factors evaluated — check data availability")
        return 1

    # ── Print results ─────────────────────────────────────────────────────────
    overfit_count = _print_results(
        results, args.overfit_threshold, args.window, train_label, val_label
    )

    # ── Summary line ──────────────────────────────────────────────────────────
    total = len(results)
    log.info("")
    log.info("=" * 65)
    if overfit_count == 0:
        log.info("RESULT: All %d factor(s) PASSED OOS validation ✅", total)
    else:
        log.warning("RESULT: %d / %d factor(s) show potential OVERFIT ⚠️", overfit_count, total)
    log.info("=" * 65)

    # ── Optional plot ─────────────────────────────────────────────────────────
    if args.plot:
        plot_path = Path(f"/tmp/oos_ic_{dt.date.today()}.png")
        _plot_ic_series(
            results,
            args.train_start, args.train_end,
            args.val_start, args.val_end,
            plot_path,
        )

    # ── Optional markdown ─────────────────────────────────────────────────────
    if args.markdown:
        _write_markdown(results, args.overfit_threshold, args.window, train_label, val_label, args.markdown)

    return 1 if overfit_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
