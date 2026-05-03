"""Feature engineering for ningbo ML scoring — Phase 3.1.

Produces a feature matrix where each row corresponds to one historical
recommendation in `ningbo.recommendations_daily`.  Used both for training
(combined with labels) and for inference (single-day candidates).

Feature groups (~25 features):

  A. Heuristic baseline (4):
     confidence_score, n_hits, resonance_boost, best_individual_score

  B. Strategy one-hot (4):
     has_sniper, has_basin, has_hyd, is_multi

  C. Sniper-specific (5, NaN if absent):
     sniper_strike_code, sniper_touch_precision, sniper_rebound_strength,
     sniper_vol_contraction, sniper_cross_freshness

  D. Basin-specific (1):
     basin_pattern_strength

  E. HYD-specific (2):
     hyd_weekly_score, hyd_daily_score

  F. Stock context (4, joined from raw_daily / raw_daily_basic):
     log_rec_price, vol_20d, return_20d, turnover_5d_avg

  G. Market context (4, joined from raw_index_daily SSE):
     index_pct_chg, index_5d_return, index_above_ma20, index_5d_vol

  H. Calendar (3):
     day_of_week, month, quarter

Public API:
    build_feature_matrix(engine, rec_date_start, rec_date_end, scoring_mode)
        -> pd.DataFrame  (one row per rec, indexed by [rec_date, ts_code, strategy])

    extract_features_from_meta(meta_dict, strategy_name) -> dict
        Pure function for inference path (single rec, no DB).
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text


# Feature column ordering — IMPORTANT: must match between training and inference
FEATURE_COLUMNS: list[str] = [
    # A. Heuristic baseline (4)
    "confidence_score", "n_hits", "resonance_boost", "best_individual_score",
    # B. Strategy one-hot (4)
    "has_sniper", "has_basin", "has_hyd", "is_multi",
    # C. Sniper specific (5)
    "sniper_strike_code", "sniper_touch_precision", "sniper_rebound_strength",
    "sniper_vol_contraction", "sniper_cross_freshness",
    # D. Basin specific (1)
    "basin_pattern_strength",
    # E. HYD specific (2)
    "hyd_weekly_score", "hyd_daily_score",
    # F. Stock context — basic (4)
    "log_rec_price", "vol_20d", "return_20d", "turnover_5d_avg",
    # F2. Stock context — extended (4)
    "log_market_cap", "vol_surge", "dist_60d_high", "dist_60d_low",
    # G. Market context (4)
    "index_pct_chg", "index_5d_return", "index_above_ma20", "index_5d_vol",
    # G2. Market context — extended (2)
    "index_10d_return", "index_above_ma60",
    # H. Calendar (3)
    "day_of_week", "month", "quarter",
    # I. SW L2 sector momentum (3 — replaces L1 one-hot for higher info density)
    "sector_l2_5d_return", "sector_l2_5d_breadth", "sector_l2_inflow_5d_norm",
    # J. Cross-sectional within day (3)
    "cs_rank_confidence", "cs_n_picks_day", "cs_n_multi_day",
]


_STRIKE_CODE_MAP = {"strike_1": 1, "strike_2": 2, "strike_3p": 3}


# ── Pure feature extractors (no DB) ──────────────────────────────────────────

def extract_features_from_meta(meta: dict | str | None, strategy: str) -> dict[str, float]:
    """Extract feature dict from a single rec's signal_meta JSON.

    Returns features in groups A-E (heuristic + strategy-specific).
    Group F-H features need DB access; computed by build_feature_matrix.
    """
    if meta is None:
        meta = {}
    elif isinstance(meta, str):
        meta = json.loads(meta)

    out: dict[str, float] = {}

    # ── A. Heuristic baseline ────────────────────────────────────────────────
    out["n_hits"]                 = float(meta.get("n_hits", 1))
    out["resonance_boost"]        = float(meta.get("resonance_boost", 0.0))
    out["best_individual_score"]  = float(meta.get("best_individual_score", 0.0))
    # confidence_score is on the rec row itself; caller fills it in

    # ── B. Strategy one-hot ──────────────────────────────────────────────────
    hits = set(meta.get("strategies_hit", [strategy]))
    out["has_sniper"] = float("sniper"           in hits)
    out["has_basin"]  = float("treasure_basin"   in hits)
    out["has_hyd"]    = float("half_year_double" in hits)
    out["is_multi"]   = float(len(hits) >= 2)

    by_strategy = meta.get("by_strategy", {})

    # ── C. Sniper-specific ──────────────────────────────────────────────────
    sniper_meta = (by_strategy.get("sniper") or {}).get("signal_meta", {})
    out["sniper_strike_code"]      = float(_STRIKE_CODE_MAP.get(sniper_meta.get("strike_type"), 0))
    out["sniper_touch_precision"]  = float(sniper_meta.get("touch_precision", 0.0))
    out["sniper_rebound_strength"] = float(sniper_meta.get("rebound_strength", 0.0))
    out["sniper_vol_contraction"]  = float(sniper_meta.get("vol_contraction", 0.0))
    out["sniper_cross_freshness"]  = float(sniper_meta.get("cross_freshness", 0.0))

    # ── D. Basin-specific ───────────────────────────────────────────────────
    basin_meta = (by_strategy.get("treasure_basin") or {}).get("signal_meta", {})
    out["basin_pattern_strength"]  = float(basin_meta.get("pattern_strength",
                                            basin_meta.get("strength_score", 0.0)))

    # ── E. HYD-specific ─────────────────────────────────────────────────────
    hyd_meta = (by_strategy.get("half_year_double") or {}).get("signal_meta", {})
    out["hyd_weekly_score"] = float(hyd_meta.get("weekly_score",
                                                 hyd_meta.get("weekly_macd_strength", 0.0)))
    out["hyd_daily_score"]  = float(hyd_meta.get("daily_score",
                                                 hyd_meta.get("daily_alignment", 0.0)))

    return out


# ── Calendar features (no DB needed) ─────────────────────────────────────────

def _calendar_features(rec_date: dt.date) -> dict[str, float]:
    return {
        "day_of_week": float(rec_date.weekday() + 1),  # 1-5 (Mon-Fri)
        "month":       float(rec_date.month),
        "quarter":     float((rec_date.month - 1) // 3 + 1),
    }


# ── DB-backed feature loaders ────────────────────────────────────────────────

def _load_stock_context(
    engine: Engine, ts_codes: list[str], start: dt.date, end: dt.date
) -> pd.DataFrame:
    """Compute per-(ts_code, rec_date) features from raw_daily + daily_basic.

    Returns columns: ts_code, trade_date, vol_20d, return_20d, turnover_5d_avg,
                     log_market_cap, vol_surge, dist_60d_high, dist_60d_low

    Window functions:
      - vol_20d:        stddev(close)/avg(close) over 20-day window
      - return_20d:     close / close_lag20 - 1
      - turnover_5d:    avg(turnover_rate) over 5-day window
      - log_market_cap: ln(total_mv) — daily snapshot, no smoothing
      - vol_surge:      vol / avg(vol over 20d)
      - dist_60d_high:  (high_60d - close) / high_60d
      - dist_60d_low:   (close - low_60d) / low_60d
    """
    if not ts_codes:
        return pd.DataFrame()

    sql = text("""
        WITH d AS (
            SELECT
                d.ts_code, d.trade_date, d.close, d.high, d.low, d.vol,
                db.turnover_rate, db.total_mv
            FROM smartmoney.raw_daily d
            LEFT JOIN smartmoney.raw_daily_basic db
                   ON d.ts_code = db.ts_code AND d.trade_date = db.trade_date
            WHERE d.ts_code = ANY(:codes)
              AND d.trade_date BETWEEN :start_lookback AND :end
        )
        SELECT
            ts_code, trade_date, close,
            STDDEV(close) OVER w20 / NULLIF(AVG(close) OVER w20, 0)  AS vol_20d,
            close / NULLIF(LAG(close, 20) OVER (
                PARTITION BY ts_code ORDER BY trade_date), 0) - 1   AS return_20d,
            AVG(turnover_rate) OVER w5                              AS turnover_5d_avg,
            LN(GREATEST(total_mv, 0.01))                            AS log_market_cap,
            vol / NULLIF(AVG(vol) OVER w20, 0)                      AS vol_surge,
            (MAX(high) OVER w60 - close) / NULLIF(MAX(high) OVER w60, 0) AS dist_60d_high,
            (close - MIN(low) OVER w60) / NULLIF(MIN(low) OVER w60, 0)   AS dist_60d_low
        FROM d
        WINDOW
            w20 AS (PARTITION BY ts_code ORDER BY trade_date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
            w5  AS (PARTITION BY ts_code ORDER BY trade_date
                    ROWS BETWEEN  4 PRECEDING AND CURRENT ROW),
            w60 AS (PARTITION BY ts_code ORDER BY trade_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
    """)
    # 90 cal days back ≈ 60 trading days warmup for w60
    start_lookback = start - dt.timedelta(days=110)
    df = pd.read_sql(sql, engine, params={
        "codes": ts_codes, "start_lookback": start_lookback, "end": end,
    })
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]


def _load_sector_membership(
    engine: Engine, ts_codes: list[str], start: dt.date, end: dt.date
) -> pd.DataFrame:
    """Look up SW L2 sector for each (ts_code, snapshot_month).

    Returns columns: ts_code, snapshot_month, l2_code
    Used to map a stock at rec_date → its L2 sector at that month.
    """
    if not ts_codes:
        return pd.DataFrame()
    start_month = dt.date(start.year, start.month, 1)
    end_month   = dt.date(end.year,   end.month,   1)
    sql = text("""
        SELECT DISTINCT ts_code, snapshot_month, l2_code
        FROM smartmoney.sw_member_monthly
        WHERE ts_code = ANY(:codes)
          AND snapshot_month BETWEEN :sm AND :em
          AND l2_code IS NOT NULL
    """)
    df = pd.read_sql(sql, engine, params={"codes": ts_codes, "sm": start_month, "em": end_month})
    df["snapshot_month"] = pd.to_datetime(df["snapshot_month"]).dt.date
    return df


def _load_l2_momentum(
    engine: Engine, start: dt.date, end: dt.date
) -> pd.DataFrame:
    """Compute per-(trade_date, l2_code) momentum features.

    Returns columns: trade_date, l2_code,
                     sector_l2_5d_return    (avg of member 5d returns)
                     sector_l2_5d_breadth   (fraction of members up over 5d)
                     sector_l2_inflow_5d_norm (sum 5d main net inflow / stock_count)

    Strategy:
      - For sector_l2_inflow_5d_norm: use sector_moneyflow_sw_daily (already
        aggregated; very fast).
      - For sector_l2_5d_return / breadth: aggregate raw_daily JOIN
        sw_member_monthly (~3M rows × window functions; ~30s).
    """
    # Lookback for 5d returns: need at least 10 trading days (~14 cal days)
    lb = start - dt.timedelta(days=20)

    # ── Inflow per L2 from pre-aggregated table ─────────────────────────────
    inflow_sql = text("""
        SELECT
            trade_date,
            l2_code,
            SUM(net_amount)  OVER w5 / NULLIF(SUM(stock_count) OVER w5, 0)
                AS sector_l2_inflow_5d_norm
        FROM smartmoney.sector_moneyflow_sw_daily
        WHERE trade_date BETWEEN :lb AND :end
        WINDOW w5 AS (PARTITION BY l2_code ORDER BY trade_date
                      ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
    """)
    inflow_df = pd.read_sql(inflow_sql, engine, params={"lb": lb, "end": end})

    # ── 5d return + breadth per L2 from raw_daily JOIN sw_member_monthly ────
    return_sql = text("""
        WITH stock_5d AS (
            SELECT
                d.ts_code,
                d.trade_date,
                d.close / NULLIF(LAG(d.close, 5) OVER (
                    PARTITION BY d.ts_code ORDER BY d.trade_date), 0) - 1 AS r5d
            FROM smartmoney.raw_daily d
            WHERE d.trade_date BETWEEN :lb AND :end
        ),
        with_l2 AS (
            SELECT
                s5.trade_date,
                sm.l2_code,
                s5.r5d
            FROM stock_5d s5
            JOIN smartmoney.sw_member_monthly sm
              ON sm.ts_code = s5.ts_code
             AND sm.snapshot_month = date_trunc('month', s5.trade_date)::date
            WHERE s5.r5d IS NOT NULL
        )
        SELECT
            trade_date,
            l2_code,
            AVG(r5d)                              AS sector_l2_5d_return,
            AVG(CASE WHEN r5d > 0 THEN 1.0 ELSE 0.0 END) AS sector_l2_5d_breadth
        FROM with_l2
        GROUP BY trade_date, l2_code
    """)
    return_df = pd.read_sql(return_sql, engine, params={"lb": lb, "end": end})

    # Merge inflow + return on (trade_date, l2_code)
    if inflow_df.empty and return_df.empty:
        return pd.DataFrame()
    if inflow_df.empty:
        out = return_df
        out["sector_l2_inflow_5d_norm"] = np.nan
    elif return_df.empty:
        out = inflow_df
        out["sector_l2_5d_return"]  = np.nan
        out["sector_l2_5d_breadth"] = np.nan
    else:
        out = return_df.merge(inflow_df, on=["trade_date", "l2_code"], how="outer")

    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.date
    return out[(out["trade_date"] >= start) & (out["trade_date"] <= end)]


def _attach_sector_features(
    rec_df: pd.DataFrame,
    membership_df: pd.DataFrame,
    momentum_df: pd.DataFrame,
) -> pd.DataFrame:
    """Add 3 L2 momentum columns to rec_df via (ts_code → l2_code → momentum)."""
    out = rec_df.copy()
    momentum_cols = ["sector_l2_5d_return", "sector_l2_5d_breadth", "sector_l2_inflow_5d_norm"]

    if membership_df.empty or momentum_df.empty:
        for c in momentum_cols:
            out[c] = np.nan
        return out

    # Join 1: rec → l2_code via snapshot_month lookup
    out["snapshot_month"] = out["rec_date"].apply(lambda d: dt.date(d.year, d.month, 1))
    out = out.merge(membership_df, on=["ts_code", "snapshot_month"], how="left")

    # Join 2: (rec_date, l2_code) → momentum
    out = out.merge(
        momentum_df, left_on=["rec_date", "l2_code"], right_on=["trade_date", "l2_code"],
        how="left",
    )
    out = out.drop(columns=["snapshot_month", "l2_code", "trade_date"], errors="ignore")
    return out


def _load_market_context(
    engine: Engine, start: dt.date, end: dt.date,
) -> pd.DataFrame:
    """Compute per-rec_date market context from raw_index_daily SSE (000001.SH).

    Returns: trade_date, index_pct_chg, index_5d_return, index_above_ma20,
             index_5d_vol, index_10d_return, index_above_ma60
    """
    sql = text("""
        WITH idx AS (
            SELECT trade_date, close, pct_chg
            FROM smartmoney.raw_index_daily
            WHERE ts_code = '000001.SH'
              AND trade_date BETWEEN :start_lookback AND :end
        )
        SELECT
            trade_date,
            pct_chg AS index_pct_chg,
            close / NULLIF(LAG(close,  5) OVER (ORDER BY trade_date), 0) - 1 AS index_5d_return,
            close / NULLIF(LAG(close, 10) OVER (ORDER BY trade_date), 0) - 1 AS index_10d_return,
            CASE WHEN close > AVG(close) OVER w20 THEN 1.0 ELSE 0.0 END AS index_above_ma20,
            CASE WHEN close > AVG(close) OVER w60 THEN 1.0 ELSE 0.0 END AS index_above_ma60,
            STDDEV(pct_chg) OVER w5 AS index_5d_vol
        FROM idx
        WINDOW
            w60 AS (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW),
            w20 AS (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
            w5  AS (ORDER BY trade_date ROWS BETWEEN  4 PRECEDING AND CURRENT ROW)
    """)
    start_lookback = start - dt.timedelta(days=110)
    df = pd.read_sql(sql, engine, params={"start_lookback": start_lookback, "end": end})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]


def _add_cross_sectional_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute within-day cross-sectional features (rank, counts).

    Operates on the assembled feature df after all per-rec features are built.
    """
    out = df.copy()
    # Rank by confidence_score within rec_date (1 = highest conf)
    out["cs_rank_confidence"] = out.groupby("rec_date")["confidence_score"].rank(
        method="min", ascending=False
    )
    # Total number of picks in this day
    out["cs_n_picks_day"] = out.groupby("rec_date")["confidence_score"].transform("count")
    # Number of multi-strategy picks in this day
    out["cs_n_multi_day"] = out.groupby("rec_date")["is_multi"].transform("sum")
    return out


# ── Main builder ─────────────────────────────────────────────────────────────

def build_feature_matrix(
    engine: Engine,
    rec_date_start: dt.date,
    rec_date_end: dt.date,
    *,
    scoring_mode: str = "heuristic",
    include_outcomes: bool = True,
) -> pd.DataFrame:
    """Build the full feature matrix for [rec_date_start, rec_date_end].

    Args:
        engine: SQLAlchemy engine.
        rec_date_start, rec_date_end: Inclusive date range over rec_date.
        scoring_mode: Filter recommendations by source ('heuristic' for training).
        include_outcomes: If True, joins with recommendation_outcomes and adds
            label columns: y_take_profit (binary), y_final_return (regression),
            outcome_status (categorical, including 'in_progress').

    Returns:
        DataFrame with FEATURE_COLUMNS + (optional label cols), indexed by
        a normal RangeIndex but containing rec_date / ts_code / strategy /
        scoring_mode columns for joining/auditing.
    """
    # ── 1. Pull recommendations + signal_meta ────────────────────────────────
    sql = """
        SELECT r.rec_date, r.ts_code, r.strategy, r.scoring_mode,
               r.confidence_score, r.rec_price, r.rec_signal_meta
    """
    if include_outcomes:
        sql += """,
               o.outcome_status,
               o.final_cum_return,
               o.peak_cum_return,
               o.outcome_track_day
        """
    sql += """
        FROM ningbo.recommendations_daily r
    """
    if include_outcomes:
        sql += """
        LEFT JOIN ningbo.recommendation_outcomes o
               ON r.rec_date = o.rec_date
              AND r.ts_code  = o.ts_code
              AND r.strategy = o.strategy
              AND r.scoring_mode = o.scoring_mode
        """
    sql += """
        WHERE r.scoring_mode = :sm
          AND r.rec_date BETWEEN :start AND :end
        ORDER BY r.rec_date, r.ts_code, r.strategy
    """
    recs = pd.read_sql(text(sql), engine, params={
        "sm": scoring_mode, "start": rec_date_start, "end": rec_date_end,
    })
    if recs.empty:
        return recs

    # ── 2. Extract features per row from signal_meta + calendar ──────────────
    feat_rows: list[dict[str, Any]] = []
    for r in recs.itertuples(index=False):
        meta = r.rec_signal_meta
        feats = extract_features_from_meta(meta, r.strategy)
        feats["confidence_score"] = float(r.confidence_score)
        feats.update(_calendar_features(r.rec_date))
        feat_rows.append(feats)
    feat_df = pd.DataFrame(feat_rows)

    # ── 3. Join stock + market + sector context ──────────────────────────────
    ts_codes = recs["ts_code"].unique().tolist()
    stock_ctx     = _load_stock_context(engine, ts_codes, rec_date_start, rec_date_end)
    mkt_ctx       = _load_market_context(engine,           rec_date_start, rec_date_end)
    sec_member_df = _load_sector_membership(engine, ts_codes, rec_date_start, rec_date_end)
    sec_momentum  = _load_l2_momentum(engine,                rec_date_start, rec_date_end)

    base = recs[["rec_date", "ts_code", "strategy", "scoring_mode", "rec_price"]].copy()

    # Stock context (vol/return/turnover + log_market_cap + vol_surge + dist_60d_*)
    stock_cols = ["vol_20d", "return_20d", "turnover_5d_avg",
                  "log_market_cap", "vol_surge", "dist_60d_high", "dist_60d_low"]
    if not stock_ctx.empty:
        base = base.merge(
            stock_ctx[["ts_code", "trade_date"] + stock_cols],
            left_on=["ts_code", "rec_date"], right_on=["ts_code", "trade_date"],
            how="left",
        ).drop(columns=["trade_date"])
    else:
        for c in stock_cols:
            base[c] = np.nan

    # Market context (index pct/5d/10d/ma20/ma60/vol)
    if not mkt_ctx.empty:
        base = base.merge(mkt_ctx, left_on="rec_date", right_on="trade_date", how="left")
        base = base.drop(columns=["trade_date"], errors="ignore")
    else:
        for c in ("index_pct_chg", "index_5d_return", "index_above_ma20",
                  "index_5d_vol", "index_10d_return", "index_above_ma60"):
            base[c] = np.nan

    # Sector momentum (L2): attach sector_l2_5d_return / breadth / inflow
    base = _attach_sector_features(base, sec_member_df, sec_momentum)

    # ── 4. Combine into feat_df ──────────────────────────────────────────────
    feat_df["log_rec_price"]    = np.log(np.maximum(base["rec_price"].astype(float), 0.01))
    for c in stock_cols + [
        "index_pct_chg", "index_5d_return", "index_above_ma20", "index_5d_vol",
        "index_10d_return", "index_above_ma60",
        "sector_l2_5d_return", "sector_l2_5d_breadth", "sector_l2_inflow_5d_norm",
    ]:
        feat_df[c] = base[c].astype(float)

    # ── 5. Cross-sectional within-day features ───────────────────────────────
    feat_df["rec_date"] = recs["rec_date"].values  # temp for groupby
    feat_df = _add_cross_sectional_features(feat_df)
    feat_df = feat_df.drop(columns=["rec_date"])

    # ── 6. Reorder columns + attach IDs ──────────────────────────────────────
    out = pd.concat([
        recs[["rec_date", "ts_code", "strategy", "scoring_mode"]].reset_index(drop=True),
        feat_df[FEATURE_COLUMNS].reset_index(drop=True),
    ], axis=1)

    if include_outcomes:
        out["outcome_status"]    = recs["outcome_status"].values
        out["final_cum_return"]  = recs["final_cum_return"].values
        out["peak_cum_return"]   = recs["peak_cum_return"].values
        out["y_take_profit"]     = (recs["outcome_status"] == "take_profit").astype(int)
        out["y_final_return"]    = recs["final_cum_return"].astype(float)

    return out


def select_trainable(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to rows with terminal outcome (drop in_progress, drop NaN labels)."""
    if "outcome_status" not in df.columns:
        return df
    mask = df["outcome_status"].isin(["take_profit", "stop_loss", "expired"])
    return df[mask].copy()
