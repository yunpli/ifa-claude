"""SmartMoney LLM Augmentation — C2: Policy Polarity Classifier.

Infers China monetary/fiscal policy stance from market-observable signals —
without requiring a news feed. Uses sector rotation patterns and factor
behavior as policy proxies.

Rationale
---------
Policy stance leaves measurable fingerprints in A-share sector flows:
  • Easing:    banks, real estate, utilities flow in; rate-sensitive sectors
               outperform; small-cap momentum accelerates
  • Tightening: value/cyclicals rotate to defensive; large-cap SOE outperform;
               liquidity premium shrinks; crowding in safety sectors rises
  • Neutral:   mixed signals; sector dispersion low; factor IC stable
  • Stimulus:  infrastructure, policy-driven sectors top list; top-list
               concentration in specific themes; heat_score spike

Market proxies used:
  - Factor heat/flow for rate-sensitive sectors (banking, real estate, utilities)
  - Factor heat for SOE-heavy sectors vs private-sector growth
  - Top-list concentration (kpl_list / top_list)
  - Market state trend over 10-20 days
  - Crowding factor direction in defensives vs cyclicals

Workflow
--------
1. Identify proxy sector groups in factor_daily by sector name keywords.
2. Compute flow differentials across the proxy groups.
3. Call LLM to synthesize policy polarity and sector implications.
4. Persist to smartmoney.llm_policy_polarity.
5. Return PolicyPolarity dataclass.

DB Table
--------
    smartmoney.llm_policy_polarity
    ───────────────────────────────
    polarity_id         UUID PK
    trade_date          DATE UNIQUE
    policy_stance       TEXT    'easing' | 'neutral' | 'tightening' | 'stimulus' | 'uncertain'
    confidence          FLOAT   0.0-1.0
    polarity_narrative  TEXT    2-4 sentences (CN) explaining the evidence
    proxy_signals       JSONB   {group → {heat_mean, trend_mean, flow_ratio}}
    sector_implications JSONB   [{sector_theme, expected_impact, timeframe}]
    recommended_tilt    JSONB   {"factor_tilt": "...", "sector_tilt": "..."}
    model_used          TEXT
    latency_seconds     FLOAT
    created_at          TIMESTAMPTZ

Usage
-----
    from ifa.families.smartmoney.llm_aug.policy_polarity import run_policy_polarity

    polarity = run_policy_polarity(engine, trade_date=dt.date(2026, 4, 30), lookback_days=15)
    print(polarity.policy_stance, polarity.confidence)
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

POLICY_STANCES = frozenset({"easing", "neutral", "tightening", "stimulus", "uncertain"})

# ── Sector proxy groups ───────────────────────────────────────────────────────
# Keywords used to classify sectors into policy-sensitive proxy groups.
# Each group represents a different policy signal.

PROXY_GROUPS: dict[str, list[str]] = {
    "rate_sensitive": [          # easing → flows in
        "银行", "地产", "房地产", "公用事业", "水电", "高速公路", "燃气",
    ],
    "soe_cyclical": [            # policy-support → outperform
        "央企", "国企", "建筑", "铁路", "钢铁", "煤炭", "电力",
    ],
    "growth_private": [          # tightening → outperform vs SOE
        "互联网", "软件", "游戏", "医药", "创新药", "半导体", "芯片",
        "人工智能", "AI", "新能源", "锂电", "光伏",
    ],
    "policy_theme": [            # stimulus → top list concentration
        "基建", "特高压", "军工", "卫星", "数据要素", "数字经济",
    ],
    "defensive": [               # risk-off → outperform
        "消费", "食品饮料", "白酒", "医疗", "保险", "黄金",
    ],
}


# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.llm_policy_polarity (
    polarity_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_date          DATE NOT NULL UNIQUE,
    policy_stance       TEXT NOT NULL CHECK (policy_stance IN (
                            'easing', 'neutral', 'tightening', 'stimulus', 'uncertain')),
    confidence          FLOAT CHECK (confidence BETWEEN 0 AND 1),
    polarity_narrative  TEXT,
    proxy_signals       JSONB NOT NULL DEFAULT '{{}}',
    sector_implications JSONB NOT NULL DEFAULT '[]',
    recommended_tilt    JSONB NOT NULL DEFAULT '{{}}',
    model_used          TEXT,
    latency_seconds     FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_llm_policy_polarity_date
    ON {SCHEMA}.llm_policy_polarity (trade_date DESC);
"""


def ensure_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
    log.debug("[policy_polarity] table ensured")


# ── Data container ────────────────────────────────────────────────────────────

@dataclass
class SectorImplication:
    sector_theme: str
    expected_impact: str    # "positive" | "negative" | "neutral"
    timeframe: str          # e.g. "1-2 weeks", "near-term"
    reasoning: str


@dataclass
class PolicyPolarity:
    polarity_id: str
    trade_date: dt.date
    policy_stance: str            # easing | neutral | tightening | stimulus | uncertain
    confidence: float
    polarity_narrative: str
    proxy_signals: dict[str, Any]  # group → computed stats
    sector_implications: list[SectorImplication]
    recommended_tilt: dict[str, str]   # factor_tilt, sector_tilt
    model_used: str
    latency_seconds: float


# ── Signal computation ────────────────────────────────────────────────────────

def _classify_sector_to_group(sector_name: str) -> str | None:
    """Return the proxy group name for a sector, or None if not matched."""
    for group, keywords in PROXY_GROUPS.items():
        for kw in keywords:
            if kw in sector_name:
                return group
    return None


def _load_proxy_signals(
    engine: Engine,
    trade_date: dt.date,
    lookback_days: int,
) -> dict[str, Any]:
    """Load factor scores for proxy sector groups over the lookback window."""
    sql = text(f"""
        SELECT fd.trade_date, fd.sector_name,
               fd.heat_score, fd.trend_score,
               fd.persistence_score, fd.crowding_score
        FROM {SCHEMA}.factor_daily fd
        WHERE fd.trade_date > :start AND fd.trade_date <= :end
          AND fd.heat_score IS NOT NULL
        ORDER BY fd.trade_date, fd.sector_name
    """)
    start = trade_date - dt.timedelta(days=int(lookback_days * 1.8))
    with engine.connect() as conn:
        rows = conn.execute(sql, {"start": start, "end": trade_date}).fetchall()

    if not rows:
        return {}

    df = pd.DataFrame(rows, columns=[
        "trade_date", "sector_name",
        "heat_score", "trend_score", "persistence_score", "crowding_score",
    ])
    for c in ["heat_score", "trend_score", "persistence_score", "crowding_score"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["proxy_group"] = df["sector_name"].map(_classify_sector_to_group)
    df = df.dropna(subset=["proxy_group"])

    proxy_signals: dict[str, Any] = {}
    for group in PROXY_GROUPS:
        grp_df = df[df["proxy_group"] == group]
        if grp_df.empty:
            proxy_signals[group] = {"n_sectors": 0, "no_data": True}
            continue

        # Full-window averages
        proxy_signals[group] = {
            "n_sectors": int(grp_df["sector_name"].nunique()),
            "heat_mean": round(float(grp_df["heat_score"].mean()), 3),
            "trend_mean": round(float(grp_df["trend_score"].mean()), 3),
            "persist_mean": round(float(grp_df["persistence_score"].mean()), 3),
            "crowding_mean": round(float(grp_df["crowding_score"].mean()), 3),
        }

        # Recent vs prior momentum: last 5 days vs rest
        recent = grp_df[grp_df["trade_date"] >= (trade_date - dt.timedelta(days=7))]
        prior = grp_df[grp_df["trade_date"] < (trade_date - dt.timedelta(days=7))]
        if not recent.empty and not prior.empty:
            proxy_signals[group]["heat_momentum_5d"] = round(
                float(recent["heat_score"].mean()) - float(prior["heat_score"].mean()), 3
            )
            proxy_signals[group]["trend_momentum_5d"] = round(
                float(recent["trend_score"].mean()) - float(prior["trend_score"].mean()), 3
            )

    # Cross-group differentials (key policy signals)
    try:
        rate_heat = proxy_signals.get("rate_sensitive", {}).get("heat_mean", 0)
        growth_heat = proxy_signals.get("growth_private", {}).get("heat_mean", 0)
        soe_heat = proxy_signals.get("soe_cyclical", {}).get("heat_mean", 0)
        theme_heat = proxy_signals.get("policy_theme", {}).get("heat_mean", 0)
        proxy_signals["_differentials"] = {
            "rate_vs_growth_heat": round(float(rate_heat) - float(growth_heat), 3),
            "soe_vs_growth_heat": round(float(soe_heat) - float(growth_heat), 3),
            "theme_vs_defensive_heat": round(
                float(theme_heat)
                - float(proxy_signals.get("defensive", {}).get("heat_mean", 0)),
                3,
            ),
        }
    except Exception:  # noqa: BLE001
        pass

    return proxy_signals


def _load_market_trend(
    engine: Engine,
    trade_date: dt.date,
    lookback_days: int,
) -> dict[str, Any]:
    """Load recent market state trend and limit-up dynamics."""
    try:
        sql = text(f"""
            SELECT trade_date, market_state, amount_ratio_10d,
                   limit_up_count, limit_down_count
            FROM {SCHEMA}.market_state_daily
            WHERE trade_date <= :td
            ORDER BY trade_date DESC
            LIMIT :n
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"td": trade_date, "n": lookback_days}).fetchall()
        if not rows:
            return {}
        df = pd.DataFrame(rows, columns=[
            "trade_date", "market_state", "amount_ratio_10d",
            "limit_up_count", "limit_down_count",
        ])
        df.sort_values("trade_date", inplace=True)
        state_counts = df["market_state"].value_counts().to_dict()
        return {
            "dominant_state": df["market_state"].iloc[-1],
            "state_distribution": {str(k): int(v) for k, v in state_counts.items()},
            "limit_up_avg": round(float(df["limit_up_count"].mean()), 1),
            "limit_down_avg": round(float(df["limit_down_count"].mean()), 1),
            "amount_ratio_latest": round(float(df["amount_ratio_10d"].iloc[-1] or 1.0), 3),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("[policy_polarity] market trend load failed: %s", exc)
        return {}


# ── Prompt + LLM ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是一位专注于中国货币/财政政策研判的A股策略分析师。

你的任务是根据市场可观测信号（无直接政策新闻输入），推断当前的政策环境极性（policy polarity）。

政策极性定义：
- easing: 宽松，降息降准信号，资金面宽裕，利率敏感板块受益
- neutral: 中性，政策观望，市场自我运行
- tightening: 收紧，流动性收缩，防风险优先，价值防御占优
- stimulus: 刺激，定向财政发力，基建/主题政策驱动明显
- uncertain: 信号混杂，难以判断

输出纯 JSON：
{
  "policy_stance": "<枚举值>",
  "confidence": 0.0-1.0,
  "polarity_narrative": "2-4句中文：关键证据+判断+注意事项",
  "sector_implications": [
    {
      "sector_theme": "主题名（CN）",
      "expected_impact": "positive" | "negative" | "neutral",
      "timeframe": "时间窗口（如'1-2周'）",
      "reasoning": "一句话理由（CN）"
    }
  ],
  "recommended_tilt": {
    "factor_tilt": "建议强化哪个因子（如'加强persistence_score权重'）",
    "sector_tilt": "建议板块倾向（如'超配利率敏感板块，低配高拥挤成长'）"
  }
}
"""

_USER_TEMPLATE = """\
分析日期：{trade_date}
过去 {lookback_days} 个交易日的代理信号：

代理板块组因子均值：
{proxy_block}

市场整体趋势：
{market_block}

请根据以上市场可观测代理信号，推断当前政策极性，输出 JSON。
"""


def _build_proxy_block(proxy_signals: dict[str, Any]) -> str:
    lines = []
    for group, signals in proxy_signals.items():
        if group.startswith("_"):
            continue
        if signals.get("no_data"):
            lines.append(f"{group}: 无数据")
            continue
        heat = signals.get("heat_mean", 0)
        trend = signals.get("trend_mean", 0)
        persist = signals.get("persist_mean", 0)
        crowd = signals.get("crowding_mean", 0)
        momentum = signals.get("heat_momentum_5d", None)
        mom_str = f" (5d动量: {momentum:+.3f})" if momentum is not None else ""
        lines.append(
            f"{group} ({signals.get('n_sectors', 0)}个板块): "
            f"heat={heat:+.3f} trend={trend:+.3f} persist={persist:+.3f} "
            f"crowd={crowd:+.3f}{mom_str}"
        )

    diffs = proxy_signals.get("_differentials", {})
    if diffs:
        lines.append("")
        lines.append("跨组差异（正=前者更热）：")
        for k, v in diffs.items():
            lines.append(f"  {k}: {v:+.3f}")

    return "\n".join(lines)


def _build_market_block(market_trend: dict[str, Any]) -> str:
    if not market_trend:
        return "市场趋势数据不可用"
    lines = [
        f"当前主要市场状态: {market_trend.get('dominant_state', '未知')}",
        f"涨停/跌停均值: {market_trend.get('limit_up_avg', 0):.0f} / {market_trend.get('limit_down_avg', 0):.0f}",
        f"成交额比率(vs 10日均): {market_trend.get('amount_ratio_latest', 1.0):.2f}",
    ]
    state_dist = market_trend.get("state_distribution", {})
    if state_dist:
        dist_str = "  ".join(f"{k}:{v}天" for k, v in sorted(state_dist.items(), key=lambda x: -x[1])[:4])
        lines.append(f"状态分布: {dist_str}")
    return "\n".join(lines)


def _call_llm(
    proxy_signals: dict[str, Any],
    market_trend: dict[str, Any],
    trade_date: dt.date,
    lookback_days: int,
    *,
    client: LLMClient,
    temperature: float = 0.1,
    max_tokens: int = 1536,
) -> tuple[dict[str, Any], str, float]:
    proxy_block = _build_proxy_block(proxy_signals)
    market_block = _build_market_block(market_trend)
    user_msg = _USER_TEMPLATE.format(
        trade_date=trade_date.isoformat(),
        lookback_days=lookback_days,
        proxy_block=proxy_block,
        market_block=market_block,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    resp = client.chat(messages, temperature=temperature, max_tokens=max_tokens)
    parsed = resp.parse_json()
    return parsed, resp.model, resp.latency_seconds


# ── Assembly ──────────────────────────────────────────────────────────────────

def _assemble_polarity(
    parsed: dict[str, Any],
    trade_date: dt.date,
    proxy_signals: dict[str, Any],
    model_used: str,
    latency_seconds: float,
) -> PolicyPolarity:
    stance = parsed.get("policy_stance", "uncertain")
    if stance not in POLICY_STANCES:
        stance = "uncertain"

    confidence = float(parsed.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    raw_implications = parsed.get("sector_implications", [])
    implications: list[SectorImplication] = []
    for item in raw_implications:
        implications.append(SectorImplication(
            sector_theme=item.get("sector_theme", ""),
            expected_impact=item.get("expected_impact", "neutral"),
            timeframe=item.get("timeframe", "near-term"),
            reasoning=item.get("reasoning", ""),
        ))

    return PolicyPolarity(
        polarity_id=str(uuid.uuid4()),
        trade_date=trade_date,
        policy_stance=stance,
        confidence=confidence,
        polarity_narrative=parsed.get("polarity_narrative", ""),
        proxy_signals=proxy_signals,
        sector_implications=implications,
        recommended_tilt=parsed.get("recommended_tilt", {}),
        model_used=model_used,
        latency_seconds=latency_seconds,
    )


# ── DB persistence ────────────────────────────────────────────────────────────

def _persist_polarity(engine: Engine, polarity: PolicyPolarity) -> None:
    sql = text(f"""
        INSERT INTO {SCHEMA}.llm_policy_polarity
            (polarity_id, trade_date, policy_stance, confidence,
             polarity_narrative, proxy_signals, sector_implications,
             recommended_tilt, model_used, latency_seconds)
        VALUES
            (:pid, :td, :stance, :conf,
             :narr, cast(:proxies AS jsonb), cast(:impls AS jsonb),
             cast(:tilt AS jsonb), :model, :latency)
        ON CONFLICT (trade_date) DO UPDATE SET
            policy_stance       = EXCLUDED.policy_stance,
            confidence          = EXCLUDED.confidence,
            polarity_narrative  = EXCLUDED.polarity_narrative,
            proxy_signals       = EXCLUDED.proxy_signals,
            sector_implications = EXCLUDED.sector_implications,
            recommended_tilt    = EXCLUDED.recommended_tilt,
            model_used          = EXCLUDED.model_used,
            latency_seconds     = EXCLUDED.latency_seconds,
            created_at          = now()
    """)
    impl_json = json.dumps(
        [
            {
                "sector_theme": imp.sector_theme,
                "expected_impact": imp.expected_impact,
                "timeframe": imp.timeframe,
                "reasoning": imp.reasoning,
            }
            for imp in polarity.sector_implications
        ],
        ensure_ascii=False,
    )
    with engine.begin() as conn:
        conn.execute(sql, {
            "pid": polarity.polarity_id,
            "td": polarity.trade_date,
            "stance": polarity.policy_stance,
            "conf": polarity.confidence,
            "narr": polarity.polarity_narrative,
            "proxies": json.dumps(
                {k: v for k, v in polarity.proxy_signals.items() if not k.startswith("_")},
                ensure_ascii=False,
            ),
            "impls": impl_json,
            "tilt": json.dumps(polarity.recommended_tilt, ensure_ascii=False),
            "model": polarity.model_used,
            "latency": polarity.latency_seconds,
        })
    log.info("[policy_polarity] persisted stance='%s' (conf=%.2f) for %s",
             polarity.policy_stance, polarity.confidence, polarity.trade_date)


# ── Public entry point ────────────────────────────────────────────────────────

def run_policy_polarity(
    engine: Engine,
    *,
    trade_date: dt.date,
    lookback_days: int = 15,
    temperature: float = 0.1,
    persist: bool = True,
    llm_client: LLMClient | None = None,
    on_log: Any = None,
) -> PolicyPolarity:
    """Infer policy polarity from market-observable proxy signals.

    Args:
        engine:        SQLAlchemy engine.
        trade_date:    The date to classify policy polarity for.
        lookback_days: Trading days of history to use (default 15 ≈ 3 weeks).
        temperature:   LLM temperature (default 0.1).
        persist:       Write to DB.
        llm_client:    LLMClient; creates from settings if None.
        on_log:        Optional callable(str) for progress logging.

    Returns:
        PolicyPolarity dataclass.
    """
    def _emit(msg: str) -> None:
        if on_log:
            on_log(msg)
        log.info(msg)

    _emit(f"[policy_polarity] {trade_date}: loading proxy signals (lookback={lookback_days}d) …")

    proxy_signals = _load_proxy_signals(engine, trade_date, lookback_days)
    market_trend = _load_market_trend(engine, trade_date, lookback_days)

    covered_groups = sum(1 for v in proxy_signals.values()
                         if isinstance(v, dict) and not v.get("no_data"))
    _emit(f"[policy_polarity] {covered_groups}/{len(PROXY_GROUPS)} proxy groups covered")

    _emit("[policy_polarity] calling LLM …")
    if llm_client is None:
        llm_client = LLMClient()

    try:
        parsed, model_used, latency = _call_llm(
            proxy_signals, market_trend, trade_date, lookback_days,
            client=llm_client, temperature=temperature,
        )
    except Exception as exc:
        _emit(f"[policy_polarity] LLM call failed: {exc}")
        raise

    _emit(f"[policy_polarity] LLM done in {latency:.1f}s (model={model_used})")

    polarity = _assemble_polarity(parsed, trade_date, proxy_signals, model_used, latency)

    _emit(
        f"[policy_polarity] stance={polarity.policy_stance} "
        f"conf={polarity.confidence:.2f}"
    )
    _emit(f"[policy_polarity] narrative: {polarity.polarity_narrative[:100]}…"
          if len(polarity.polarity_narrative) > 100 else
          f"[policy_polarity] narrative: {polarity.polarity_narrative}")

    if persist:
        ensure_table(engine)
        _persist_polarity(engine, polarity)

    return polarity


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_polarity_history(
    engine: Engine,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Return recent policy polarity readings as list of dicts."""
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
        SELECT trade_date, policy_stance, confidence,
               polarity_narrative, recommended_tilt, model_used
        FROM {SCHEMA}.llm_policy_polarity
        {where}
        ORDER BY trade_date DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "trade_date": r[0], "policy_stance": r[1], "confidence": r[2],
            "polarity_narrative": r[3], "recommended_tilt": r[4], "model_used": r[5],
        }
        for r in rows
    ]
