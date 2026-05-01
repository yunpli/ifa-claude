"""SmartMoney LLM Augmentation — B3: Stock Signal Counterfactual Analysis.

Asks: "What would it take to invalidate this signal?" — combining deterministic
component ablation with LLM-generated counterfactual narratives, fragility
assessment, and invalidation paths.

Why this matters
----------------
A 龙头 stock signal with composite_score=0.85 might be "carried" by a single
component (e.g. limit_bonus from one limit-up day). If that one event is removed,
the signal collapses. Knowing which signals are robust vs fragile helps:
  • Filter the candidate pool (drop fragile leaders)
  • Set position sizing (smaller for fragile signals)
  • Build risk narratives in the evening report
  • Track regime-specific failure modes

Workflow
--------
1. Load stock_signals_daily for the target date (signals with evidence_json).
2. For each signal, run deterministic component ablation:
     - Zero out / median-shift each component in turn
     - Compute new composite_score, measure delta
     - Identify load-bearing components
     - Compute fragility_score (how concentrated the signal's support is)
3. Batch signals to LLM with evidence + ablation summary.
4. LLM returns:
     - counterfactual_narrative: 2-4 sentence story of how this could fail
     - invalidation_paths: list of plausible scenarios that flip the signal
     - robustness_verdict: 'robust' | 'moderate' | 'fragile'
     - risk_factors: bullet list of asymmetric risks
5. Persist to smartmoney.llm_counterfactuals (UPSERT by trade_date, ts_code, role).
6. Return list of CounterfactualAnalysis dataclasses.

DB Table
--------
    smartmoney.llm_counterfactuals
    ───────────────────────────────
    counterfactual_id        UUID PK
    trade_date               DATE
    ts_code                  TEXT
    name                     TEXT
    role                     TEXT
    original_score           FLOAT
    sector_code              TEXT
    sector_role              TEXT
    theme                    TEXT
    component_ablations      JSONB     -- deterministic per-component impact
    fragility_score          FLOAT     -- 0-1, higher = more fragile
    load_bearing_components  JSONB     -- ranked component names + impacts
    invalidation_paths       JSONB     -- LLM-generated scenarios
    counterfactual_narrative TEXT      -- LLM 2-4 sentence story (CN)
    robustness_verdict       TEXT      -- 'robust' | 'moderate' | 'fragile'
    risk_factors             JSONB     -- LLM bullet list (CN)
    model_used               TEXT
    latency_seconds          FLOAT
    created_at               TIMESTAMPTZ
    UNIQUE (trade_date, ts_code, role)

Usage
-----
    from ifa.families.smartmoney.llm_aug.counterfactual import run_counterfactual

    analyses = run_counterfactual(engine, trade_date=dt.date(2026, 4, 30))
    for a in analyses:
        print(a.ts_code, a.robustness_verdict, a.fragility_score)
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

ROBUSTNESS_VERDICTS = frozenset({"robust", "moderate", "fragile"})

# Components present in evidence_json that contribute to composite_score.
# These match the leader.py scoring inputs.
ABLATABLE_COMPONENTS = (
    "rs",              # relative strength vs sector
    "amount_rank",     # turnover percentile
    "elg_rank",        # extra-large net flow percentile
    "limit_bonus",     # limit-up reward
    "top_inst_bonus",  # top-inst-list participant
)

# Default weights — match leader.py's defaults.  If params override exists in
# the evidence (e.g. evidence['_weights']), we'll use that instead.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "rs": 0.30,
    "amount_rank": 0.20,
    "elg_rank": 0.20,
    "limit_bonus": 0.20,
    "top_inst_bonus": 0.10,
}


# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.llm_counterfactuals (
    counterfactual_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_date               DATE NOT NULL,
    ts_code                  TEXT NOT NULL,
    name                     TEXT,
    role                     TEXT NOT NULL,
    original_score           FLOAT,
    sector_code              TEXT,
    sector_role              TEXT,
    theme                    TEXT,
    component_ablations      JSONB NOT NULL DEFAULT '{{}}',
    fragility_score          FLOAT,
    load_bearing_components  JSONB NOT NULL DEFAULT '[]',
    invalidation_paths       JSONB NOT NULL DEFAULT '[]',
    counterfactual_narrative TEXT,
    robustness_verdict       TEXT CHECK (robustness_verdict IN ('robust', 'moderate', 'fragile')),
    risk_factors             JSONB NOT NULL DEFAULT '[]',
    model_used               TEXT,
    latency_seconds          FLOAT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_llm_counterfactuals_signal
    ON {SCHEMA}.llm_counterfactuals (trade_date, ts_code, role);
"""


def ensure_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
    log.debug("[counterfactual] table ensured")


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class ComponentAblation:
    """Deterministic ablation result for a single evidence component."""
    component: str
    actual_value: float          # original component value
    counterfactual_value: float  # what we replaced it with (median 0.5 or 0)
    delta_score: float           # original_score - counterfactual_score
    pct_of_score: float          # delta / original_score
    direction: str               # 'load_bearing' | 'neutral' | 'drag'


@dataclass
class CounterfactualAnalysis:
    counterfactual_id: str
    trade_date: dt.date
    ts_code: str
    name: str
    role: str
    original_score: float
    sector_code: str | None
    sector_role: str | None
    theme: str | None

    # Deterministic ablation
    component_ablations: list[ComponentAblation]
    fragility_score: float                        # 0-1
    load_bearing_components: list[dict[str, Any]] # ranked

    # LLM analysis
    invalidation_paths: list[str]
    counterfactual_narrative: str
    robustness_verdict: str   # robust | moderate | fragile
    risk_factors: list[str]

    model_used: str
    latency_seconds: float


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_signals(
    engine: Engine,
    trade_date: dt.date,
    *,
    roles: tuple[str, ...] = ("龙头", "中军", "情绪先锋"),
    limit: int = 80,
) -> pd.DataFrame:
    """Load stock signals for the date with non-null evidence_json."""
    placeholders = ", ".join(f"'{r}'" for r in roles)
    sql = text(f"""
        SELECT
            trade_date, ts_code, name, role, score,
            primary_sector_code, primary_sector_source,
            theme, lu_desc, evidence_json
        FROM {SCHEMA}.stock_signals_daily
        WHERE trade_date = :td
          AND role IN ({placeholders})
          AND evidence_json IS NOT NULL
        ORDER BY score DESC
        LIMIT :lim
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"td": trade_date, "lim": limit}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "trade_date", "ts_code", "name", "role", "score",
        "sector_code", "sector_source", "theme", "lu_desc", "evidence",
    ])
    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0.0)
    return df


# ── Deterministic ablation logic ──────────────────────────────────────────────

def _extract_component_value(evidence: dict[str, Any], component: str) -> float | None:
    """Pull a component's value from evidence_json. Returns None if missing."""
    v = evidence.get(component)
    if v is None:
        # Fallback aliases
        aliases = {
            "rs": ["rs_rank"],
            "top_inst_bonus": ["has_top_inst", "top_inst"],
        }
        for alias in aliases.get(component, []):
            if alias in evidence:
                v = evidence[alias]
                break
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _component_counterfactual_value(component: str, actual: float) -> float:
    """The value to substitute when ablating this component.

    For ranked features (rs, amount_rank, elg_rank), median = 0.5 (neutral).
    For binary bonuses (limit_bonus, top_inst_bonus), counterfactual = 0
    (signal removed).
    """
    if component in ("rs", "amount_rank", "elg_rank"):
        return 0.5
    return 0.0


def _compute_ablations(
    evidence: dict[str, Any],
    original_score: float,
    weights: dict[str, float] | None = None,
) -> list[ComponentAblation]:
    """For each ablatable component, compute the counterfactual score impact."""
    if weights is None:
        # Allow per-signal weight override via evidence['_weights']
        weights = evidence.get("_weights") or _DEFAULT_WEIGHTS
    weights = {k: weights.get(k, _DEFAULT_WEIGHTS[k]) for k in ABLATABLE_COMPONENTS}

    # Reconstruct the implied weighted contributions from evidence values.
    # We don't have the *exact* original computation, but for ablation
    # we only need the *delta* when one component changes:
    #   new_score = original_score - w * actual + w * cf_value
    ablations: list[ComponentAblation] = []
    for comp in ABLATABLE_COMPONENTS:
        actual = _extract_component_value(evidence, comp)
        if actual is None:
            continue
        cf_value = _component_counterfactual_value(comp, actual)
        w = weights[comp]
        delta = w * (actual - cf_value)   # positive = component helps the score

        pct_of = delta / original_score if abs(original_score) > 1e-9 else 0.0

        if delta > 0.05 * abs(original_score):
            direction = "load_bearing"
        elif delta < -0.02 * abs(original_score):
            direction = "drag"
        else:
            direction = "neutral"

        ablations.append(ComponentAblation(
            component=comp,
            actual_value=round(actual, 4),
            counterfactual_value=round(cf_value, 4),
            delta_score=round(delta, 4),
            pct_of_score=round(pct_of, 3),
            direction=direction,
        ))
    return ablations


def _compute_fragility(ablations: list[ComponentAblation]) -> float:
    """Fragility ∈ [0, 1].  High when a single component carries the score.

    Defined as: max(load-bearing delta) / sum(positive deltas).
    A perfectly balanced signal (4 equal-contributing components) → ~0.25.
    A signal carried by one component → ~1.0.
    """
    positive_deltas = [a.delta_score for a in ablations if a.delta_score > 0]
    if not positive_deltas:
        return 0.0
    total = sum(positive_deltas)
    if total <= 1e-9:
        return 0.0
    return min(max(positive_deltas) / total, 1.0)


def _rank_load_bearing(ablations: list[ComponentAblation]) -> list[dict[str, Any]]:
    """Return components ranked by absolute impact on score."""
    sorted_abl = sorted(ablations, key=lambda a: abs(a.delta_score), reverse=True)
    return [
        {
            "component": a.component,
            "actual_value": a.actual_value,
            "delta_score": a.delta_score,
            "pct_of_score": a.pct_of_score,
            "direction": a.direction,
        }
        for a in sorted_abl
    ]


# ── Prompt + LLM ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是一位资深A股龙头股研究员，专长于"反事实分析"——评估单个股票信号的鲁棒性。

每个信号都由若干证据组件构成（如相对强弱RS、成交额排名、超大单、涨停加分、龙虎榜）。
你将收到信号原始得分 + 每个组件的"消除影响"（如果该组件被中性化，得分会下降多少）。

你的任务：
1. 判断信号鲁棒性：
   - robust（鲁棒）: 多个组件均衡贡献，没有单一组件超过总贡献的50%
   - moderate（中等）: 1-2个组件占主导，但其他组件也有支撑
   - fragile（脆弱）: 单一组件贡献超过60%，或存在明显的"运气"成分（如单日涨停）

2. 列出 invalidation_paths（失效路径）：列出 2-4 个具体的、可观察的市场情景，
   如果发生，会让这个信号失效。例如：
   - "明日跌停板被打开，连板逻辑断裂"
   - "板块龙头切换，资金转向其他细分方向"

3. 给出 counterfactual_narrative（反事实叙述）：2-3 句话，描述这个信号最可能的"故事破灭"路径。

4. 列出 risk_factors（风险点）：3-5 个该信号的不对称风险点（CN 短句）。

输出纯 JSON：
{
  "analyses": [
    {
      "ts_code": "<原样复制>",
      "robustness_verdict": "robust" | "moderate" | "fragile",
      "counterfactual_narrative": "...",
      "invalidation_paths": ["...", "..."],
      "risk_factors": ["...", "..."]
    }
  ]
}
"""

_USER_TEMPLATE = """\
分析日期：{trade_date}
共 {n} 个股票信号，每个信号附带证据组件 + 消除影响分析（fragility_score越高越脆弱）：

{signals_block}

请对每个信号做反事实分析，输出 JSON。
"""


def _build_signal_block(
    signals_with_ablations: list[tuple[pd.Series, list[ComponentAblation], float]],
) -> str:
    """Format signals + ablations for the LLM prompt."""
    lines = []
    for sig, ablations, fragility in signals_with_ablations:
        evidence = sig["evidence"] or {}
        sector_name = evidence.get("sector_name", "")
        consec = evidence.get("consec_boards", 0)
        lu_desc = sig.get("lu_desc") or evidence.get("lu_desc", "")

        lines.append(f"信号: {sig['ts_code']} {sig['name']}  role={sig['role']}")
        lines.append(f"  sector: {sector_name} (role={evidence.get('sector_role', '?')})")
        lines.append(f"  theme: {sig.get('theme') or '-'}")
        lines.append(
            f"  原始得分: {float(sig['score']):.3f}  "
            f"fragility_score: {fragility:.2f}  "
            f"连板: {consec}  涨停描述: {lu_desc or '-'}"
        )
        lines.append("  组件消除影响（按绝对影响排序）:")
        sorted_abl = sorted(ablations, key=lambda a: abs(a.delta_score), reverse=True)
        for a in sorted_abl:
            lines.append(
                f"    - {a.component}: actual={a.actual_value:+.3f} "
                f"→ cf={a.counterfactual_value:+.3f} | "
                f"delta={a.delta_score:+.3f} ({a.pct_of_score:+.1%} of score) | "
                f"{a.direction}"
            )
        lines.append("")
    return "\n".join(lines)


def _call_llm(
    signals_with_ablations: list[tuple[pd.Series, list[ComponentAblation], float]],
    trade_date: dt.date,
    *,
    client: LLMClient,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> tuple[list[dict[str, Any]], str, float]:
    block = _build_signal_block(signals_with_ablations)
    user_msg = _USER_TEMPLATE.format(
        trade_date=trade_date.isoformat(),
        n=len(signals_with_ablations),
        signals_block=block,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    resp = client.chat(messages, temperature=temperature, max_tokens=max_tokens)
    parsed = resp.parse_json()
    return parsed.get("analyses", []), resp.model, resp.latency_seconds


# ── Assembly ──────────────────────────────────────────────────────────────────

def _assemble_analyses(
    signals_with_ablations: list[tuple[pd.Series, list[ComponentAblation], float]],
    llm_results: list[dict[str, Any]],
    model_used: str,
    latency_seconds: float,
    trade_date: dt.date,
) -> list[CounterfactualAnalysis]:
    # Index LLM results by ts_code for robust matching
    by_ts: dict[str, dict[str, Any]] = {}
    for r in llm_results:
        ts = r.get("ts_code", "")
        if ts:
            by_ts[ts] = r

    out: list[CounterfactualAnalysis] = []
    per_signal_latency = latency_seconds / max(len(signals_with_ablations), 1)

    for sig, ablations, fragility in signals_with_ablations:
        ts_code = sig["ts_code"]
        llm_r = by_ts.get(ts_code, {})
        verdict = llm_r.get("robustness_verdict", "moderate")
        if verdict not in ROBUSTNESS_VERDICTS:
            # Fall back to fragility-based verdict
            verdict = "fragile" if fragility > 0.6 else ("robust" if fragility < 0.35 else "moderate")

        evidence = sig["evidence"] or {}

        out.append(CounterfactualAnalysis(
            counterfactual_id=str(uuid.uuid4()),
            trade_date=trade_date,
            ts_code=ts_code,
            name=sig["name"] or "",
            role=sig["role"],
            original_score=float(sig["score"]),
            sector_code=sig.get("sector_code"),
            sector_role=evidence.get("sector_role"),
            theme=sig.get("theme"),
            component_ablations=ablations,
            fragility_score=fragility,
            load_bearing_components=_rank_load_bearing(ablations),
            invalidation_paths=llm_r.get("invalidation_paths", []),
            counterfactual_narrative=llm_r.get("counterfactual_narrative", ""),
            robustness_verdict=verdict,
            risk_factors=llm_r.get("risk_factors", []),
            model_used=model_used,
            latency_seconds=per_signal_latency,
        ))
    return out


# ── DB persistence ────────────────────────────────────────────────────────────

def _persist_analyses(engine: Engine, analyses: list[CounterfactualAnalysis]) -> None:
    if not analyses:
        return
    sql = text(f"""
        INSERT INTO {SCHEMA}.llm_counterfactuals
            (counterfactual_id, trade_date, ts_code, name, role,
             original_score, sector_code, sector_role, theme,
             component_ablations, fragility_score, load_bearing_components,
             invalidation_paths, counterfactual_narrative,
             robustness_verdict, risk_factors,
             model_used, latency_seconds)
        VALUES
            (:cid, :td, :ts, :name, :role,
             :score, :sec_code, :sec_role, :theme,
             cast(:abl AS jsonb), :frag, cast(:lb AS jsonb),
             cast(:paths AS jsonb), :narr,
             :verdict, cast(:risks AS jsonb),
             :model, :latency)
        ON CONFLICT (trade_date, ts_code, role) DO UPDATE SET
            original_score           = EXCLUDED.original_score,
            sector_code              = EXCLUDED.sector_code,
            sector_role              = EXCLUDED.sector_role,
            theme                    = EXCLUDED.theme,
            component_ablations      = EXCLUDED.component_ablations,
            fragility_score          = EXCLUDED.fragility_score,
            load_bearing_components  = EXCLUDED.load_bearing_components,
            invalidation_paths       = EXCLUDED.invalidation_paths,
            counterfactual_narrative = EXCLUDED.counterfactual_narrative,
            robustness_verdict       = EXCLUDED.robustness_verdict,
            risk_factors             = EXCLUDED.risk_factors,
            model_used               = EXCLUDED.model_used,
            latency_seconds          = EXCLUDED.latency_seconds,
            created_at               = now()
    """)
    rows = []
    for a in analyses:
        abl_json = json.dumps(
            [
                {
                    "component": x.component,
                    "actual_value": x.actual_value,
                    "counterfactual_value": x.counterfactual_value,
                    "delta_score": x.delta_score,
                    "pct_of_score": x.pct_of_score,
                    "direction": x.direction,
                }
                for x in a.component_ablations
            ],
            ensure_ascii=False,
        )
        rows.append({
            "cid": a.counterfactual_id,
            "td": a.trade_date,
            "ts": a.ts_code,
            "name": a.name,
            "role": a.role,
            "score": a.original_score,
            "sec_code": a.sector_code,
            "sec_role": a.sector_role,
            "theme": a.theme,
            "abl": abl_json,
            "frag": a.fragility_score,
            "lb": json.dumps(a.load_bearing_components, ensure_ascii=False),
            "paths": json.dumps(a.invalidation_paths, ensure_ascii=False),
            "narr": a.counterfactual_narrative,
            "verdict": a.robustness_verdict,
            "risks": json.dumps(a.risk_factors, ensure_ascii=False),
            "model": a.model_used,
            "latency": a.latency_seconds,
        })
    with engine.begin() as conn:
        conn.execute(sql, rows)
    log.info("[counterfactual] persisted %d analyses for %s",
             len(analyses), analyses[0].trade_date)


# ── Public entry point ────────────────────────────────────────────────────────

def run_counterfactual(
    engine: Engine,
    *,
    trade_date: dt.date,
    roles: tuple[str, ...] = ("龙头", "中军", "情绪先锋"),
    max_signals: int = 30,
    batch_size: int = 8,
    weights: dict[str, float] | None = None,
    persist: bool = True,
    llm_client: LLMClient | None = None,
    on_log: Any = None,
) -> list[CounterfactualAnalysis]:
    """Generate counterfactual analyses for stock signals.

    Args:
        engine:       SQLAlchemy engine.
        trade_date:   Date to analyze.
        roles:        Which signal roles to analyze (default 龙头/中军/情绪先锋).
        max_signals:  Cap on signals to analyze (default 30; ranked by score).
        batch_size:   Signals per LLM call (default 8).
        weights:      Override component weights for ablation.
        persist:      Write to DB.
        llm_client:   LLMClient; creates from settings if None.
        on_log:       Optional callable(str) for progress logging.

    Returns:
        List of CounterfactualAnalysis dataclasses (empty if no signals).
    """
    def _emit(msg: str) -> None:
        if on_log:
            on_log(msg)
        log.info(msg)

    _emit(f"[counterfactual] {trade_date}: loading signals (roles={roles}, max={max_signals}) …")
    df = _load_signals(engine, trade_date, roles=roles, limit=max_signals)
    if df.empty:
        _emit(f"[counterfactual] no stock_signals_daily rows for {trade_date}; aborting")
        return []

    _emit(f"[counterfactual] {len(df)} signals loaded; computing deterministic ablations …")

    # Phase 1: deterministic ablation for each signal
    signals_with_ablations: list[tuple[pd.Series, list[ComponentAblation], float]] = []
    for _, sig in df.iterrows():
        evidence = sig["evidence"] or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except (TypeError, json.JSONDecodeError):
                evidence = {}
        # Mutate the row's evidence to dict for downstream
        sig["evidence"] = evidence
        ablations = _compute_ablations(evidence, float(sig["score"]), weights=weights)
        if not ablations:
            log.warning("[counterfactual] %s: no ablatable components in evidence; skipping",
                        sig["ts_code"])
            continue
        fragility = _compute_fragility(ablations)
        signals_with_ablations.append((sig, ablations, fragility))

    if not signals_with_ablations:
        _emit("[counterfactual] no signals with extractable components; aborting")
        return []

    _emit(f"[counterfactual] {len(signals_with_ablations)} signals had ablatable components")

    # Phase 2: LLM batches
    if llm_client is None:
        llm_client = LLMClient()

    all_results: list[dict[str, Any]] = []
    total_latency = 0.0
    model_used = ""

    for batch_start in range(0, len(signals_with_ablations), batch_size):
        batch = signals_with_ablations[batch_start: batch_start + batch_size]
        _emit(f"[counterfactual] LLM batch {batch_start // batch_size + 1} "
              f"({len(batch)} signals) …")
        try:
            llm_results, model_used, latency = _call_llm(
                batch, trade_date, client=llm_client,
            )
        except Exception as exc:
            _emit(f"[counterfactual] LLM call failed: {exc}")
            raise
        all_results.extend(llm_results)
        total_latency += latency

    _emit(f"[counterfactual] LLM total {total_latency:.1f}s, returned {len(all_results)} analyses")

    # Phase 3: assemble final dataclasses
    analyses = _assemble_analyses(
        signals_with_ablations, all_results, model_used, total_latency, trade_date
    )

    # Summary stats
    by_verdict: dict[str, int] = {"robust": 0, "moderate": 0, "fragile": 0}
    for a in analyses:
        by_verdict[a.robustness_verdict] = by_verdict.get(a.robustness_verdict, 0) + 1
    _emit(
        f"[counterfactual] verdicts: robust={by_verdict['robust']} "
        f"moderate={by_verdict['moderate']} fragile={by_verdict['fragile']}"
    )

    # Top fragile signals (worth flagging)
    fragile = sorted(
        [a for a in analyses if a.robustness_verdict == "fragile"],
        key=lambda a: a.fragility_score, reverse=True,
    )[:5]
    for a in fragile:
        top_comp = a.load_bearing_components[0] if a.load_bearing_components else {}
        _emit(f"  ⚠ fragile: {a.ts_code} {a.name} (frag={a.fragility_score:.2f}, "
              f"carried by {top_comp.get('component', '?')})")

    if persist:
        ensure_table(engine)
        _persist_analyses(engine, analyses)

    return analyses


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_fragile_signals(
    engine: Engine,
    trade_date: dt.date,
    *,
    min_fragility: float = 0.55,
) -> list[dict[str, Any]]:
    """Return fragile signals for a given date — useful for filtering candidate pool."""
    sql = text(f"""
        SELECT trade_date, ts_code, name, role, original_score,
               fragility_score, robustness_verdict,
               load_bearing_components, invalidation_paths,
               counterfactual_narrative, risk_factors
        FROM {SCHEMA}.llm_counterfactuals
        WHERE trade_date = :td
          AND (robustness_verdict = 'fragile' OR fragility_score >= :min_frag)
        ORDER BY fragility_score DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"td": trade_date, "min_frag": min_fragility}).fetchall()
    return [
        {
            "trade_date": r[0], "ts_code": r[1], "name": r[2], "role": r[3],
            "original_score": r[4], "fragility_score": r[5],
            "robustness_verdict": r[6], "load_bearing_components": r[7],
            "invalidation_paths": r[8], "counterfactual_narrative": r[9],
            "risk_factors": r[10],
        }
        for r in rows
    ]


def get_signal_counterfactual(
    engine: Engine,
    trade_date: dt.date,
    ts_code: str,
    role: str,
) -> dict[str, Any] | None:
    """Return the full counterfactual record for one signal."""
    sql = text(f"""
        SELECT trade_date, ts_code, name, role, original_score,
               fragility_score, robustness_verdict, theme,
               component_ablations, load_bearing_components,
               invalidation_paths, counterfactual_narrative, risk_factors,
               model_used, created_at
        FROM {SCHEMA}.llm_counterfactuals
        WHERE trade_date = :td AND ts_code = :ts AND role = :role
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"td": trade_date, "ts": ts_code, "role": role}).fetchone()
    if not row:
        return None
    return {
        "trade_date": row[0], "ts_code": row[1], "name": row[2], "role": row[3],
        "original_score": row[4], "fragility_score": row[5],
        "robustness_verdict": row[6], "theme": row[7],
        "component_ablations": row[8], "load_bearing_components": row[9],
        "invalidation_paths": row[10], "counterfactual_narrative": row[11],
        "risk_factors": row[12], "model_used": row[13], "created_at": row[14],
    }
