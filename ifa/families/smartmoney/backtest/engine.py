"""SmartMoney backtest engine.

Loads factor_daily + sector forward returns, runs factor IC evaluation
and optional walk-forward ML model evaluation, returns BacktestResult.

Factor evaluation:
  For each of the 4 raw factor scores (heat / trend / persistence / crowding):
    - Build (date, sector) panel with factor score + next-day pct_change
    - Compute IC / RankIC / TopN hit rate / Q1..Q5 group returns

Walk-forward ML evaluation (optional, controlled by run_ml_walkforward param):
  Rolling window: train on last `wf_train_days` trading days, predict next
  `wf_step_days`. Repeat step-by-step across the full date range.
  Metrics: AUC on held-out step predictions (aggregated across all steps).

Returns:
  BacktestResult dataclass — consumed by runner.py for DB persistence.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .metrics import compute_factor_metrics

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"

# ── Factor columns to evaluate ────────────────────────────────────────────────

FACTOR_COLS = ["heat_score", "trend_score", "persistence_score", "crowding_score"]


# ── Result containers ─────────────────────────────────────────────────────────

@dataclass
class FactorMetricsResult:
    """Metrics for a single factor across the backtest period."""
    factor_name: str
    window_days: int                    # forward return window (1 or 5)
    ic_mean: float
    ic_std: float
    ic_ir: float
    ic_positive_rate: float
    rank_ic_mean: float
    rank_ic_std: float
    rank_ic_ir: float
    topn_hit_rate: float
    group_returns: dict[str, float]     # Q1..Q5 → mean return
    n_dates: int
    n_samples: int
    per_date_ic: pd.Series = field(default_factory=pd.Series)
    per_date_rank_ic: pd.Series = field(default_factory=pd.Series)


@dataclass
class MLWalkforwardResult:
    """Walk-forward evaluation result for one ML model."""
    model_name: str
    mean_auc: float
    std_auc: float
    n_steps: int
    n_pred_rows: int


@dataclass
class BacktestResult:
    """Container for the full backtest output."""
    start_date: dt.date
    end_date: dt.date
    n_trading_days: int
    params_used: dict[str, Any]

    factor_results: list[FactorMetricsResult]
    ml_results: list[MLWalkforwardResult]   # empty if walk-forward skipped

    # Quick-access summary: factor_name → {metric_name → value}
    summary: dict[str, dict[str, float]] = field(default_factory=dict)

    def best_factor_by_ic(self) -> str | None:
        """Return the factor with the highest absolute IC IR among 1d results."""
        candidates = [
            r for r in self.factor_results if r.window_days == 1
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: abs(r.ic_ir) if np.isfinite(r.ic_ir) else 0).factor_name


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_factor_panel(
    engine: Engine,
    start: dt.date,
    end: dt.date,
) -> pd.DataFrame:
    """Load factor_daily for the window. Returns (trade_date, sector_code, sector_source, *factors)."""
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


def _load_return_panel(
    engine: Engine,
    start: dt.date,
    end: dt.date,
) -> pd.DataFrame:
    """Load sector daily pct_change from all available price tables.

    Returns (trade_date, sector_code, sector_source, pct_chg).
    """
    frames: list[pd.DataFrame] = []

    # SW (申万)
    try:
        sql = text(f"""
            SELECT trade_date, ts_code AS sector_code,
                   'sw' AS sector_source, pct_change AS pct_chg
            FROM {SCHEMA}.raw_sw_daily
            WHERE trade_date BETWEEN :start AND :end
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"start": start, "end": end}).fetchall()
        if rows:
            frames.append(pd.DataFrame(rows, columns=["trade_date", "sector_code", "sector_source", "pct_chg"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("[engine] raw_sw_daily load failed: %s", exc)

    # DC (东财)
    try:
        sql = text(f"""
            SELECT trade_date, ts_code AS sector_code,
                   'dc' AS sector_source, pct_change AS pct_chg
            FROM {SCHEMA}.raw_moneyflow_ind_dc
            WHERE trade_date BETWEEN :start AND :end
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"start": start, "end": end}).fetchall()
        if rows:
            frames.append(pd.DataFrame(rows, columns=["trade_date", "sector_code", "sector_source", "pct_chg"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("[engine] raw_moneyflow_ind_dc load failed: %s", exc)

    # THS (同花顺)
    try:
        sql = text(f"""
            SELECT trade_date, ts_code AS sector_code,
                   'ths' AS sector_source, pct_change AS pct_chg
            FROM {SCHEMA}.raw_moneyflow_ind_ths
            WHERE trade_date BETWEEN :start AND :end
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"start": start, "end": end}).fetchall()
        if rows:
            frames.append(pd.DataFrame(rows, columns=["trade_date", "sector_code", "sector_source", "pct_chg"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("[engine] raw_moneyflow_ind_ths load failed: %s", exc)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
    return df.dropna(subset=["pct_chg"])


# ── Forward return attachment ─────────────────────────────────────────────────

def _attach_forward_returns(
    factor_df: pd.DataFrame,
    return_df: pd.DataFrame,
    window_days: int = 1,
) -> pd.DataFrame:
    """Attach forward return to each (date, sector) factor row.

    For window_days=1: next single trading day's pct_chg.
    For window_days=5: mean pct_chg over next 5 trading days.

    Rows without a valid forward return are dropped.
    """
    # Build date mapping per (sector_code, sector_source)
    ret_pivot = return_df.set_index(["sector_code", "sector_source", "trade_date"])["pct_chg"]
    dates_by_key: dict[tuple[str, str], list[dt.date]] = {}
    for (code, src), grp in return_df.groupby(["sector_code", "sector_source"]):
        dates_by_key[(code, src)] = sorted(grp["trade_date"].tolist())

    def _fwd(row: pd.Series) -> float:
        key = (row["sector_code"], row["sector_source"])
        td: dt.date = row["trade_date"]
        dates = dates_by_key.get(key, [])
        try:
            idx = dates.index(td)
        except ValueError:
            return float("nan")
        fwd_dates = dates[idx + 1: idx + 1 + window_days]
        if not fwd_dates:
            return float("nan")
        vals = [float(ret_pivot.get((key[0], key[1], d), float("nan"))) for d in fwd_dates]
        finite = [v for v in vals if np.isfinite(v)]
        if not finite:
            return float("nan")
        return float(np.mean(finite))

    factor_df = factor_df.copy()
    factor_df["fwd_return"] = factor_df.apply(_fwd, axis=1)
    return factor_df.dropna(subset=["fwd_return"])


# ── Walk-forward ML evaluation ────────────────────────────────────────────────

def _run_walkforward_ml(
    engine: Engine,
    start: dt.date,
    end: dt.date,
    *,
    train_days: int = 60,
    step_days: int = 20,
    label_threshold: float = 0.5,
    on_log: Any = None,
) -> list[MLWalkforwardResult]:
    """Walk-forward evaluation of 3 ML models over [start, end].

    Rolls a training window of `train_days` calendar days, steps `step_days` forward,
    evaluates on the held-out step. Repeats until end.

    Returns:
        List of MLWalkforwardResult (one per model type).
    """
    try:
        from sklearn.metrics import roc_auc_score

        from ..ml.dataset import build_dataset
        from ..ml.logistic import SmartMoneyLogistic
        from ..ml.random_forest import SmartMoneyRandomForest
        from ..ml.xgboost_model import SmartMoneyXGBoost
    except ImportError as e:
        log.warning("[engine] ML imports failed (%s); skipping walk-forward", e)
        return []

    def _emit(msg: str) -> None:
        if on_log:
            on_log(msg)
        log.info(msg)

    # Build list of (train_start, train_end, val_start, val_end) windows
    all_dates_sql = text(f"""
        SELECT DISTINCT trade_date FROM {SCHEMA}.factor_daily
        WHERE trade_date BETWEEN :start AND :end
        ORDER BY trade_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(all_dates_sql, {"start": start, "end": end}).fetchall()
    all_dates: list[dt.date] = [r[0] for r in rows]

    if len(all_dates) < train_days + step_days:
        _emit("[walk-forward] not enough trading days; skipping ML eval")
        return []

    windows: list[tuple[dt.date, dt.date, dt.date, dt.date]] = []
    i = train_days
    while i + step_days <= len(all_dates):
        w_start = all_dates[i - train_days]
        w_end = all_dates[i - 1]
        v_start = all_dates[i]
        v_end = all_dates[min(i + step_days - 1, len(all_dates) - 1)]
        windows.append((w_start, w_end, v_start, v_end))
        i += step_days

    if not windows:
        return []

    _emit(f"[walk-forward] {len(windows)} windows × 3 models")

    model_classes = [SmartMoneyLogistic, SmartMoneyRandomForest, SmartMoneyXGBoost]
    # auc_by_model: model_name → list of per-step AUC scores
    auc_by_model: dict[str, list[float]] = {}
    pred_rows_by_model: dict[str, int] = {}

    for w_idx, (t_start, t_end, v_start, v_end) in enumerate(windows):
        _emit(f"  window {w_idx + 1}/{len(windows)}: train [{t_start}..{t_end}] val [{v_start}..{v_end}]")
        try:
            ds = build_dataset(
                engine,
                train_start=t_start,
                train_end=t_end,
                val_frac=0.0,       # no internal val split; we supply val externally
                label_scheme="binary_up",
                label_threshold=label_threshold,
            )
            ds_val = build_dataset(
                engine,
                train_start=v_start,
                train_end=v_end,
                val_frac=1.0,       # all dates go to val
                label_scheme="binary_up",
                label_threshold=label_threshold,
            )
        except RuntimeError as exc:
            _emit(f"  [skip window {w_idx + 1}] data error: {exc}")
            continue

        if ds.n_train < 20 or ds_val.n_val < 5:
            _emit(f"  [skip window {w_idx + 1}] insufficient rows (train={ds.n_train}, val={ds_val.n_val})")
            continue

        for Cls in model_classes:
            mname = Cls.model_name  # type: ignore[attr-defined]
            try:
                model = Cls().fit(ds)
                proba = model.predict_proba(ds_val.X_val)
                y_true = ds_val.y_val
                if len(np.unique(y_true)) < 2:
                    continue
                auc = float(roc_auc_score(y_true, proba))
                auc_by_model.setdefault(mname, []).append(auc)
                pred_rows_by_model[mname] = pred_rows_by_model.get(mname, 0) + len(y_true)
            except Exception as exc:  # noqa: BLE001
                log.warning("[walk-forward] model=%s window=%d failed: %s", mname, w_idx, exc)

    results: list[MLWalkforwardResult] = []
    for Cls in model_classes:
        mname = Cls.model_name  # type: ignore[attr-defined]
        aucs = auc_by_model.get(mname, [])
        results.append(MLWalkforwardResult(
            model_name=mname,
            mean_auc=float(np.mean(aucs)) if aucs else float("nan"),
            std_auc=float(np.std(aucs)) if len(aucs) > 1 else float("nan"),
            n_steps=len(aucs),
            n_pred_rows=pred_rows_by_model.get(mname, 0),
        ))

    return results


# ── Main entry point ──────────────────────────────────────────────────────────

def run_backtest(
    engine: Engine,
    *,
    start: dt.date,
    end: dt.date,
    params: dict[str, Any] | None = None,
    forward_windows: tuple[int, ...] = (1, 5),
    topn: int = 5,
    n_groups: int = 5,
    run_ml_walkforward: bool = True,
    wf_train_days: int = 60,
    wf_step_days: int = 20,
    label_threshold: float = 0.5,
    on_log: Any = None,
) -> BacktestResult:
    """Run the full factor + ML backtest over [start, end].

    Args:
        engine:              SQLAlchemy engine.
        start:               First date of backtest (inclusive).
        end:                 Last date of backtest (inclusive).
        params:              Params dict attached to the result for reference.
        forward_windows:     Tuple of forward return windows in trading days.
        topn:                N for top-N hit rate.
        n_groups:            Number of quintile groups.
        run_ml_walkforward:  Whether to run walk-forward ML AUC evaluation.
        wf_train_days:       Walk-forward training window (trading days).
        wf_step_days:        Walk-forward step size (trading days).
        label_threshold:     pct_chg threshold for binary label (default 0.5%).
        on_log:              Optional callback(str) for progress messages.

    Returns:
        BacktestResult ready for DB persistence.
    """
    def _emit(msg: str) -> None:
        if on_log:
            on_log(msg)
        log.info(msg)

    _emit(f"[backtest] start={start} end={end} windows={forward_windows}")

    # ── Load base data ──────────────────────────────────────────────────────
    # Extend return window by 10 calendar days to label the last factor dates
    return_end = end + dt.timedelta(days=10)

    _emit("[backtest] loading factor panel...")
    factor_df = _load_factor_panel(engine, start, end)
    if factor_df.empty:
        raise RuntimeError(f"No factor_daily data found for [{start}, {end}]")

    _emit("[backtest] loading return panel...")
    return_df = _load_return_panel(engine, start, return_end)
    if return_df.empty:
        raise RuntimeError("No sector return data found — run ETL first")

    n_trading_days = factor_df["trade_date"].nunique()
    _emit(f"[backtest] {n_trading_days} trading days, {len(factor_df)} factor rows, {len(return_df)} return rows")

    # ── Factor evaluation ───────────────────────────────────────────────────
    factor_results: list[FactorMetricsResult] = []
    summary: dict[str, dict[str, float]] = {}

    for window in forward_windows:
        _emit(f"[backtest] attaching {window}d forward returns...")
        panel = _attach_forward_returns(factor_df, return_df, window_days=window)
        if panel.empty:
            _emit(f"[backtest] no valid rows after {window}d return join; skipping")
            continue

        for factor_col in FACTOR_COLS:
            _emit(f"[backtest] computing metrics: {factor_col} / {window}d")
            try:
                m = compute_factor_metrics(
                    panel,
                    factor_col=factor_col,
                    return_col="fwd_return",
                    topn=topn,
                    n_groups=n_groups,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("[backtest] metrics failed for %s/%dd: %s", factor_col, window, exc)
                continue

            result = FactorMetricsResult(
                factor_name=factor_col,
                window_days=window,
                ic_mean=m["ic_mean"],
                ic_std=m["ic_std"],
                ic_ir=m["ic_ir"],
                ic_positive_rate=m["ic_positive_rate"],
                rank_ic_mean=m["rank_ic_mean"],
                rank_ic_std=m["rank_ic_std"],
                rank_ic_ir=m["rank_ic_ir"],
                topn_hit_rate=m["topn_hit_rate_mean"],
                group_returns=m["group_returns"],
                n_dates=m["n_dates"],
                n_samples=m["n_samples"],
                per_date_ic=m["per_date_ic"],
                per_date_rank_ic=m["per_date_rank_ic"],
            )
            factor_results.append(result)

            key = f"{factor_col}/{window}d"
            summary[key] = {
                "ic_mean": m["ic_mean"],
                "ic_ir": m["ic_ir"],
                "rank_ic_mean": m["rank_ic_mean"],
                "rank_ic_ir": m["rank_ic_ir"],
                "topn_hit_rate": m["topn_hit_rate_mean"],
                **{f"group_{k}": v for k, v in m["group_returns"].items()},
            }

    # ── Walk-forward ML ─────────────────────────────────────────────────────
    ml_results: list[MLWalkforwardResult] = []
    if run_ml_walkforward:
        _emit("[backtest] starting walk-forward ML evaluation...")
        ml_results = _run_walkforward_ml(
            engine, start, end,
            train_days=wf_train_days,
            step_days=wf_step_days,
            label_threshold=label_threshold,
            on_log=on_log,
        )
        for ml in ml_results:
            summary[f"ml_{ml.model_name}"] = {
                "mean_auc": ml.mean_auc,
                "std_auc": ml.std_auc,
                "n_steps": float(ml.n_steps),
            }

    _emit(f"[backtest] done. {len(factor_results)} factor×window results, {len(ml_results)} ML models")

    return BacktestResult(
        start_date=start,
        end_date=end,
        n_trading_days=n_trading_days,
        params_used=params or {},
        factor_results=factor_results,
        ml_results=ml_results,
        summary=summary,
    )
