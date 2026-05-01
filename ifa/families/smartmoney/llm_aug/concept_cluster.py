"""SmartMoney LLM Augmentation — B1: Concept Cluster.

Clusters sectors into thematic investment narratives using LLM reasoning.

Workflow
--------
1. Load factor_daily for the target date → sector × (heat, trend, persistence, crowding)
2. Compute a per-sector composite score (equal-weight of the 4 factors).
3. Send the sector list + scores to the LLM and ask it to group them into
   coherent thematic investment clusters (e.g. "AI算力硬件", "新能源车产业链").
4. LLM returns structured JSON: cluster_name, member_sectors, momentum_signal,
   narrative (one paragraph per cluster).
5. Persist results to smartmoney.llm_concept_clusters.
6. Return a list of ConceptCluster dataclasses for downstream use (e.g. evening report).

DB Table
--------
    smartmoney.llm_concept_clusters
    ────────────────────────────────
    cluster_id          UUID PK
    trade_date          DATE
    cluster_name        TEXT          LLM-assigned human-readable theme name (CN preferred)
    cluster_label       TEXT          machine-friendly slug, e.g. "ai_hardware"
    member_codes        JSONB         [{sector_code, sector_source, sector_name, composite_score}]
    n_members           INT
    narrative           TEXT          LLM paragraph: what this cluster is doing and why
    momentum_signal     TEXT          "accelerating" | "peaking" | "cooling" | "dormant"
    composite_score_avg FLOAT         mean composite score of member sectors
    model_used          TEXT          LLM model id
    latency_seconds     FLOAT
    created_at          TIMESTAMPTZ

Usage
-----
    from ifa.families.smartmoney.llm_aug.concept_cluster import run_concept_cluster

    clusters = run_concept_cluster(engine, trade_date=dt.date(2026, 4, 30))
    for c in clusters:
        print(c.cluster_name, c.momentum_signal, len(c.members), 'sectors')
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

# ── DDL (run once via migration or call ensure_table()) ──────────────────────

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.llm_concept_clusters (
    cluster_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_date          DATE NOT NULL,
    cluster_name        TEXT NOT NULL,
    cluster_label       TEXT NOT NULL,
    member_codes        JSONB NOT NULL DEFAULT '[]',
    n_members           INT NOT NULL DEFAULT 0,
    narrative           TEXT,
    momentum_signal     TEXT CHECK (momentum_signal IN (
                            'accelerating', 'peaking', 'cooling', 'dormant')),
    composite_score_avg FLOAT,
    model_used          TEXT,
    latency_seconds     FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_llm_concept_clusters_date_label
    ON {SCHEMA}.llm_concept_clusters (trade_date, cluster_label);
"""


def ensure_table(engine: Engine) -> None:
    """Create llm_concept_clusters if it doesn't exist (idempotent)."""
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
    log.debug("[concept_cluster] table ensured")


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class ClusterMember:
    sector_code: str
    sector_source: str
    sector_name: str
    composite_score: float
    heat_score: float
    trend_score: float
    persistence_score: float
    crowding_score: float


@dataclass
class ConceptCluster:
    cluster_id: str
    trade_date: dt.date
    cluster_name: str              # e.g. "AI算力硬件"
    cluster_label: str             # e.g. "ai_hardware"
    members: list[ClusterMember]
    narrative: str
    momentum_signal: str           # "accelerating" | "peaking" | "cooling" | "dormant"
    composite_score_avg: float
    model_used: str
    latency_seconds: float


# ── Data loader ───────────────────────────────────────────────────────────────

def _load_sector_scores(
    engine: Engine,
    trade_date: dt.date,
    *,
    top_n: int = 60,
) -> pd.DataFrame:
    """Load factor scores for the given date.

    Returns a DataFrame with one row per sector, sorted descending by composite_score.
    Limits to the top `top_n` sectors by composite score to keep prompt manageable.
    """
    sql = text(f"""
        SELECT
            sector_code, sector_source, sector_name,
            heat_score, trend_score, persistence_score, crowding_score
        FROM {SCHEMA}.factor_daily
        WHERE trade_date = :td
          AND heat_score IS NOT NULL
        ORDER BY (
            COALESCE(heat_score, 0)
            + COALESCE(trend_score, 0)
            + COALESCE(persistence_score, 0)
            + COALESCE(crowding_score, 0)
        ) DESC
        LIMIT :top_n
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"td": trade_date, "top_n": top_n}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "sector_code", "sector_source", "sector_name",
        "heat_score", "trend_score", "persistence_score", "crowding_score",
    ])
    for c in ["heat_score", "trend_score", "persistence_score", "crowding_score"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["composite_score"] = (
        df["heat_score"] + df["trend_score"]
        + df["persistence_score"] + df["crowding_score"]
    ) / 4.0
    return df


# ── Prompt builder ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是一位量化基金经理，专注A股行业轮动与题材炒作研究。
你的任务是把一批行业/概念板块按照当前市场主题，归类为若干个"投资概念簇"。
每个概念簇代表一个当前市场上活跃的投资叙事（如AI算力、新能源车、红利防御等）。

输出要求：
- 输出纯 JSON，不要有任何 markdown 代码块
- 顶层为 {"clusters": [...]}
- 每个 cluster 包含：
  - cluster_name: 中文主题名（4-8字），如 "AI算力硬件"
  - cluster_label: 英文slug（小写下划线），如 "ai_hardware"
  - member_codes: 属于这个簇的 sector_code 列表（原样复制，不要修改）
  - momentum_signal: 其中一个枚举值 "accelerating" | "peaking" | "cooling" | "dormant"
  - narrative: 2-4句中文描述，解释这个簇当前的资金动向、驱动因素和风险
- 每个板块只能属于一个簇
- 可以有 "其他" 兜底簇（label="other"）接收不好分类的板块
- 簇数量建议 4-10 个
- 不要输出任何解释文字，只输出 JSON
"""

_USER_TEMPLATE = """\
今日日期：{trade_date}
行业/概念板块评分（已按综合得分降序排列，共 {n} 个板块）：

{sector_table}

因子说明：
- heat_score: 资金热度（买方主动性、超大单流入比）
- trend_score: 趋势强度（近期价格动量、突破信号）
- persistence_score: 持续性（连续多日资金持续流入）
- crowding_score: 拥挤度（高拥挤度=机构持仓集中，反向信号）
- composite: 四因子等权均值

请根据以上数据，将这些板块分组为投资概念簇，并输出 JSON。
"""


def _build_sector_table(df: pd.DataFrame) -> str:
    """Format sector scores as a compact text table for the prompt."""
    lines = ["sector_code | sector_source | sector_name | composite | heat | trend | persist | crowd"]
    lines.append("-" * 90)
    for _, row in df.iterrows():
        lines.append(
            f"{row['sector_code']:<14} | {row['sector_source']:<4} | "
            f"{row['sector_name']:<16} | {row['composite_score']:+.3f} | "
            f"{row['heat_score']:+.3f} | {row['trend_score']:+.3f} | "
            f"{row['persistence_score']:+.3f} | {row['crowding_score']:+.3f}"
        )
    return "\n".join(lines)


# ── LLM call ──────────────────────────────────────────────────────────────────

def _call_llm(
    df: pd.DataFrame,
    trade_date: dt.date,
    *,
    client: LLMClient,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> tuple[list[dict[str, Any]], str, float]:
    """Call LLM and return (parsed_clusters_list, model_used, latency_seconds)."""
    sector_table = _build_sector_table(df)
    user_msg = _USER_TEMPLATE.format(
        trade_date=trade_date.isoformat(),
        n=len(df),
        sector_table=sector_table,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    resp = client.chat(messages, temperature=temperature, max_tokens=max_tokens)
    parsed = resp.parse_json()
    clusters_raw = parsed.get("clusters", [])
    return clusters_raw, resp.model, resp.latency_seconds


# ── Assembly ──────────────────────────────────────────────────────────────────

def _assemble_clusters(
    clusters_raw: list[dict[str, Any]],
    df: pd.DataFrame,
    trade_date: dt.date,
    model_used: str,
    latency_seconds: float,
) -> list[ConceptCluster]:
    """Join LLM cluster output back to the sector DataFrame."""
    # Build a lookup: sector_code → row
    code_to_row: dict[str, dict] = {
        row["sector_code"]: row for row in df.to_dict("records")
    }

    result: list[ConceptCluster] = []
    for c in clusters_raw:
        name = c.get("cluster_name", "Unknown")
        label = c.get("cluster_label", "unknown")
        member_codes: list[str] = c.get("member_codes", [])
        momentum = c.get("momentum_signal", "dormant")
        narrative = c.get("narrative", "")

        members: list[ClusterMember] = []
        for code in member_codes:
            row = code_to_row.get(code)
            if row is None:
                log.warning("[concept_cluster] LLM returned unknown sector_code '%s'", code)
                continue
            members.append(ClusterMember(
                sector_code=row["sector_code"],
                sector_source=row["sector_source"],
                sector_name=row["sector_name"],
                composite_score=float(row["composite_score"]),
                heat_score=float(row["heat_score"]),
                trend_score=float(row["trend_score"]),
                persistence_score=float(row["persistence_score"]),
                crowding_score=float(row["crowding_score"]),
            ))

        if not members:
            log.warning("[concept_cluster] cluster '%s' has no valid members; skipping", name)
            continue

        avg_composite = float(np.mean([m.composite_score for m in members]))

        result.append(ConceptCluster(
            cluster_id=str(uuid.uuid4()),
            trade_date=trade_date,
            cluster_name=name,
            cluster_label=label,
            members=members,
            narrative=narrative,
            momentum_signal=momentum if momentum in ("accelerating", "peaking", "cooling", "dormant") else "dormant",
            composite_score_avg=avg_composite,
            model_used=model_used,
            latency_seconds=latency_seconds,
        ))

    return result


# ── DB persistence ────────────────────────────────────────────────────────────

def _persist_clusters(engine: Engine, clusters: list[ConceptCluster]) -> None:
    """Upsert clusters into smartmoney.llm_concept_clusters."""
    if not clusters:
        return
    sql = text(f"""
        INSERT INTO {SCHEMA}.llm_concept_clusters
            (cluster_id, trade_date, cluster_name, cluster_label,
             member_codes, n_members, narrative, momentum_signal,
             composite_score_avg, model_used, latency_seconds)
        VALUES
            (:cid, :td, :name, :label,
             cast(:members AS jsonb), :n, :narr, :mom,
             :score_avg, :model, :latency)
        ON CONFLICT (trade_date, cluster_label) DO UPDATE SET
            cluster_name        = EXCLUDED.cluster_name,
            member_codes        = EXCLUDED.member_codes,
            n_members           = EXCLUDED.n_members,
            narrative           = EXCLUDED.narrative,
            momentum_signal     = EXCLUDED.momentum_signal,
            composite_score_avg = EXCLUDED.composite_score_avg,
            model_used          = EXCLUDED.model_used,
            latency_seconds     = EXCLUDED.latency_seconds,
            created_at          = now()
    """)
    rows = [
        {
            "cid": c.cluster_id,
            "td": c.trade_date,
            "name": c.cluster_name,
            "label": c.cluster_label,
            "members": json.dumps(
                [
                    {
                        "sector_code": m.sector_code,
                        "sector_source": m.sector_source,
                        "sector_name": m.sector_name,
                        "composite_score": round(m.composite_score, 4),
                    }
                    for m in c.members
                ],
                ensure_ascii=False,
            ),
            "n": len(c.members),
            "narr": c.narrative,
            "mom": c.momentum_signal,
            "score_avg": c.composite_score_avg,
            "model": c.model_used,
            "latency": c.latency_seconds,
        }
        for c in clusters
    ]
    with engine.begin() as conn:
        conn.execute(sql, rows)
    log.info("[concept_cluster] persisted %d clusters for %s",
             len(clusters), clusters[0].trade_date)


# ── Public entry point ────────────────────────────────────────────────────────

def run_concept_cluster(
    engine: Engine,
    *,
    trade_date: dt.date,
    top_n: int = 60,
    temperature: float = 0.2,
    persist: bool = True,
    llm_client: LLMClient | None = None,
    on_log: Any = None,
) -> list[ConceptCluster]:
    """Cluster sectors into thematic investment narratives for `trade_date`.

    Args:
        engine:     SQLAlchemy engine.
        trade_date: The market date to cluster (uses that day's factor_daily rows).
        top_n:      Number of top-ranked sectors to include in the prompt (default 60).
        temperature: LLM temperature (lower = more deterministic, default 0.2).
        persist:    Whether to write results to DB (default True).
        llm_client: LLMClient instance; creates one from settings if None.
        on_log:     Optional callable(str) for progress logging.

    Returns:
        List of ConceptCluster dataclasses (empty if no factor data available).
    """
    def _emit(msg: str) -> None:
        if on_log:
            on_log(msg)
        log.info(msg)

    _emit(f"[concept_cluster] {trade_date}: loading sector scores (top {top_n}) …")
    df = _load_sector_scores(engine, trade_date, top_n=top_n)
    if df.empty:
        _emit(f"[concept_cluster] no factor_daily data for {trade_date}; aborting")
        return []

    _emit(f"[concept_cluster] {len(df)} sectors loaded; calling LLM …")
    if llm_client is None:
        llm_client = LLMClient()

    try:
        clusters_raw, model_used, latency = _call_llm(
            df, trade_date, client=llm_client, temperature=temperature
        )
    except Exception as exc:
        _emit(f"[concept_cluster] LLM call failed: {exc}")
        raise

    _emit(f"[concept_cluster] LLM returned {len(clusters_raw)} clusters in {latency:.1f}s (model={model_used})")

    clusters = _assemble_clusters(clusters_raw, df, trade_date, model_used, latency)
    _emit(f"[concept_cluster] assembled {len(clusters)} valid clusters")

    for c in clusters:
        _emit(f"  [{c.momentum_signal:12s}] {c.cluster_name} ({len(c.members)} sectors, avg={c.composite_score_avg:+.3f})")

    if persist:
        ensure_table(engine)
        _persist_clusters(engine, clusters)
        _emit(f"[concept_cluster] persisted to DB")

    return clusters


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_clusters_for_date(
    engine: Engine,
    trade_date: dt.date,
) -> list[dict[str, Any]]:
    """Return all cluster rows for a given date (without reconstructing dataclasses)."""
    sql = text(f"""
        SELECT cluster_id::text, cluster_name, cluster_label, n_members,
               narrative, momentum_signal, composite_score_avg,
               member_codes, model_used, latency_seconds, created_at
        FROM {SCHEMA}.llm_concept_clusters
        WHERE trade_date = :td
        ORDER BY composite_score_avg DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"td": trade_date}).fetchall()
    return [
        {
            "cluster_id": r[0], "cluster_name": r[1], "cluster_label": r[2],
            "n_members": r[3], "narrative": r[4], "momentum_signal": r[5],
            "composite_score_avg": r[6], "member_codes": r[7],
            "model_used": r[8], "latency_seconds": r[9], "created_at": r[10],
        }
        for r in rows
    ]
