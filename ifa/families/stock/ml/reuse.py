"""Adapters that reuse existing SmartMoney / Ningbo / Kronos model assets.

Stock Edge should not train or maintain a parallel model stack when adjacent
families already own production models. This module turns those existing
outputs into single-stock evidence:

* SmartMoney RF/XGB sector models score the target stock's SW L2 sector.
* Ningbo active aggressive/conservative models score the target if it is in
  the stored Ningbo candidate pool.
* Kronos cached embeddings are attached when Ningbo has already computed them.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text


def load_reused_model_context(
    engine: Engine,
    *,
    ts_code: str,
    as_of_trade_date: dt.date,
    sector_data: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "smartmoney_sector": _smartmoney_sector_score(engine, sector_data, as_of_trade_date),
        "ningbo_candidate": _ningbo_candidate_score(engine, ts_code, as_of_trade_date),
        "kronos": _kronos_cache_signal(ts_code, as_of_trade_date),
        "kronos_analog": _kronos_analog_signal(engine, ts_code, as_of_trade_date),
        "llm_regime": _smartmoney_llm_regime(engine, as_of_trade_date),
        "llm_counterfactual": _smartmoney_llm_counterfactual(engine, ts_code, as_of_trade_date),
    }


def _smartmoney_sector_score(
    engine: Engine,
    sector_data: dict[str, Any] | None,
    as_of_trade_date: dt.date,
) -> dict[str, Any]:
    l2_code = (sector_data or {}).get("l2_code")
    if not l2_code:
        return {"available": False, "message": "缺少 SW L2 行业归属，无法复用 SmartMoney 板块模型。"}

    try:
        from ifa.families.smartmoney.ml.features import ALL_FEATURE_COLS, build_feature_matrix
        from ifa.families.smartmoney.ml.persistence import load_model

        features = build_feature_matrix(engine, as_of_trade_date, as_of_trade_date, source="sw_l2")
        if features.empty:
            return {"available": False, "message": "SmartMoney sw_l2 特征矩阵为空。"}
        row = features[features["sector_code"] == l2_code]
        if row.empty:
            return {"available": False, "message": f"SmartMoney 模型特征缺少目标行业 {l2_code}。"}

        versions = ["v2026_05", "v2026_04"]
        last_error = None
        for version in versions:
            try:
                rf = load_model("random_forest", version)
                xgb = load_model("xgboost", version)
                X = row[list(ALL_FEATURE_COLS)].astype("float32")
                with _quiet_feature_name_warnings():
                    rf_proba = float(rf.predict_proba(X)[0])
                    xgb_proba = float(xgb.predict_proba(X)[0])
                return {
                    "available": True,
                    "model_family": "SmartMoney",
                    "rf_proba": rf_proba,
                    "xgb_proba": xgb_proba,
                    "version": version,
                    "sector_code": l2_code,
                    "sector_name": row.iloc[0].get("sector_name"),
                    "message": "复用 SmartMoney RF/XGB SW L2 板块模型。",
                }
            except FileNotFoundError as exc:
                last_error = exc
        return {"available": False, "message": f"SmartMoney RF/XGB 模型文件未找到：{last_error}"}
    except Exception as exc:
        return {"available": False, "message": f"SmartMoney 模型复用失败：{type(exc).__name__}: {exc}"}


def _ningbo_candidate_score(engine: Engine, ts_code: str, as_of_trade_date: dt.date) -> dict[str, Any]:
    try:
        candidates = pd.read_sql(
            text("""
                SELECT rec_date, ts_code, strategy, confidence_score,
                       rec_price, signal_meta AS rec_signal_meta
                FROM ningbo.candidates_daily
                WHERE rec_date = :d AND ts_code = :ts_code
                ORDER BY confidence_score DESC
            """),
            engine,
            params={"d": as_of_trade_date, "ts_code": ts_code},
        )
        if candidates.empty:
            return {"available": False, "message": "目标股未进入当日宁波候选池。"}

        from ifa.families.ningbo.ml.dual_scorer import score_with_active_models

        with _quiet_feature_name_warnings():
            scores = score_with_active_models(engine, candidates, as_of_trade_date)
        out: dict[str, Any] = {
            "available": True,
            "model_family": "Ningbo",
            "candidate_count": int(len(candidates)),
            "strategies": sorted(candidates["strategy"].astype(str).unique().tolist()),
            "heuristic": _max_score(scores.get("heuristic")),
            "aggressive": _max_score(scores.get("ml_aggressive")),
            "conservative": _max_score(scores.get("ml_conservative")),
            "message": "复用宁波 active aggressive/conservative 模型。",
        }
        return out
    except Exception as exc:
        return {"available": False, "message": f"宁波模型复用失败：{type(exc).__name__}: {exc}"}


def _kronos_cache_signal(ts_code: str, as_of_trade_date: dt.date) -> dict[str, Any]:
    try:
        from ifa.config import get_settings
        from ifa.families.ningbo.ml.kronos_features import EMBEDDING_DIM, KRONOS_TOKENIZER_ID, LOOKBACK_BARS

        root = Path(get_settings().output_root).parent / "embeddings" / "ningbo" / "kronos_small_v1"
        if not root.exists():
            return {"available": False, "message": "本地无宁波 Kronos embedding cache。"}
        files = sorted(root.glob("emb_*.parquet")) + sorted(root.glob("emb_*.pkl"))
        if not files:
            return {"available": False, "message": "Kronos embedding cache 目录为空。"}
        frames = []
        for path in files:
            if path.suffix == ".parquet":
                df = pd.read_parquet(path, columns=["rec_date", "ts_code", "kronos_emb_0"])
            else:
                df = pd.read_pickle(path)[["rec_date", "ts_code", "kronos_emb_0"]]
            frames.append(df[(pd.to_datetime(df["rec_date"]).dt.date == as_of_trade_date) & (df["ts_code"] == ts_code)])
        hit = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if hit.empty:
            return {"available": False, "message": "目标股当日无 Kronos cached embedding。"}
        return {
            "available": True,
            "model_family": "Kronos",
            "model_id": KRONOS_TOKENIZER_ID,
            "lookback_bars": LOOKBACK_BARS,
            "embedding_dim": EMBEDDING_DIM,
            "message": "复用宁波 Kronos tokenizer embedding cache。",
        }
    except Exception as exc:
        return {"available": False, "message": f"Kronos cache 读取失败：{type(exc).__name__}: {exc}"}


def _kronos_analog_signal(engine: Engine, ts_code: str, as_of_trade_date: dt.date) -> dict[str, Any]:
    """Use cached Kronos embeddings as a no-lookahead analog distribution.

    Ningbo owns the embedding production job. Stock Edge consumes that cache
    and labels nearest neighbors through local raw_daily only when their future
    20/40-trading-day path is already observable before the report cutoff.
    """
    try:
        from ifa.config import get_settings

        cfg = {
            "max_scan_rows": 250_000,
            "max_neighbors": 96,
            "min_valid_analogs": 12,
            "max_horizon_bars": 40,
            "cutoff_calendar_buffer_days": 70,
        }
        root = Path(get_settings().output_root).parent / "embeddings" / "ningbo" / "kronos_small_v1"
        files = sorted(root.glob("emb_*.parquet")) + sorted(root.glob("emb_*.pkl"))
        if not files:
            return {"available": False, "message": "Kronos analog cache 目录为空。"}

        emb = _load_kronos_embedding_frame(files, max_rows=int(cfg["max_scan_rows"]))
        if emb.empty:
            return {"available": False, "message": "Kronos embedding frame 为空。"}
        emb["rec_date"] = pd.to_datetime(emb["rec_date"]).dt.date
        emb_cols = [c for c in emb.columns if c.startswith("kronos_emb_")]
        target_rows = emb[(emb["ts_code"] == ts_code) & (emb["rec_date"] <= as_of_trade_date)].sort_values("rec_date")
        if target_rows.empty:
            return {"available": False, "message": "目标股没有可用于近邻搜索的 Kronos embedding。"}

        target = target_rows.iloc[-1]
        target_date = target["rec_date"]
        cutoff = as_of_trade_date - dt.timedelta(days=int(cfg["cutoff_calendar_buffer_days"]))
        candidates = emb[emb["rec_date"] <= cutoff].copy()
        candidates = candidates[~((candidates["ts_code"] == ts_code) & (candidates["rec_date"] == target_date))]
        if candidates.empty:
            return {"available": False, "message": "无满足无前视约束的 Kronos 历史近邻。"}

        target_vec = target[emb_cols].astype("float32").to_numpy()
        matrix = candidates[emb_cols].astype("float32").to_numpy()
        sims = _cosine_similarity(matrix, target_vec)
        candidates = candidates.assign(similarity=sims)
        analogs = candidates.sort_values("similarity", ascending=False).head(int(cfg["max_neighbors"]))
        outcomes = _label_kronos_analog_outcomes(
            engine,
            analogs[["rec_date", "ts_code", "similarity"]],
            as_of_trade_date=as_of_trade_date,
            max_horizon_bars=int(cfg["max_horizon_bars"]),
        )
        if len(outcomes) < int(cfg["min_valid_analogs"]):
            return {
                "available": False,
                "message": f"Kronos 有效近邻只有 {len(outcomes)} 个，低于 {cfg['min_valid_analogs']} 个。",
                "raw_neighbor_count": int(len(analogs)),
            }

        top = outcomes.head(32)
        path_clusters = _kronos_path_cluster_distribution(top)
        expected_20 = float(top["ret_20d"].mean())
        expected_40 = float(top["ret_40d"].mean())
        max_40 = float(top["max_ret_40d"].mean())
        drawdown_40 = float(top["min_ret_40d"].mean())
        hit_20_20 = float((top["max_ret_20d"] >= 0.20).mean())
        hit_30_40 = float((top["max_ret_40d"] >= 0.30).mean())
        hit_50_40 = float((top["max_ret_40d"] >= 0.50).mean())
        stop_first = float((top["min_ret_40d"] <= -0.12).mean())
        return {
            "available": True,
            "model_family": "Kronos",
            "target_embedding_date": str(target_date),
            "analog_count": int(len(top)),
            "raw_neighbor_count": int(len(analogs)),
            "avg_similarity": float(top["similarity"].mean()),
            "expected_return_20d": expected_20,
            "expected_return_40d": expected_40,
            "avg_max_return_40d": max_40,
            "avg_drawdown_40d": drawdown_40,
            "hit_20pct_20d": hit_20_20,
            "hit_30pct_40d": hit_30_40,
            "hit_50pct_40d": hit_50_40,
            "stop_12pct_first_rate": stop_first,
            "path_cluster_distribution": path_clusters["distribution"],
            "dominant_path_cluster": path_clusters["dominant"],
            "path_cluster_edge": path_clusters["edge"],
            "top_cases": top.head(5)[["ts_code", "rec_date", "similarity", "max_ret_40d", "ret_40d"]].to_dict("records"),
            "message": "Kronos embedding 近邻已用本地 raw_daily 标注未来路径。",
        }
    except Exception as exc:
        return {"available": False, "message": f"Kronos 近邻分析失败：{type(exc).__name__}: {exc}"}


def _load_kronos_embedding_frame(files: list[Path], *, max_rows: int) -> pd.DataFrame:
    frames = []
    row_count = 0
    for path in files:
        if path.suffix == ".parquet":
            df = pd.read_parquet(path)
        else:
            df = pd.read_pickle(path)
        frames.append(df)
        row_count += len(df)
        if row_count >= max_rows:
            break
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(out) > max_rows:
        out = out.tail(max_rows).reset_index(drop=True)
    return out


def _cosine_similarity(matrix: np.ndarray, target: np.ndarray) -> np.ndarray:
    target_norm = np.linalg.norm(target) + 1e-9
    row_norms = np.linalg.norm(matrix, axis=1) + 1e-9
    return (matrix @ target) / (row_norms * target_norm)


def _label_kronos_analog_outcomes(
    engine: Engine,
    analogs: pd.DataFrame,
    *,
    as_of_trade_date: dt.date,
    max_horizon_bars: int,
) -> pd.DataFrame:
    if analogs.empty:
        return pd.DataFrame()
    ts_codes = sorted(analogs["ts_code"].astype(str).unique().tolist())
    start_date = min(analogs["rec_date"])
    daily = pd.read_sql(
        text("""
            SELECT ts_code, trade_date, close, high, low
            FROM smartmoney.raw_daily
            WHERE ts_code = ANY(:ts_codes)
              AND trade_date >= :start_date
              AND trade_date <= :end_date
            ORDER BY ts_code, trade_date
        """),
        engine,
        params={"ts_codes": ts_codes, "start_date": start_date, "end_date": as_of_trade_date},
    )
    if daily.empty:
        return pd.DataFrame()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"]).dt.date
    grouped = {code: df.reset_index(drop=True) for code, df in daily.groupby("ts_code", sort=False)}
    rows: list[dict[str, Any]] = []
    for analog in analogs.itertuples(index=False):
        bars = grouped.get(str(analog.ts_code))
        if bars is None or bars.empty:
            continue
        pos = bars.index[bars["trade_date"] >= analog.rec_date]
        if len(pos) == 0:
            continue
        idx = int(pos[0])
        future = bars.iloc[idx + 1 : idx + 1 + max_horizon_bars]
        if len(future) < 20:
            continue
        entry = float(bars.iloc[idx]["close"])
        if entry <= 0:
            continue
        first20 = future.head(20)
        first40 = future.head(40)
        rows.append(
            {
                "ts_code": str(analog.ts_code),
                "rec_date": analog.rec_date,
                "similarity": float(analog.similarity),
                "ret_20d": float(first20.iloc[-1]["close"] / entry - 1.0),
                "ret_40d": float(first40.iloc[-1]["close"] / entry - 1.0),
                "max_ret_20d": float(first20["high"].max() / entry - 1.0),
                "max_ret_40d": float(first40["high"].max() / entry - 1.0),
                "min_ret_40d": float(first40["low"].min() / entry - 1.0),
            }
        )
    return pd.DataFrame(rows).sort_values("similarity", ascending=False).reset_index(drop=True)


def _kronos_path_cluster_distribution(outcomes: pd.DataFrame) -> dict[str, Any]:
    counts = {
        "right_tail": 0,
        "swing_up": 0,
        "grind_up": 0,
        "pop_and_fade": 0,
        "range_chop": 0,
        "stop_first": 0,
    }
    if outcomes.empty:
        return {"distribution": counts, "dominant": None, "edge": 0.0}
    for row in outcomes.itertuples(index=False):
        max40 = float(row.max_ret_40d)
        ret40 = float(row.ret_40d)
        min40 = float(row.min_ret_40d)
        max20 = float(row.max_ret_20d)
        if max40 >= 0.50:
            key = "right_tail"
        elif min40 <= -0.12 and max40 < 0.20:
            key = "stop_first"
        elif max40 >= 0.30 and ret40 >= 0.10:
            key = "swing_up"
        elif max20 >= 0.20 and ret40 < 0.02:
            key = "pop_and_fade"
        elif ret40 >= 0.08:
            key = "grind_up"
        else:
            key = "range_chop"
        counts[key] += 1
    total = max(1, sum(counts.values()))
    distribution = {key: value / total for key, value in counts.items()}
    dominant = max(distribution, key=distribution.get)
    edge = (
        0.48 * distribution["right_tail"]
        + 0.30 * distribution["swing_up"]
        + 0.16 * distribution["grind_up"]
        - 0.26 * distribution["pop_and_fade"]
        - 0.38 * distribution["stop_first"]
    )
    return {"distribution": distribution, "dominant": dominant, "edge": float(edge)}


def _smartmoney_llm_regime(engine: Engine, as_of_trade_date: dt.date) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT trade_date, regime_label, confidence, transition_risk,
                           regime_narrative, factor_weight_adj, model_used
                    FROM smartmoney.llm_regime_states
                    WHERE trade_date <= :d
                    ORDER BY trade_date DESC
                    LIMIT 1
                """),
                {"d": as_of_trade_date},
            ).mappings().fetchone()
        if row is None:
            return {"available": False, "message": "本地无 SmartMoney LLM regime cache。"}
        data = dict(row)
        weight_adj = data.get("factor_weight_adj") or {}
        if isinstance(weight_adj, dict):
            data["recommended_tilt"] = weight_adj.get("recommended_tilt") or weight_adj.get("tilt")
        return {"available": True, **data, "message": "复用 SmartMoney LLM regime cache。"}
    except Exception as exc:
        return {"available": False, "message": f"SmartMoney LLM regime cache 不可用：{type(exc).__name__}: {exc}"}


def _smartmoney_llm_counterfactual(engine: Engine, ts_code: str, as_of_trade_date: dt.date) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT trade_date, ts_code, role, robustness_verdict,
                           invalidation_paths, counterfactual_narrative,
                           risk_factors, model_used
                    FROM smartmoney.llm_counterfactuals
                    WHERE ts_code = :ts_code AND trade_date <= :d
                    ORDER BY trade_date DESC
                    LIMIT 1
                """),
                {"ts_code": ts_code, "d": as_of_trade_date},
            ).mappings().fetchone()
        if row is None:
            return {"available": False, "message": "目标股无 SmartMoney LLM counterfactual cache。"}
        return {"available": True, **dict(row), "message": "复用 SmartMoney LLM counterfactual cache。"}
    except Exception as exc:
        return {"available": False, "message": f"SmartMoney LLM counterfactual cache 不可用：{type(exc).__name__}: {exc}"}


def _max_score(values: Any) -> float | None:
    if values is None:
        return None
    try:
        arr = list(values)
    except TypeError:
        return None
    if not arr:
        return None
    return float(max(arr))


@contextmanager
def _quiet_feature_name_warnings():
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="X .*feature names.*", category=UserWarning)
        yield
