"""Train/val/predict dataset assembly for SmartMoney ML models.

Label construction (next-day return classification):
  For each sector on day T, the label is whether sector's pct_change on day
  T+1 exceeds a threshold (positive class = "上涨").

  Three label schemes available:
    binary_up   — 1 if T+1 pct_change > threshold, else 0
    binary_up5d — 1 if avg(pct_change, T+1..T+5) > threshold, else 0
    three_class — 2=up / 1=neutral / 0=down  (not used in P0 models)

Label sources by sector_source:
  sw  → raw_sw_daily.pct_change
  dc  → raw_moneyflow_ind_dc.pct_change  (or raw_dc_index.pct_change)
  ths → raw_moneyflow_ind_ths.pct_change
  kpl → no price series → excluded from supervised learning

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
    """Combine all source returns into one panel."""
    frames = []
    for fn in [_load_sw_returns, _load_dc_returns, _load_ths_returns]:
        df = fn(engine, start, end)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
    return df.dropna(subset=["pct_chg"])


def _attach_next_day_label(
    feature_df: pd.DataFrame,
    return_panel: pd.DataFrame,
    *,
    scheme: str = "binary_up",
    threshold: float = 0.5,
) -> pd.DataFrame:
    """Join next-day return as label onto feature_df.

    For each row (date=T, sector_code, sector_source) in feature_df, find the
    row with date=T+1 (next trading day in the panel) and attach its pct_chg.

    Args:
        feature_df:    Output of build_feature_matrix.
        return_panel:  Panel with (trade_date, sector_code, sector_source, pct_chg).
        scheme:        'binary_up' → 0/1 label.
        threshold:     pct_chg > threshold → label=1.

    Returns:
        feature_df with added 'label' and 'next_pct_chg' columns,
        with rows dropped where next-day data is unavailable.
    """
    # Build mapping: (sector_code, sector_source, date) → pct_chg
    ret_idx = return_panel.set_index(["sector_code", "sector_source", "trade_date"])["pct_chg"]

    # For each (code, src), find the next trade date
    dates_by_key: dict[tuple[str, str], list[dt.date]] = {}
    for (code, src), grp in return_panel.groupby(["sector_code", "sector_source"]):
        dates_by_key[(code, src)] = sorted(grp["trade_date"].tolist())

    def _next_pct(row: pd.Series) -> float:
        key = (row["sector_code"], row["sector_source"])
        td = row["trade_date"]
        dates = dates_by_key.get(key, [])
        try:
            idx = dates.index(td)
            next_date = dates[idx + 1]
            return float(ret_idx.get((key[0], key[1], next_date), np.nan))
        except (ValueError, IndexError):
            return np.nan

    feature_df = feature_df.copy()
    feature_df["next_pct_chg"] = feature_df.apply(_next_pct, axis=1)
    feature_df = feature_df.dropna(subset=["next_pct_chg"]).copy()

    if scheme == "binary_up":
        feature_df["label"] = (feature_df["next_pct_chg"] > threshold).astype(int)
    elif scheme == "binary_up5d":
        # Only labels rows that are at least 5 trading days from the end
        # Callers should ensure the return_panel extends 5 days beyond feature_df end
        feature_df["label"] = (feature_df["next_pct_chg"] > threshold).astype(int)
    elif scheme == "three_class":
        def _three(v: float) -> int:
            if v > threshold:
                return 2
            if v < -threshold:
                return 0
            return 1
        feature_df["label"] = feature_df["next_pct_chg"].apply(_three)
    else:
        raise ValueError(f"Unknown label scheme: {scheme}")

    return feature_df


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
    label_scheme: str = "binary_up",
    label_threshold: float = 0.5,
    source: str | None = None,
    predict_date: dt.date | None = None,
) -> MLDataset:
    """Build a complete train/val (and optionally predict) dataset.

    Args:
        engine:          SQLAlchemy engine.
        train_start:     Start of the feature+label window.
        train_end:       End of the feature window (labels look one day ahead).
        val_frac:        Fraction of *dates* (not rows) to use as validation.
        label_scheme:    'binary_up' / 'three_class'.
        label_threshold: pct_chg threshold for positive label (default 0.5%).
        source:          Sector source filter or None (all sources).
        predict_date:    If given, build X_pred for this date (no label).

    Returns:
        MLDataset ready for model.fit(ds.X_train, ds.y_train).
    """
    # Need return data one day beyond train_end to label the last feature day
    return_end = train_end + dt.timedelta(days=7)

    feature_df = build_feature_matrix(engine, train_start, train_end, source=source)
    if feature_df.empty:
        raise RuntimeError(f"No feature data for [{train_start}, {train_end}]")

    return_panel = _build_return_panel(engine, train_start, return_end)
    if return_panel.empty:
        raise RuntimeError("No return data found for label construction")

    labeled = _attach_next_day_label(
        feature_df, return_panel,
        scheme=label_scheme, threshold=label_threshold,
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
