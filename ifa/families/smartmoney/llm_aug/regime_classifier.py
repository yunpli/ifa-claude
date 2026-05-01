"""SmartMoney LLM Augmentation — C1: Market Regime Classifier.

Uses LLM to synthesize macro signals, factor score distributions, and sector
rotation patterns into a human-interpretable market regime label — and to
recommend how to re-weight SmartMoney factors for that regime.

Workflow
--------
1. Load recent market state: market_state_daily (macro), sector_state_daily
   (rotation signals), and factor_daily aggregated statistics.
2. Compute derived signals: factor breadth, IC stability proxy, cross-sector
   dispersion, rolling regime momentum.
3. Call LLM to classify regime and explain it.
4. Persist to smartmoney.llm_regime_states.
5. Return a RegimeState dataclass for downstream use (factor weight adjustment,
   evening report narrative, signal filtering).

Regime Labels
-------------
    risk_on_growth      — broad advance, growth factors working, flow-in across sectors
    defensive_rotation  — quality/dividend flows, crowding reversal, rotation to safety
    bear_squeeze        — short-covering rally, weak breadth, high crowding in survivors
    thematic_frenzy     — narrow theme concentration, top-list dominated by one theme
    pre_breakout        — base-building, low dispersion, coiling for directional move
    consolidation       — range-bound, weak factor IC, no clear sector leadership
    distribution        — institutional exit, high crowding + weakening persistence
    neutral             — LLM cannot confidently classify given current signals

DB Table
--------
    smartmoney.llm_regime_states
    ─────────────────────────────
    regime_id               UUID PK
    trade_date              DATE UNIQUE
    regime_label            TEXT
    confidence              FLOAT          0.0-1.0 (LLM self-assessed)
    regime_narrative        TEXT           2-4 sentence explanation (CN preferred)
    factor_weight_adj       JSONB          {"heat": 1.2, "trend": 0.9, ...}
    regime_duration_est     INT            estimated days this regime has been active
    transition_risk         TEXT           "high" | "medium" | "low"
    prior_regime_label      TEXT           previous day's regime (for transition tracking)
    model_used              TEXT
    latency_seconds         FLOAT
    created_at              TIMESTAMPTZ

Usage
-----
    from ifa.families.smartmoney.llm_aug.regime_classifier import run_regime_classifier

    state = run_regime_classifier(engine, trade_date=dt.date(2026, 4, 30), lookback_days=20)
    print(state.regime_label, state.confidence, state.factor_weight_adj)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.llm.client import LLMClient

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"

# ── Valid regime labels ───────────────────────────────────────────────────────

REGIME_LABELS = frozenset({
    "risk_on_growth", "defensive_rotation", "bear_squeeze",
    "thematic_frenzy", "pre_breakout", "consolidation",
    "distribution", "neutral",
})

TRANSITION_RISKS = frozenset({"high", "medium", "low"})

# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.llm_regime_states (
    regime_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_date          DATE NOT NULL UNIQUE,
    regime_label        TEXT NOT NULL,
    confidence          FLOAT CHECK (confidence BETWEEN 0 AND 1),
    regime_narrative    TEXT,
    factor_weight_adj   JSONB NOT NULL DEFAULT '{{}}',
    regime_duration_est INT,
    transition_risk     TEXT CHECK (transition_risk IN ('high', 'medium', 'low')),
    prior_regime_label  TEXT,
    model_used          TEXT,
    latency_seconds     FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_llm_regime_states_date
    ON {SCHEMA}.llm_regime_states (trade_date DESC);
"""


def ensure_table(engine: Engine) -> None:
    """Create llm_regime_states if it doesn't exist (idempotent)."""
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
    log.debug("[regime_classifier] table ensured")


# ── Data container ────────────────────────────────────────────────────────────

@dataclass
class RegimeState:
    regime_id: str
    trade_date: dt.date
    regime_label: str              # one of REGIME_LABELS
    confidence: float              # 0.0–1.0
    regime_narrative: str          # LLM explanation
    factor_weight_adj: dict[str, float]  # multipliers for heat/trend/persistence/crowding
    regime_duration_est: int       # LLM estimate of how many days in current regime
    transition_risk: str           # "high" | "medium" | "low"
    prior_regime_label: str | None
    model_used: str
    latency_seconds: float

    def adjusted_weights(self, base: dict[str, float] | None = None) -> dict[str, float]:
        """Apply factor_weight_adj to a base weight dict (default equal weights)."""
        if base is None:
            base = {"heat_score": 0.25, "trend_score": 0.25,
                    "persistence_score": 0.25, "crowding_score": 0.25}
        result: dict[str, float] = {}
        total = 0.0
        for k, v in base.items():
            factor_key = k.replace("_score", "")
            adj = self.factor_weight_adj.get(factor_key, 1.0)
            result[k] = v * adj
            total += result[k]
        # Renormalize
        if total > 0:
            result = {k: v / total for k, v in result.items()}
        return result


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_market_state(
    engine: Engine,
    trade_date: dt.date,
    lookback_days: int,
) -> pd.DataFrame:
    """Load market_state_daily for the past `lookback_days` trading dates."""
    try:
        sql = text(f"""
            SELECT trade_date, market_state, amount_ratio_10d,
                   limit_up_count, limit_down_count, blow_up_count,
                   up_count, down_count
            FROM {SCHEMA}.market_state_daily
            WHERE trade_date <= :td
            ORDER BY trade_date DESC
            LIMIT :n
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"td": trade_date, "n": lookback_days}).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=[
            "trade_date", "market_state", "amount_ratio_10d",
            "limit_up_count", "limit_down_count", "blow_up_count",
            "up_count", "down_count",
        ])
        df.sort_values("trade_date", inplace=True)
        return df
    except Exception as exc:  # noqa: BLE001
        log.warning("[regime_classifier] market_state_daily load failed: %s", exc)
        return pd.DataFrame()


def _load_factor_distribution(
    engine: Engine,
    trade_date: dt.date,
    lookback_days: int,
) -> pd.DataFrame:
    """Load per-day aggregate factor stats (mean, std, cross-sector dispersion)."""
    try:
        sql = text(f"""
            SELECT
                trade_date,
                AVG(heat_score)        AS heat_mean,
                STDDEV(heat_score)     AS heat_std,
                AVG(trend_score)       AS trend_mean,
                STDDEV(trend_score)    AS trend_std,
                AVG(persistence_score) AS persist_mean,
                STDDEV(persistence_score) AS persist_std,
                AVG(crowding_score)    AS crowding_mean,
                STDDEV(crowding_score) AS crowding_std,
                COUNT(*)               AS n_sectors
            FROM {SCHEMA}.factor_daily
            WHERE trade_date <= :td
              AND heat_score IS NOT NULL
            GROUP BY trade_date
            ORDER BY trade_date DESC
            LIMIT :n
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"td": trade_date, "n": lookback_days}).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=[
            "trade_date",
            "heat_mean", "heat_std",
            "trend_mean", "trend_std",
            "persist_mean", "persist_std",
            "crowding_mean", "crowding_std",
            "n_sectors",
        ])
        df.sort_values("trade_date", inplace=True)
        for c in df.columns[1:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    except Exception as exc:  # noqa: BLE001
        log.warning("[regime_classifier] factor_daily aggregate load failed: %s", exc)
        return pd.DataFrame()


def _load_sector_state(
    engine: Engine,
    trade_date: dt.date,
    lookback_days: int,
) -> pd.DataFrame:
    """Load recent sector_state_daily (rotation signals)."""
    try:
        sql = text(f"""
            SELECT trade_date, sector_code, sector_source, sector_name,
                   inflow_rank, heat_score, rotation_state
            FROM {SCHEMA}.sector_state_daily
            WHERE trade_date <= :td
            ORDER BY trade_date DESC, inflow_rank ASC
            LIMIT :n
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"td": trade_date, "n": lookback_days * 10}).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=[
            "trade_date", "sector_code", "sector_source", "sector_name",
            "inflow_rank", "heat_score", "rotation_state",
        ])
    except Exception as exc:  # noqa: BLE001
        log.warning("[regime_classifier] sector_state_daily load failed: %s", exc)
        return pd.DataFrame()


def _load_prior_regime(engine: Engine, trade_date: dt.date) -> str | None:
    """Return the regime label for the most recent date before trade_date."""
    try:
        sql = text(f"""
            SELECT regime_label FROM {SCHEMA}.llm_regime_states
            WHERE trade_date < :td
            ORDER BY trade_date DESC LIMIT 1
        """)
        with engine.connect() as conn:
            row = conn.execute(sql, {"td": trade_date}).fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


# ── Signal computation ────────────────────────────────────────────────────────

def _compute_derived_signals(
    market_df: pd.DataFrame,
    factor_df: pd.DataFrame,
    sector_df: pd.DataFrame,
    trade_date: dt.date,
) -> dict[str, Any]:
    """Compute a set of regime-relevant derived signals from raw data."""
    signals: dict[str, Any] = {}

    # ── Market breadth ────────────────────────────────────────────────────────
    if not market_df.empty:
        latest = market_df.iloc[-1]
        signals["market_state_today"] = latest.get("market_state", "未知")
        signals["limit_up_today"] = int(latest.get("limit_up_count", 0) or 0)
        signals["limit_down_today"] = int(latest.get("limit_down_count", 0) or 0)
        signals["blow_up_today"] = int(latest.get("blow_up_count", 0) or 0)
        up = float(latest.get("up_count", 0) or 0)
        down = float(latest.get("down_count", 0) or 0)
        total = up + down
        signals["advance_ratio_today"] = round(up / total, 3) if total > 0 else 0.5

        # Rolling stats
        if len(market_df) >= 5:
            signals["limit_up_5d_avg"] = round(float(market_df["limit_up_count"].tail(5).mean()), 1)
            signals["amount_ratio_10d_latest"] = round(float(market_df["amount_ratio_10d"].iloc[-1] or 1.0), 3)

    # ── Factor distribution signals ───────────────────────────────────────────
    if not factor_df.empty:
        latest = factor_df.iloc[-1]
        signals["heat_mean_today"] = round(float(latest.get("heat_mean", 0) or 0), 3)
        signals["crowding_mean_today"] = round(float(latest.get("crowding_mean", 0) or 0), 3)
        signals["trend_mean_today"] = round(float(latest.get("trend_mean", 0) or 0), 3)
        signals["persist_mean_today"] = round(float(latest.get("persist_mean", 0) or 0), 3)
        signals["n_active_sectors"] = int(latest.get("n_sectors", 0) or 0)

        # Cross-sector dispersion (high = thematic frenzy or polarization)
        if len(factor_df) >= 3:
            heat_std = factor_df["heat_std"].tail(5).mean()
            signals["heat_dispersion_5d"] = round(float(heat_std or 0), 3)

        # Factor momentum (are means rising or falling?)
        if len(factor_df) >= 10:
            heat_5d = float(factor_df["heat_mean"].tail(5).mean() or 0)
            heat_10d = float(factor_df["heat_mean"].tail(10).mean() or 0)
            signals["heat_momentum"] = round(heat_5d - heat_10d, 3)

            trend_5d = float(factor_df["trend_mean"].tail(5).mean() or 0)
            trend_10d = float(factor_df["trend_mean"].tail(10).mean() or 0)
            signals["trend_momentum"] = round(trend_5d - trend_10d, 3)

    # ── Sector rotation ───────────────────────────────────────────────────────
    if not sector_df.empty:
        today_sectors = sector_df[sector_df["trade_date"] == trade_date]
        if not today_sectors.empty and "rotation_state" in today_sectors.columns:
            state_counts = today_sectors["rotation_state"].value_counts().to_dict()
            signals["rotation_state_counts"] = {
                str(k): int(v) for k, v in state_counts.items()
            }
        # Top-3 heat sectors today
        if not today_sectors.empty:
            top3 = today_sectors.nsmallest(3, "inflow_rank")[["sector_name", "heat_score"]].to_dict("records")
            signals["top3_sectors_today"] = [
                {"name": r.get("sector_name", ""), "heat": round(float(r.get("heat_score") or 0), 3)}
                for r in top3
            ]

    return signals


# ── Prompt builder ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是一位资深A股量化策略分析师，专注于识别市场regime（市场环境/状态）。
你的任务是根据给定的市场信号，判断当前市场所处的regime，并给出调整SmartMoney因子权重的建议。

可用的regime类型：
- risk_on_growth: 全面风险偏好，成长风格，资金大面积流入，多个行业同涨
- defensive_rotation: 防御轮动，资金从高估值转向红利/消费，crowding因子反转信号明显
- bear_squeeze: 空头回补主导反弹，成交量偏低，拥挤度高，持续性差
- thematic_frenzy: 题材炒作为主，资金高度集中在少数概念，涨停板集中
- pre_breakout: 蓄势待发，振幅收窄，因子分散度低，方向不明
- consolidation: 震荡整理，因子IC弱，无明显板块领涨
- distribution: 机构出货，拥挤度高但热度下滑，持续性衰减
- neutral: 信号混杂，难以判断

因子调整说明（factor_weight_adj）：
- 每个因子（heat, trend, persistence, crowding）给一个乘数
- 正常权重 = 1.0，增强 > 1.0，降权 < 1.0，逆用 = 负值（crowding通常负相关）
- 最终系统会归一化，所以只需给出相对大小

输出要求：
- 纯 JSON，不含 markdown 代码块
- 格式：
  {
    "regime_label": "<枚举值>",
    "confidence": 0.0-1.0,
    "regime_narrative": "2-4句中文解释",
    "factor_weight_adj": {"heat": 1.2, "trend": 0.8, "persistence": 1.0, "crowding": -0.5},
    "regime_duration_est": <估计已持续天数, 整数>,
    "transition_risk": "high" | "medium" | "low"
  }
"""

_USER_TEMPLATE = """\
分析日期：{trade_date}
过去 {lookback_days} 个交易日的市场信号摘要：

{signals_json}

请根据以上信号判断当前市场regime，并输出 JSON 格式的分析结果。
"""


# ── LLM call ──────────────────────────────────────────────────────────────────

def _call_llm(
    signals: dict[str, Any],
    trade_date: dt.date,
    lookback_days: int,
    *,
    client: LLMClient,
    temperature: float = 0.1,
    max_tokens: int = 1024,
) -> tuple[dict[str, Any], str, float]:
    """Call LLM. Returns (parsed_response_dict, model_used, latency_seconds)."""
    signals_json = json.dumps(signals, ensure_ascii=False, indent=2)
    user_msg = _USER_TEMPLATE.format(
        trade_date=trade_date.isoformat(),
        lookback_days=lookback_days,
        signals_json=signals_json,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    resp = client.chat(messages, temperature=temperature, max_tokens=max_tokens)
    parsed = resp.parse_json()
    return parsed, resp.model, resp.latency_seconds


# ── Assembly ──────────────────────────────────────────────────────────────────

def _assemble_regime(
    parsed: dict[str, Any],
    trade_date: dt.date,
    prior_regime: str | None,
    model_used: str,
    latency_seconds: float,
) -> RegimeState:
    """Validate and assemble a RegimeState from LLM output."""
    label = parsed.get("regime_label", "neutral")
    if label not in REGIME_LABELS:
        log.warning("[regime_classifier] unknown regime_label '%s'; defaulting to neutral", label)
        label = "neutral"

    confidence = float(parsed.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    narrative = parsed.get("regime_narrative", "")

    raw_adj = parsed.get("factor_weight_adj", {})
    valid_factors = {"heat", "trend", "persistence", "crowding"}
    factor_adj: dict[str, float] = {}
    for k in valid_factors:
        v = raw_adj.get(k, 1.0)
        try:
            factor_adj[k] = float(v)
        except (TypeError, ValueError):
            factor_adj[k] = 1.0

    duration_est = int(parsed.get("regime_duration_est", 1))
    duration_est = max(1, duration_est)

    transition_risk = parsed.get("transition_risk", "medium")
    if transition_risk not in TRANSITION_RISKS:
        transition_risk = "medium"

    return RegimeState(
        regime_id=str(uuid.uuid4()),
        trade_date=trade_date,
        regime_label=label,
        confidence=confidence,
        regime_narrative=narrative,
        factor_weight_adj=factor_adj,
        regime_duration_est=duration_est,
        transition_risk=transition_risk,
        prior_regime_label=prior_regime,
        model_used=model_used,
        latency_seconds=latency_seconds,
    )


# ── DB persistence ────────────────────────────────────────────────────────────

def _persist_regime(engine: Engine, state: RegimeState) -> None:
    """Upsert regime state into smartmoney.llm_regime_states."""
    sql = text(f"""
        INSERT INTO {SCHEMA}.llm_regime_states
            (regime_id, trade_date, regime_label, confidence,
             regime_narrative, factor_weight_adj,
             regime_duration_est, transition_risk, prior_regime_label,
             model_used, latency_seconds)
        VALUES
            (:rid, :td, :label, :conf,
             :narr, cast(:wadj AS jsonb),
             :dur, :risk, :prior,
             :model, :latency)
        ON CONFLICT (trade_date) DO UPDATE SET
            regime_label        = EXCLUDED.regime_label,
            confidence          = EXCLUDED.confidence,
            regime_narrative    = EXCLUDED.regime_narrative,
            factor_weight_adj   = EXCLUDED.factor_weight_adj,
            regime_duration_est = EXCLUDED.regime_duration_est,
            transition_risk     = EXCLUDED.transition_risk,
            prior_regime_label  = EXCLUDED.prior_regime_label,
            model_used          = EXCLUDED.model_used,
            latency_seconds     = EXCLUDED.latency_seconds,
            created_at          = now()
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "rid": state.regime_id,
            "td": state.trade_date,
            "label": state.regime_label,
            "conf": state.confidence,
            "narr": state.regime_narrative,
            "wadj": json.dumps(state.factor_weight_adj, ensure_ascii=False),
            "dur": state.regime_duration_est,
            "risk": state.transition_risk,
            "prior": state.prior_regime_label,
            "model": state.model_used,
            "latency": state.latency_seconds,
        })
    log.info("[regime_classifier] persisted regime '%s' (conf=%.2f) for %s",
             state.regime_label, state.confidence, state.trade_date)


# ── Public entry point ────────────────────────────────────────────────────────

def run_regime_classifier(
    engine: Engine,
    *,
    trade_date: dt.date,
    lookback_days: int = 20,
    temperature: float = 0.1,
    persist: bool = True,
    llm_client: LLMClient | None = None,
    on_log: Any = None,
) -> RegimeState:
    """Classify the current market regime for `trade_date`.

    Args:
        engine:        SQLAlchemy engine.
        trade_date:    The market date to classify.
        lookback_days: How many trading days of history to include in signals (default 20).
        temperature:   LLM temperature (default 0.1 — regime classification is factual).
        persist:       Whether to write result to DB (default True).
        llm_client:    LLMClient instance; creates one from settings if None.
        on_log:        Optional callable(str) for progress logging.

    Returns:
        RegimeState dataclass.
    """
    def _emit(msg: str) -> None:
        if on_log:
            on_log(msg)
        log.info(msg)

    _emit(f"[regime_classifier] {trade_date}: loading signals (lookback={lookback_days}d) …")

    market_df = _load_market_state(engine, trade_date, lookback_days)
    factor_df = _load_factor_distribution(engine, trade_date, lookback_days)
    sector_df = _load_sector_state(engine, trade_date, lookback_days)
    prior_regime = _load_prior_regime(engine, trade_date)

    _emit(f"[regime_classifier] market={len(market_df)}rows factor={len(factor_df)}rows "
          f"sector={len(sector_df)}rows prior={prior_regime or 'none'}")

    signals = _compute_derived_signals(market_df, factor_df, sector_df, trade_date)
    if prior_regime:
        signals["prior_regime"] = prior_regime
    signals["lookback_days"] = lookback_days

    _emit(f"[regime_classifier] derived {len(signals)} signals; calling LLM …")

    if llm_client is None:
        llm_client = LLMClient()

    try:
        parsed, model_used, latency = _call_llm(
            signals, trade_date, lookback_days,
            client=llm_client, temperature=temperature,
        )
    except Exception as exc:
        _emit(f"[regime_classifier] LLM call failed: {exc}")
        raise

    _emit(f"[regime_classifier] LLM done in {latency:.1f}s (model={model_used})")

    state = _assemble_regime(parsed, trade_date, prior_regime, model_used, latency)

    _emit(
        f"[regime_classifier] regime={state.regime_label} conf={state.confidence:.2f} "
        f"transition_risk={state.transition_risk} duration_est={state.regime_duration_est}d"
    )
    _emit(f"[regime_classifier] factor weights: {state.factor_weight_adj}")
    _emit(f"[regime_classifier] narrative: {state.regime_narrative[:100]}…" if len(state.regime_narrative) > 100 else f"[regime_classifier] narrative: {state.regime_narrative}")

    if persist:
        ensure_table(engine)
        _persist_regime(engine, state)

    return state


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_regime_history(
    engine: Engine,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Return recent regime states as list of dicts."""
    conditions = []
    params: dict[str, Any] = {"limit": limit}
    if start:
        conditions.append("trade_date >= :start")
        params["start"] = start
    if end:
        conditions.append("trade_date <= :end")
        params["end"] = end
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = text(f"""
        SELECT trade_date, regime_label, confidence, transition_risk,
               regime_duration_est, prior_regime_label,
               factor_weight_adj, regime_narrative, model_used
        FROM {SCHEMA}.llm_regime_states
        {where}
        ORDER BY trade_date DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "trade_date": r[0], "regime_label": r[1], "confidence": r[2],
            "transition_risk": r[3], "regime_duration_est": r[4],
            "prior_regime_label": r[5], "factor_weight_adj": r[6],
            "regime_narrative": r[7], "model_used": r[8],
        }
        for r in rows
    ]


def get_latest_regime(engine: Engine) -> dict[str, Any] | None:
    """Return the most recent regime state, or None if table is empty."""
    sql = text(f"""
        SELECT trade_date, regime_label, confidence, transition_risk,
               regime_duration_est, factor_weight_adj, regime_narrative
        FROM {SCHEMA}.llm_regime_states
        ORDER BY trade_date DESC LIMIT 1
    """)
    with engine.connect() as conn:
        row = conn.execute(sql).fetchone()
    if not row:
        return None
    return {
        "trade_date": row[0], "regime_label": row[1], "confidence": row[2],
        "transition_risk": row[3], "regime_duration_est": row[4],
        "factor_weight_adj": row[5], "regime_narrative": row[6],
    }
