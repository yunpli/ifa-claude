"""Train/val/predict dataset assembly for SmartMoney ML models.

Label construction (next-day return classification):
  For each sector on day T, the label is whether sector's pct_change on day
  T+1 exceeds a threshold (positive class = "上涨").

  Three label schemes available:
    binary_up   — 1 if T+1 pct_change > threshold, else 0
    binary_up5d — 1 if avg(pct_change, T+1..T+5) > threshold, else 0
    three_class — 2=up / 1=neutral / 0=down  (not used in P0 models)

Label sources by sector_source:
  sw    → raw_sw_daily.pct_change                  (TuShare L1 index daily)
  sw_l2 → equal-weighted AVG(raw_daily.pct_chg) over sw_member_monthly L2
  dc    → raw_moneyflow_ind_dc.pct_change           (or raw_dc_index.pct_change)
  ths   → raw_moneyflow_ind_ths.pct_change
  kpl   → no price series → excluded from supervised learning

Split strategy:
  Time-based split (never shuffle): last val_frac of dates = val set.
  This prevents look-ahead leakage between train and val.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .features import ALL_FEATURE_COLS, build_feature_matrix

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"


# ── Label loading ─────────────────────────────────────────────────────────────

def _load_sw_returns(engine: Engine, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Load pct_change for SW sectors, shifted to represent *next-day* return."""
    sql = f"""
        SELECT trade_date, ts_code AS sector_code,
               'sw' AS sector_source, pct_change AS pct_chg
        FROM {SCHEMA}.raw_sw_daily
        WHERE trade_date BETWEEN :start AND :end
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"start": start, "end": end}).fetchall()
    return pd.DataFrame(rows, columns=["trade_date", "sector_code", "sector_source", "pct_chg"])


def _load_sw_l2_returns(engine: Engine, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Equal-weighted SW L2 sector daily return.

    SW L2 has no direct daily index in TuShare (sw_daily is L1 only). We
    construct an equal-weighted L2 sector return as the AVG of member
    stocks' pct_chg via the PIT-correct sw_member_monthly snapshot.

    Why equal-weight (not value-weighted): equal-weighted returns are more
    sensitive to broad participation (mid/small caps), which is the SmartMoney
    thesis. Value-weighted would over-emphasise the few largest names.
    """
    sql = f"""
        SELECT m.trade_date, sm.l2_code AS sector_code,
               'sw_l2' AS sector_source, AVG(m.pct_chg) AS pct_chg
        FROM {SCHEMA}.raw_daily m
        JOIN {SCHEMA}.sw_member_monthly sm
          ON m.ts_code = sm.ts_code
         AND sm.snapshot_month = date_trunc('month', m.trade_date)::date
        WHERE m.trade_date BETWEEN :start AND :end
        GROUP BY m.trade_date, sm.l2_code
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"start": start, "end": end}).fetchall()
    return pd.DataFrame(rows, columns=["trade_date", "sector_code", "sector_source", "pct_chg"])


def _load_dc_returns(engine: Engine, start: dt.date, end: dt.date) -> pd.DataFrame:
    sql = f"""
        SELECT trade_date, ts_code AS sector_code,
               'dc' AS sector_source, pct_change AS pct_chg
        FROM {SCHEMA}.raw_moneyflow_ind_dc
        WHERE trade_date BETWEEN :start AND :end
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"start": start, "end": end}).fetchall()
    return pd.DataFrame(rows, columns=["trade_date", "sector_code", "sector_source", "pct_chg"])


def _load_ths_returns(engine: Engine, start: dt.date, end: dt.date) -> pd.DataFrame:
    sql = f"""
        SELECT trade_date, ts_code AS sector_code,
               'ths' AS sector_source, pct_change AS pct_chg
        FROM {SCHEMA}.raw_moneyflow_ind_ths
        WHERE trade_date BETWEEN :start AND :end
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"start": start, "end": end}).fetchall()
    return pd.DataFrame(rows, columns=["trade_date", "sector_code", "sector_source", "pct_chg"])


def _build_return_panel(
    engine: Engine,
    start: dt.date,
    end: dt.date,
) -> pd.DataFrame:
    """Combine all source returns into one panel.

    Order of frames matters only for diagnostic logs — the panel is queried
    by (sector_code, sector_source) so each source is keyed independently.
    """
    frames = []
    for fn in [_load_sw_l2_returns, _load_sw_returns, _load_dc_returns, _load_ths_returns]:
        df = fn(engine, start, end)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
    return df.dropna(subset=["pct_chg"])


def _attach_forward_return_label(
    feature_df: pd.DataFrame,
    return_panel: pd.DataFrame,
    *,
    horizon_days: int = 1,
    scheme: str = "binary_top_quintile",
    threshold: float = 0.5,
    quantile: float = 0.20,
) -> pd.DataFrame:
    """Attach forward N-day return + label onto feature_df.

    For each row (date=T, sector_code, sector_source) we compound pct_chg over
    T+1 .. T+horizon_days (using the sector's own trading-day calendar).

    Schemes:
      'binary_up'           → label=1 if cumulative forward return > threshold (%)
      'binary_top_quintile' → per-date cross-section: top `quantile` fraction = 1
                              (recommended for cross-sectional ranking models)
      'three_class'         → 2/1/0 by ±threshold cutoff
      'regression'          → label = forward_return (no binarisation)

    Returns feature_df with added 'forward_return' and 'label' columns; rows
    where forward return is unavailable are dropped.
    """
    if horizon_days < 1:
        raise ValueError(f"horizon_days must be >= 1, got {horizon_days}")

    # Sort returns once per (code, src) and build a lookup
    panel = return_panel.sort_values(["sector_code", "sector_source", "trade_date"]).reset_index(drop=True)
    dates_by_key: dict[tuple[str, str], list[dt.date]] = {}
    rets_by_key: dict[tuple[str, str], list[float]] = {}
    for (code, src), grp in panel.groupby(["sector_code", "sector_source"], sort=False):
        dates_by_key[(code, src)] = grp["trade_date"].tolist()
        rets_by_key[(code, src)] = grp["pct_chg"].tolist()

    def _forward_return(row: pd.Series) -> float:
        key = (row["sector_code"], row["sector_source"])
        dates = dates_by_key.get(key, [])
        rets = rets_by_key.get(key, [])
        try:
            idx = dates.index(row["trade_date"])
        except ValueError:
            return np.nan
        # Compound the pct_chg from idx+1 to idx+horizon_days
        if idx + horizon_days >= len(dates):
            return np.nan  # not enough forward data
        cum = 1.0
        for j in range(idx + 1, idx + 1 + horizon_days):
            cum *= 1.0 + (rets[j] / 100.0)
        return (cum - 1.0) * 100.0

    feature_df = feature_df.copy()
    feature_df["forward_return"] = feature_df.apply(_forward_return, axis=1)
    feature_df = feature_df.dropna(subset=["forward_return"]).copy()

    if scheme == "binary_up":
        feature_df["label"] = (feature_df["forward_return"] > threshold).astype(int)
    elif scheme == "binary_top_quintile":
        # Per-date cross-section: rank within each trade_date, top `quantile` = 1
        feature_df["_pct_rank"] = (
            feature_df.groupby("trade_date")["forward_return"]
            .rank(pct=True, method="first")
        )
        feature_df["label"] = (feature_df["_pct_rank"] >= 1.0 - quantile).astype(int)
        feature_df = feature_df.drop(columns=["_pct_rank"])
    elif scheme == "three_class":
        def _three(v: float) -> int:
            if v > threshold:
                return 2
            if v < -threshold:
                return 0
            return 1
        feature_df["label"] = feature_df["forward_return"].apply(_three)
    elif scheme == "regression":
        feature_df["label"] = feature_df["forward_return"].astype(float)
    else:
        raise ValueError(f"Unknown label scheme: {scheme}")

    return feature_df


# Backwards-compat alias for any caller still using the 1-day-only function
def _attach_next_day_label(
    feature_df: pd.DataFrame,
    return_panel: pd.DataFrame,
    *,
    scheme: str = "binary_up",
    threshold: float = 0.5,
) -> pd.DataFrame:
    """Deprecated: use `_attach_forward_return_label(horizon_days=1, ...)`.

    Kept for backwards compatibility — translates 1-day-only intent to the
    new general helper. The legacy 'binary_up5d' scheme was actually 1-day
    (a known bug fixed by this refactor); callers wanting 5-day labels
    should now use `_attach_forward_return_label(horizon_days=5, ...)`.
    """
    return _attach_forward_return_label(
        feature_df, return_panel,
        horizon_days=1, scheme=scheme, threshold=threshold,
    )


# ── Dataset container ─────────────────────────────────────────────────────────

@dataclass
class MLDataset:
    """Train/val/predict split with metadata."""
    X_train: np.ndarray
    y_train: np.ndarray
    X_val: np.ndarray
    y_val: np.ndarray
    feature_names: list[str]

    # Metadata for cross-referencing predictions
    train_meta: pd.DataFrame   # columns: trade_date, sector_code, sector_source
    val_meta: pd.DataFrame

    # For prediction (no labels)
    X_pred: np.ndarray | None = None
    pred_meta: pd.DataFrame | None = None

    @property
    def n_train(self) -> int:
        return len(self.y_train)

    @property
    def n_val(self) -> int:
        return len(self.y_val)

    @property
    def class_balance_train(self) -> dict[int, int]:
        vals, counts = np.unique(self.y_train, return_counts=True)
        return dict(zip(vals.tolist(), counts.tolist()))


def build_dataset(
    engine: Engine,
    *,
    train_start: dt.date,
    train_end: dt.date,
    val_frac: float = 0.20,
    label_scheme: str = "binary_top_quintile",
    label_threshold: float = 0.5,
    label_quantile: float = 0.20,
    horizon_days: int = 1,
    source: str | None = None,
    predict_date: dt.date | None = None,
) -> MLDataset:
    """Build a complete train/val (and optionally predict) dataset.

    Args:
        engine:          SQLAlchemy engine.
        train_start:     Start of the feature+label window.
        train_end:       End of the feature window (labels look horizon_days ahead).
        val_frac:        Fraction of *dates* (not rows) to use as validation.
        label_scheme:    'binary_up' / 'binary_top_quintile' / 'three_class' / 'regression'.
        label_threshold: pct_chg threshold for positive label in 'binary_up' (%).
        label_quantile:  Top fraction for 'binary_top_quintile' (default 0.20).
        horizon_days:    Forward return horizon in trading days (1=short-term RF,
                         20=mid-term XGB).
        source:          Sector source filter or None (all sources).
        predict_date:    If given, build X_pred for this date (no label).

    Returns:
        MLDataset ready for model.fit(ds.X_train, ds.y_train).
    """
    # Need return data extending horizon_days beyond train_end (with calendar
    # padding for weekends/holidays) to label the last feature day
    return_end = train_end + dt.timedelta(days=max(horizon_days * 2 + 7, 7))

    feature_df = build_feature_matrix(engine, train_start, train_end, source=source)
    if feature_df.empty:
        raise RuntimeError(f"No feature data for [{train_start}, {train_end}]")

    return_panel = _build_return_panel(engine, train_start, return_end)
    if return_panel.empty:
        raise RuntimeError("No return data found for label construction")

    labeled = _attach_forward_return_label(
        feature_df, return_panel,
        horizon_days=horizon_days,
        scheme=label_scheme,
        threshold=label_threshold,
        quantile=label_quantile,
    )
    if labeled.empty:
        raise RuntimeError("No labeled rows after join (check data overlap)")

    # Time-based train/val split
    all_dates = sorted(labeled["trade_date"].unique())
    n_val_dates = max(1, int(len(all_dates) * val_frac))
    val_dates = set(all_dates[-n_val_dates:])

    train_mask = ~labeled["trade_date"].isin(val_dates)
    val_mask = labeled["trade_date"].isin(val_dates)

    train_df = labeled[train_mask]
    val_df = labeled[val_mask]

    X_train = train_df[ALL_FEATURE_COLS].values.astype(np.float32)
    y_train = train_df["label"].values.astype(np.int32)
    X_val = val_df[ALL_FEATURE_COLS].values.astype(np.float32)
    y_val = val_df["label"].values.astype(np.int32)

    meta_cols = ["trade_date", "sector_code", "sector_source"]
    ds = MLDataset(
        X_train=X_train, y_train=y_train,
        X_val=X_val, y_val=y_val,
        feature_names=ALL_FEATURE_COLS,
        train_meta=train_df[meta_cols].reset_index(drop=True),
        val_meta=val_df[meta_cols].reset_index(drop=True),
    )

    # Optional predict set (today's features, no labels)
    if predict_date is not None:
        pred_feat = build_feature_matrix(engine, predict_date, predict_date, source=source)
        if not pred_feat.empty:
            ds.X_pred = pred_feat[ALL_FEATURE_COLS].values.astype(np.float32)
            ds.pred_meta = pred_feat[meta_cols].reset_index(drop=True)

    log.info(
        "[dataset] train=%d rows (%d dates), val=%d rows (%d dates), label=%s, balance=%s",
        ds.n_train, len(all_dates) - n_val_dates,
        ds.n_val, n_val_dates,
        label_scheme, ds.class_balance_train,
    )
    return ds
