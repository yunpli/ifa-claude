"""Feature engineering for SmartMoney ML models.

Reads from:
  smartmoney.factor_daily        — 4 raw scores + derived_json
  smartmoney.sector_state_daily  — role + cycle_phase (categorical)

Output: pd.DataFrame with index (trade_date, sector_code, sector_source)
and feature columns ready for sklearn/XGBoost.

Feature groups:
  F1  raw scores       heat, trend, persistence, crowding (today)
  F2  momentum deltas  heat_delta_1d, heat_delta_3d, trend_delta_1d
  F3  rolling stats    heat_mean_5d, heat_std_5d, heat_pct_rank_10d
  F4  derived ratios   heat_trend_ratio, persist_crowd_ratio
  F5  role dummies     one-hot of sector role (6 classes)
  F6  cycle dummies    one-hot of cycle_phase (7 stages)
  F7  DC extras        dc_rank_norm, elg_rate (from derived_json if dc source)

All NaN values are mean-filled per column so sklearn doesn't crash.
All categoricals are one-hot encoded (no ordinal assumption).
"""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"

# Role / cycle label ordering (deterministic column order)
ROLE_LABELS = ["主线", "中军", "轮动", "防守", "催化", "退潮", "未识别"]
CYCLE_LABELS = ["冷", "点火", "确认", "扩散", "高潮", "分歧", "退潮", "未识别"]


def _load_factor_window(
    engine: Engine,
    start: dt.date,
    end: dt.date,
    source: str | None = None,
) -> pd.DataFrame:
    """Load factor_daily rows for a date window, optionally filtered by source."""
    sql = f"""
        SELECT trade_date, sector_code, sector_source, sector_name,
               heat_score, trend_score, persistence_score, crowding_score,
               derived_json
        FROM {SCHEMA}.factor_daily
        WHERE trade_date BETWEEN :start AND :end
        {" AND sector_source = :src" if source else ""}
        ORDER BY sector_code, sector_source, trade_date
    """
    params: dict = {"start": start, "end": end}
    if source:
        params["src"] = source
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "trade_date", "sector_code", "sector_source", "sector_name",
        "heat_score", "trend_score", "persistence_score", "crowding_score",
        "derived_json",
    ])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for c in ["heat_score", "trend_score", "persistence_score", "crowding_score"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _load_sector_states(engine: Engine, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Load role + cycle_phase from sector_state_daily."""
    sql = f"""
        SELECT trade_date, sector_code, sector_source, role, cycle_phase
        FROM {SCHEMA}.sector_state_daily
        WHERE trade_date BETWEEN :start AND :end
        ORDER BY sector_code, sector_source, trade_date
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"start": start, "end": end}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "trade_date", "sector_code", "sector_source", "role", "cycle_phase",
    ])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df


def _add_deltas_and_rolling(df: pd.DataFrame) -> pd.DataFrame:
    """Add momentum delta and rolling stat features, grouped by (code, source)."""
    groups = []
    for (code, src), grp in df.groupby(["sector_code", "sector_source"], sort=False):
        grp = grp.sort_values("trade_date").copy()
        h = grp["heat_score"]
        t = grp["trend_score"]

        grp["heat_delta_1d"] = h.diff(1)
        grp["heat_delta_3d"] = h.diff(3)
        grp["trend_delta_1d"] = t.diff(1)

        grp["heat_mean_5d"] = h.rolling(5, min_periods=2).mean()
        grp["heat_std_5d"] = h.rolling(5, min_periods=2).std()
        grp["heat_pct_rank_10d"] = h.rolling(10, min_periods=5).apply(
            lambda x: float(np.mean(x <= x.iloc[-1])), raw=False
        )
        grp["persist_mean_5d"] = grp["persistence_score"].rolling(5, min_periods=2).mean()

        groups.append(grp)

    return pd.concat(groups, ignore_index=True)


def _add_ratio_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["heat_trend_ratio"] = np.where(
        df["trend_score"].abs() > 1e-9,
        df["heat_score"] / df["trend_score"],
        np.nan,
    )
    df["persist_crowd_ratio"] = np.where(
        df["crowding_score"].abs() > 1e-9,
        df["persistence_score"] / df["crowding_score"],
        np.nan,
    )
    df["heat_crowd_gap"] = df["heat_score"] - df["crowding_score"]
    return df


def _extract_dc_extras(df: pd.DataFrame) -> pd.DataFrame:
    """Extract DC-specific fields from derived_json."""
    def _parse(row: pd.Series) -> pd.Series:
        if row["sector_source"] != "dc":
            return pd.Series({"dc_rank_norm": np.nan, "dc_elg_rate": np.nan})
        raw = row.get("derived_json")
        if not raw:
            return pd.Series({"dc_rank_norm": np.nan, "dc_elg_rate": np.nan})
        try:
            d = raw if isinstance(raw, dict) else json.loads(raw)
            rank = d.get("dc_rank", None)
            elg = d.get("buy_elg_amount_rate", None)
            # Normalise rank: lower rank (closer to 1) → higher score
            rank_norm = max(0.0, 1.0 - (float(rank) / 1013.0)) if rank is not None else np.nan
            return pd.Series({"dc_rank_norm": rank_norm,
                              "dc_elg_rate": float(elg) if elg is not None else np.nan})
        except Exception:
            return pd.Series({"dc_rank_norm": np.nan, "dc_elg_rate": np.nan})

    extras = df.apply(_parse, axis=1)
    return pd.concat([df, extras], axis=1)


def _add_role_cycle_dummies(df: pd.DataFrame, state_df: pd.DataFrame) -> pd.DataFrame:
    """Merge role/cycle and create one-hot dummies."""
    if state_df.empty:
        for lbl in ROLE_LABELS:
            df[f"role_{lbl}"] = 0.0
        for lbl in CYCLE_LABELS:
            df[f"cycle_{lbl}"] = 0.0
        return df

    merged = df.merge(
        state_df[["trade_date", "sector_code", "sector_source", "role", "cycle_phase"]],
        on=["trade_date", "sector_code", "sector_source"],
        how="left",
    )
    merged["role"] = merged["role"].fillna("未识别")
    merged["cycle_phase"] = merged["cycle_phase"].fillna("未识别")

    for lbl in ROLE_LABELS:
        merged[f"role_{lbl}"] = (merged["role"] == lbl).astype(float)
    for lbl in CYCLE_LABELS:
        merged[f"cycle_{lbl}"] = (merged["cycle_phase"] == lbl).astype(float)

    return merged.drop(columns=["role", "cycle_phase"])


# ── Feature column list (canonical order) ────────────────────────────────────

RAW_FEATURE_COLS = [
    # F1
    "heat_score", "trend_score", "persistence_score", "crowding_score",
    # F2
    "heat_delta_1d", "heat_delta_3d", "trend_delta_1d",
    # F3
    "heat_mean_5d", "heat_std_5d", "heat_pct_rank_10d", "persist_mean_5d",
    # F4
    "heat_trend_ratio", "persist_crowd_ratio", "heat_crowd_gap",
    # F7 (DC extras)
    "dc_rank_norm", "dc_elg_rate",
]
ROLE_FEATURE_COLS = [f"role_{lbl}" for lbl in ROLE_LABELS]
CYCLE_FEATURE_COLS = [f"cycle_{lbl}" for lbl in CYCLE_LABELS]
ALL_FEATURE_COLS = RAW_FEATURE_COLS + ROLE_FEATURE_COLS + CYCLE_FEATURE_COLS


def build_feature_matrix(
    engine: Engine,
    start: dt.date,
    end: dt.date,
    *,
    source: str | None = None,
    fill_na: bool = True,
) -> pd.DataFrame:
    """Build the full feature matrix for dates [start, end].

    Args:
        engine:   SQLAlchemy engine.
        start:    First trade date (inclusive).
        end:      Last trade date (inclusive).
        source:   Filter to one sector source ('sw'/'dc'/'ths'/'kpl') or None.
        fill_na:  If True, fill NaN with column mean (required for sklearn).

    Returns:
        DataFrame with (trade_date, sector_code, sector_source, sector_name)
        as the first 4 columns + ALL_FEATURE_COLS as feature columns.
        Rows are sorted by trade_date ascending.
    """
    factor_df = _load_factor_window(engine, start, end, source=source)
    if factor_df.empty:
        log.warning("[features] no factor_daily in [%s, %s]", start, end)
        return pd.DataFrame()

    state_df = _load_sector_states(engine, start, end)

    # Pipeline
    df = _add_deltas_and_rolling(factor_df)
    df = _add_ratio_features(df)
    df = _extract_dc_extras(df)
    df = _add_role_cycle_dummies(df, state_df)
    df = df.sort_values(["trade_date", "sector_code", "sector_source"]).reset_index(drop=True)

    # Ensure all feature columns exist
    for col in ALL_FEATURE_COLS:
        if col not in df.columns:
            df[col] = np.nan

    if fill_na:
        means = df[ALL_FEATURE_COLS].mean()
        df[ALL_FEATURE_COLS] = df[ALL_FEATURE_COLS].fillna(means)

    meta_cols = ["trade_date", "sector_code", "sector_source", "sector_name"]
    return df[meta_cols + ALL_FEATURE_COLS]
