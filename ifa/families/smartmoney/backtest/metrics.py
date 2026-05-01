"""Pure-math metric functions for SmartMoney factor backtest.

All functions operate on numpy arrays and return floats / dicts.
No I/O, no DB, no side effects — safe to unit-test in isolation.

Metrics:
  ic(scores, fwd_returns)               → Pearson IC (float)
  rank_ic(scores, fwd_returns)          → Spearman RankIC (float)
  topn_hit_rate(scores, fwd_returns, n) → Top-N positive hit rate (float)
  group_returns(scores, fwd_returns, k) → Mean return per Q1..Qk (dict)
  compute_factor_metrics(df, ...)       → Full aggregate metric dict from
                                          a time-series panel DataFrame
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

log = logging.getLogger(__name__)

# ── Atomic metric functions ───────────────────────────────────────────────────

def ic(scores: np.ndarray, fwd_returns: np.ndarray) -> float:
    """Pearson IC between factor scores and forward returns.

    Returns nan if fewer than 5 valid observations.
    """
    mask = np.isfinite(scores) & np.isfinite(fwd_returns)
    if mask.sum() < 5:
        return float("nan")
    corr = np.corrcoef(scores[mask], fwd_returns[mask])
    return float(corr[0, 1])


def rank_ic(scores: np.ndarray, fwd_returns: np.ndarray) -> float:
    """Spearman rank IC between factor scores and forward returns.

    Returns nan if fewer than 5 valid observations.
    """
    mask = np.isfinite(scores) & np.isfinite(fwd_returns)
    if mask.sum() < 5:
        return float("nan")
    r, _ = stats.spearmanr(scores[mask], fwd_returns[mask])
    return float(r)


def topn_hit_rate(
    scores: np.ndarray,
    fwd_returns: np.ndarray,
    n: int = 5,
) -> float:
    """Fraction of top-N factor-ranked sectors that had positive forward return.

    Args:
        scores:      Factor values for one cross-section.
        fwd_returns: Corresponding next-period returns.
        n:           Number of top-ranked sectors to evaluate.

    Returns:
        Hit rate in [0, 1], or nan if not enough valid data.
    """
    mask = np.isfinite(scores) & np.isfinite(fwd_returns)
    if mask.sum() < n:
        return float("nan")
    s = scores[mask]
    r = fwd_returns[mask]
    top_idx = np.argsort(s)[::-1][:n]
    return float(np.mean(r[top_idx] > 0))


def group_returns(
    scores: np.ndarray,
    fwd_returns: np.ndarray,
    n_groups: int = 5,
) -> dict[str, float]:
    """Mean forward return per equal-count quintile (Q1=lowest, Q5=highest score).

    Args:
        scores:      Factor values for one cross-section.
        fwd_returns: Corresponding next-period returns.
        n_groups:    Number of groups (default 5 → Q1..Q5).

    Returns:
        Dict mapping group label → mean return. Values may be nan.
    """
    labels = [f"Q{i + 1}" for i in range(n_groups)]
    mask = np.isfinite(scores) & np.isfinite(fwd_returns)
    if mask.sum() < n_groups * 2:
        return {lbl: float("nan") for lbl in labels}

    s = scores[mask]
    r = fwd_returns[mask]

    try:
        quantile_labels = pd.qcut(s, n_groups, labels=labels)
    except ValueError:
        # Degenerate: too many ties to create n_groups unique bins
        return {lbl: float("nan") for lbl in labels}

    result: dict[str, float] = {}
    for lbl in labels:
        idx = quantile_labels == lbl
        result[lbl] = float(r[idx].mean()) if idx.sum() > 0 else float("nan")
    return result


# ── Aggregate metric computation ──────────────────────────────────────────────

def _ir(series: pd.Series) -> float:
    """Information Ratio = mean(IC) / std(IC).  nan if std == 0."""
    m = float(series.mean())
    s = float(series.std())
    if s < 1e-12:
        return float("nan")
    return m / s


def compute_factor_metrics(
    df: pd.DataFrame,
    *,
    factor_col: str,
    return_col: str,
    topn: int = 5,
    n_groups: int = 5,
) -> dict[str, Any]:
    """Compute full aggregate metrics from a time-series panel.

    Args:
        df:          DataFrame with columns: trade_date, {factor_col}, {return_col}.
                     One row per (date, sector) cross-section observation.
        factor_col:  Column name of the factor scores.
        return_col:  Column name of the forward returns.
        topn:        N for top-N hit rate calculation.
        n_groups:    Number of quantile groups.

    Returns dict with keys:
        n_dates, n_samples,
        ic_mean, ic_std, ic_ir, ic_positive_rate,
        rank_ic_mean, rank_ic_std, rank_ic_ir,
        topn_hit_rate_mean,
        group_returns: {Q1: float, ..., Qk: float},
        per_date_ic: pd.Series (index=trade_date),
        per_date_rank_ic: pd.Series (index=trade_date),
    """
    required = {"trade_date", factor_col, return_col}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing columns: {missing}")

    dates = sorted(df["trade_date"].unique())
    n_dates = len(dates)

    ic_vals: list[float] = []
    ric_vals: list[float] = []
    topn_vals: list[float] = []
    group_accum: dict[str, list[float]] = {f"Q{i + 1}": [] for i in range(n_groups)}
    date_ic: dict[Any, float] = {}
    date_ric: dict[Any, float] = {}

    for date in dates:
        day = df[df["trade_date"] == date]
        s = day[factor_col].values.astype(float)
        r = day[return_col].values.astype(float)

        day_ic = ic(s, r)
        day_ric = rank_ic(s, r)
        day_topn = topn_hit_rate(s, r, n=topn)
        day_groups = group_returns(s, r, n_groups=n_groups)

        ic_vals.append(day_ic)
        ric_vals.append(day_ric)
        topn_vals.append(day_topn)
        date_ic[date] = day_ic
        date_ric[date] = day_ric

        for lbl, v in day_groups.items():
            group_accum[lbl].append(v)

    ic_series = pd.Series(ic_vals, index=dates, dtype=float).dropna()
    ric_series = pd.Series(ric_vals, index=dates, dtype=float).dropna()
    topn_series = pd.Series(topn_vals, index=dates, dtype=float).dropna()

    mean_groups: dict[str, float] = {}
    for lbl, vals in group_accum.items():
        arr = np.array(vals, dtype=float)
        finite = arr[np.isfinite(arr)]
        mean_groups[lbl] = float(finite.mean()) if len(finite) > 0 else float("nan")

    n_samples = len(df.dropna(subset=[factor_col, return_col]))

    result: dict[str, Any] = {
        "n_dates": n_dates,
        "n_samples": n_samples,
        # IC
        "ic_mean": float(ic_series.mean()) if not ic_series.empty else float("nan"),
        "ic_std": float(ic_series.std()) if not ic_series.empty else float("nan"),
        "ic_ir": _ir(ic_series) if not ic_series.empty else float("nan"),
        "ic_positive_rate": float((ic_series > 0).mean()) if not ic_series.empty else float("nan"),
        # RankIC
        "rank_ic_mean": float(ric_series.mean()) if not ric_series.empty else float("nan"),
        "rank_ic_std": float(ric_series.std()) if not ric_series.empty else float("nan"),
        "rank_ic_ir": _ir(ric_series) if not ric_series.empty else float("nan"),
        # TopN
        "topn_hit_rate_mean": float(topn_series.mean()) if not topn_series.empty else float("nan"),
        # Quintiles
        "group_returns": mean_groups,
        # Series for plots / export
        "per_date_ic": pd.Series(date_ic, dtype=float),
        "per_date_rank_ic": pd.Series(date_ric, dtype=float),
    }

    log.debug(
        "[metrics] %s | IC=%.4f (IR=%.2f) | RankIC=%.4f | TopN=%.1f%% | dates=%d samples=%d",
        factor_col,
        result["ic_mean"], result["ic_ir"],
        result["rank_ic_mean"],
        result["topn_hit_rate_mean"] * 100,
        n_dates, n_samples,
    )
    return result
