"""Feature engineering for SmartMoney ML models.

Reads from:
  smartmoney.factor_daily          — 4 raw scores + derived_json
  smartmoney.sector_state_daily    — role + cycle_phase (categorical)
  smartmoney.raw_daily             — for sector lagged returns (sw_l2 only)
  smartmoney.sw_member_monthly     — for sw_l2 sector aggregation
  smartmoney.stock_signals_daily   — for member-stock signal density per sector
  smartmoney.raw_moneyflow_hsgt    — for northbound flow regime
  smartmoney.market_state_daily    — for breadth / limit-up baseline

Output: pd.DataFrame with index (trade_date, sector_code, sector_source)
and feature columns ready for sklearn/XGBoost.

Feature groups:
  F1  raw scores            heat, trend, persistence, crowding (today)
  F2  momentum deltas       heat_delta_1d, heat_delta_3d, trend_delta_1d
  F3  rolling stats         heat_mean_5d, heat_std_5d, heat_pct_rank_10d
  F4  derived ratios        heat_trend_ratio, persist_crowd_ratio, heat_crowd_gap
  F5  role dummies          one-hot of sector role (7 classes)
  F6  cycle dummies         one-hot of cycle_phase (8 stages)
  F7  DC extras             dc_rank_norm, elg_rate (from derived_json if dc)
  F8  cross-sectional ranks heat/trend/persist/crowd pct-rank within trade_date
  F9  lagged returns (NEW)  ret_1d, ret_5d, ret_20d (own sector trailing)
  F10 member signal density (NEW) frac of members tagged 龙头/中军/补涨/趋势
  F11 market regime (NEW)   north_money_5d_avg_zscore, limit_up_count_norm

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


def _add_cross_sectional_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """Per-date cross-sectional pct-rank for the 4 raw factor scores.

    These are arguably the *most important* features for cross-sectional
    cross-sectional model since absolute factor levels are less stable than
    relative rankings (which sectors are top decile *today*).
    """
    df = df.copy()
    for col in ["heat_score", "trend_score", "persistence_score", "crowding_score"]:
        df[f"{col}_xs_rank"] = df.groupby("trade_date")[col].rank(pct=True, method="average")
    return df


def _load_sector_lagged_returns(
    engine: Engine,
    start: dt.date,
    end: dt.date,
    *,
    source: str,
) -> pd.DataFrame:
    """Compute trailing 1d/5d/20d sector returns for the given source.

    For sw_l2: equal-weighted member-stock pct_chg via sw_member_monthly.
    For sw: from raw_sw_daily directly.
    For dc/ths/kpl: skipped (not the focus of B8 sw_l2-first ML).

    Returns: (trade_date, sector_code, sector_source, ret_1d, ret_5d, ret_20d)
    The lagged returns are the *trailing* compounded returns ending on the
    given trade_date — they're features (known at T), not labels (T+N).
    """
    # Pad start by 30 cal days so we have history for the first trade_date
    pad_start = start - dt.timedelta(days=45)

    if source == "sw_l2":
        sql = f"""
            SELECT m.trade_date, sm.l2_code AS sector_code,
                   'sw_l2' AS sector_source, AVG(m.pct_chg) AS pct_chg
            FROM {SCHEMA}.raw_daily m
            JOIN {SCHEMA}.sw_member_monthly sm
              ON m.ts_code = sm.ts_code
             AND sm.snapshot_month = date_trunc('month', m.trade_date)::date
            WHERE m.trade_date BETWEEN :s AND :e
            GROUP BY m.trade_date, sm.l2_code
        """
    elif source == "sw":
        sql = f"""
            SELECT trade_date, ts_code AS sector_code,
                   'sw' AS sector_source, pct_change AS pct_chg
            FROM {SCHEMA}.raw_sw_daily
            WHERE trade_date BETWEEN :s AND :e
        """
    else:
        # No lagged returns for dc/ths/kpl in this pipeline
        return pd.DataFrame(columns=[
            "trade_date", "sector_code", "sector_source",
            "ret_1d", "ret_5d", "ret_20d",
        ])

    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"s": pad_start, "e": end}).fetchall()
    if not rows:
        return pd.DataFrame(columns=[
            "trade_date", "sector_code", "sector_source",
            "ret_1d", "ret_5d", "ret_20d",
        ])

    df = pd.DataFrame(rows, columns=["trade_date", "sector_code", "sector_source", "pct_chg"])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")

    # Compute trailing compound returns per (sector_code, sector_source)
    out_frames = []
    for (code, src), grp in df.groupby(["sector_code", "sector_source"], sort=False):
        grp = grp.sort_values("trade_date").reset_index(drop=True)
        ret_decimal = grp["pct_chg"] / 100.0
        # Compound trailing returns: prod(1+r) - 1 over the window
        # Using rolling apply for each window
        log1p = np.log1p(ret_decimal)
        grp["ret_1d"] = grp["pct_chg"]  # already pct
        grp["ret_5d"] = (np.exp(log1p.rolling(5, min_periods=2).sum()) - 1) * 100
        grp["ret_20d"] = (np.exp(log1p.rolling(20, min_periods=10).sum()) - 1) * 100
        out_frames.append(grp[["trade_date", "sector_code", "sector_source",
                                "ret_1d", "ret_5d", "ret_20d"]])
    if not out_frames:
        return pd.DataFrame(columns=[
            "trade_date", "sector_code", "sector_source",
            "ret_1d", "ret_5d", "ret_20d",
        ])
    out = pd.concat(out_frames, ignore_index=True)
    # Trim padding
    return out[out["trade_date"] >= start].reset_index(drop=True)


def _load_member_signal_density(
    engine: Engine,
    start: dt.date,
    end: dt.date,
    *,
    source: str,
) -> pd.DataFrame:
    """Per (sector, date), fraction of L2 members tagged with each role in
    stock_signals_daily.

    Currently only meaningful for sw_l2 since sw_member_monthly drives the
    join. Returns columns: frac_lead, frac_core, frac_filler, frac_trending.
    """
    if source != "sw_l2":
        return pd.DataFrame(columns=[
            "trade_date", "sector_code", "sector_source",
            "frac_lead", "frac_core", "frac_filler", "frac_trending",
        ])

    sql = f"""
        WITH membership AS (
            SELECT sm.snapshot_month, sm.l2_code, sm.ts_code
            FROM {SCHEMA}.sw_member_monthly sm
            WHERE sm.snapshot_month >= date_trunc('month', :s)::date
              AND sm.snapshot_month <= date_trunc('month', :e)::date
        ),
        sig AS (
            SELECT trade_date, ts_code, role
            FROM {SCHEMA}.stock_signals_daily
            WHERE trade_date BETWEEN :s AND :e
        ),
        joined AS (
            SELECT s.trade_date, m.l2_code,
                   COUNT(*) FILTER (WHERE s.role = '龙头')::float AS n_lead,
                   COUNT(*) FILTER (WHERE s.role = '中军')::float AS n_core,
                   COUNT(*) FILTER (WHERE s.role = '补涨')::float AS n_filler,
                   COUNT(*) FILTER (WHERE s.role = '趋势')::float AS n_trending
            FROM membership m
            JOIN sig s
              ON s.ts_code = m.ts_code
             AND date_trunc('month', s.trade_date)::date = m.snapshot_month
            GROUP BY s.trade_date, m.l2_code
        ),
        sizes AS (
            SELECT snapshot_month, l2_code, COUNT(*)::float AS sz
            FROM membership GROUP BY snapshot_month, l2_code
        )
        SELECT j.trade_date, j.l2_code AS sector_code, 'sw_l2' AS sector_source,
               (j.n_lead     / sz.sz) AS frac_lead,
               (j.n_core     / sz.sz) AS frac_core,
               (j.n_filler   / sz.sz) AS frac_filler,
               (j.n_trending / sz.sz) AS frac_trending
        FROM joined j
        JOIN sizes sz
          ON sz.l2_code = j.l2_code
         AND sz.snapshot_month = date_trunc('month', j.trade_date)::date
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"s": start, "e": end}).fetchall()
    if not rows:
        return pd.DataFrame(columns=[
            "trade_date", "sector_code", "sector_source",
            "frac_lead", "frac_core", "frac_filler", "frac_trending",
        ])
    df = pd.DataFrame(rows, columns=[
        "trade_date", "sector_code", "sector_source",
        "frac_lead", "frac_core", "frac_filler", "frac_trending",
    ])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df


def _load_market_regime_features(
    engine: Engine,
    start: dt.date,
    end: dt.date,
) -> pd.DataFrame:
    """Per-date market-wide regime indicators (broadcasted to all sectors).

    Features:
      - north_money_5d_avg_zscore: 5d trailing avg of north_money normalized
        by 60d std (regime: northbound buying / selling).
      - limit_up_count_norm: today's limit_up_count / 60d mean (sentiment).
      - blow_up_rate: today's blow_up_rate (limit_up failures, fragility).
    """
    pad_start = start - dt.timedelta(days=120)
    sql = f"""
        SELECT m.trade_date,
               m.limit_up_count, m.blow_up_rate,
               h.north_money
        FROM {SCHEMA}.market_state_daily m
        LEFT JOIN {SCHEMA}.raw_moneyflow_hsgt h
               ON h.trade_date = m.trade_date
        WHERE m.trade_date BETWEEN :s AND :e
        ORDER BY m.trade_date
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"s": pad_start, "e": end}).fetchall()
    if not rows:
        return pd.DataFrame(columns=[
            "trade_date", "north_money_5d_avg_z", "limit_up_count_norm", "blow_up_rate",
        ])
    df = pd.DataFrame(rows, columns=[
        "trade_date", "limit_up_count", "blow_up_rate", "north_money",
    ])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["limit_up_count"] = pd.to_numeric(df["limit_up_count"], errors="coerce")
    df["blow_up_rate"] = pd.to_numeric(df["blow_up_rate"], errors="coerce")
    df["north_money"] = pd.to_numeric(df["north_money"], errors="coerce").fillna(0)

    df["north_money_5d_avg"] = df["north_money"].rolling(5, min_periods=2).mean()
    n_std = df["north_money"].rolling(60, min_periods=20).std()
    df["north_money_5d_avg_z"] = df["north_money_5d_avg"] / n_std.replace(0, np.nan)
    lu_mean = df["limit_up_count"].rolling(60, min_periods=20).mean().replace(0, np.nan)
    df["limit_up_count_norm"] = df["limit_up_count"] / lu_mean

    out = df[df["trade_date"] >= start][[
        "trade_date", "north_money_5d_avg_z", "limit_up_count_norm", "blow_up_rate",
    ]].reset_index(drop=True)
    return out


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
# F8 cross-sectional ranks
XS_RANK_FEATURE_COLS = [
    "heat_score_xs_rank", "trend_score_xs_rank",
    "persistence_score_xs_rank", "crowding_score_xs_rank",
]
# F9 lagged returns
LAGGED_RET_COLS = ["ret_1d", "ret_5d", "ret_20d"]
# F10 member signal density
MEMBER_DENSITY_COLS = ["frac_lead", "frac_core", "frac_filler", "frac_trending"]
# F11 market regime
MARKET_REGIME_COLS = ["north_money_5d_avg_z", "limit_up_count_norm", "blow_up_rate"]

ALL_FEATURE_COLS = (
    RAW_FEATURE_COLS
    + ROLE_FEATURE_COLS
    + CYCLE_FEATURE_COLS
    + XS_RANK_FEATURE_COLS
    + LAGGED_RET_COLS
    + MEMBER_DENSITY_COLS
    + MARKET_REGIME_COLS
)


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

    # Pipeline (factor-derived features)
    df = _add_deltas_and_rolling(factor_df)
    df = _add_ratio_features(df)
    df = _extract_dc_extras(df)
    df = _add_role_cycle_dummies(df, state_df)
    df = _add_cross_sectional_ranks(df)

    # F9: lagged returns (sw_l2 derived from members; sw direct; others NaN)
    if source in ("sw_l2", "sw", None):
        # When source is None we'd need separate calls per source — keep simple
        # and only enrich if user filtered to a single source.
        if source:
            ret_df = _load_sector_lagged_returns(engine, start, end, source=source)
            if not ret_df.empty:
                df = df.merge(
                    ret_df,
                    on=["trade_date", "sector_code", "sector_source"],
                    how="left",
                )
        else:
            # source=None: enrich sw_l2 + sw separately, concat
            for src in ("sw_l2", "sw"):
                ret_df = _load_sector_lagged_returns(engine, start, end, source=src)
                if not ret_df.empty:
                    df = df.merge(
                        ret_df,
                        on=["trade_date", "sector_code", "sector_source"],
                        how="left",
                    )

    # F10: member signal density (sw_l2 only)
    if source == "sw_l2" or source is None:
        ms_df = _load_member_signal_density(
            engine, start, end, source=source if source else "sw_l2"
        )
        if not ms_df.empty:
            df = df.merge(
                ms_df,
                on=["trade_date", "sector_code", "sector_source"],
                how="left",
            )

    # F11: market regime (date-level, broadcast to all sectors)
    mr_df = _load_market_regime_features(engine, start, end)
    if not mr_df.empty:
        df = df.merge(mr_df, on="trade_date", how="left")

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
