"""SmartMoney LLM Augmentation — B4: Backtest Forensics.

Post-hoc LLM analysis of backtest results: why did factors work or fail,
which market regimes drove the IC, and what parameter changes are warranted.

Workflow
--------
1. Load metrics for a given backtest_run_id from smartmoney.backtest_metrics.
2. Load per-date IC time series from factor_daily (reconstructed via rolling
   cross-section IC computation or loaded from existing per-date metric rows).
3. Load market context: market_state_daily and llm_regime_states (if available)
   for the same period.
4. Call LLM with a structured forensics prompt — factor by factor analysis,
   regime attribution, and concrete improvement recommendations.
5. Persist forensics report to smartmoney.llm_backtest_forensics.
6. Return a ForensicsReport dataclass.

DB Table
--------
    smartmoney.llm_backtest_forensics
    ──────────────────────────────────
    forensics_id        UUID PK
    backtest_run_id     UUID FK → smartmoney.backtest_runs
    analysis_date       DATE     (when this forensics run was done)
    factor_assessments  JSONB    [{factor, ic_ir, verdict, strength, regime_sensitivity, notes}]
    top_findings        JSONB    [str]  — top 3-5 key findings (CN)
    regime_attribution  TEXT     — which regimes drove (hurt) performance
    improvement_recs    JSONB    [str]  — actionable parameter / logic recommendations
    overall_verdict     TEXT     'strong' | 'acceptable' | 'weak' | 'overfit_suspected'
    model_used          TEXT
    latency_seconds     FLOAT
    created_at          TIMESTAMPTZ

Usage
-----
    from ifa.families.smartmoney.llm_aug.backtest_forensics import run_backtest_forensics

    report = run_backtest_forensics(engine, backtest_run_id='758314b4-...')
    print(report.overall_verdict, report.top_findings)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.llm.client import LLMClient

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"

OVERALL_VERDICTS = frozenset({"strong", "acceptable", "weak", "overfit_suspected"})
FACTOR_STRENGTHS = frozenset({"strong", "moderate", "weak", "inverse", "noisy"})

# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.llm_backtest_forensics (
    forensics_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    backtest_run_id     UUID NOT NULL,
    analysis_date       DATE NOT NULL,
    factor_assessments  JSONB NOT NULL DEFAULT '[]',
    top_findings        JSONB NOT NULL DEFAULT '[]',
    regime_attribution  TEXT,
    improvement_recs    JSONB NOT NULL DEFAULT '[]',
    overall_verdict     TEXT CHECK (overall_verdict IN (
                            'strong', 'acceptable', 'weak', 'overfit_suspected')),
    model_used          TEXT,
    latency_seconds     FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_llm_backtest_forensics_run
    ON {SCHEMA}.llm_backtest_forensics (backtest_run_id);
"""


def ensure_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
    log.debug("[backtest_forensics] table ensured")


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class FactorAssessment:
    factor: str
    ic_mean: float
    ic_ir: float
    ic_positive_rate: float
    rank_ic_mean: float
    topn_hit_rate: float
    verdict: str          # 'working' | 'marginal' | 'failing'
    strength: str         # 'strong' | 'moderate' | 'weak' | 'inverse' | 'noisy'
    regime_sensitivity: str   # LLM note on which regimes this factor works best
    notes: str


@dataclass
class ForensicsReport:
    forensics_id: str
    backtest_run_id: str
    analysis_date: dt.date
    factor_assessments: list[FactorAssessment]
    top_findings: list[str]
    regime_attribution: str
    improvement_recs: list[str]
    overall_verdict: str
    model_used: str
    latency_seconds: float


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_backtest_run(engine: Engine, backtest_run_id: str) -> dict[str, Any] | None:
    sql = text(f"""
        SELECT backtest_run_id::text, start_date, end_date,
               param_version_used, status, notes, started_at
        FROM {SCHEMA}.backtest_runs
        WHERE backtest_run_id = cast(:rid AS uuid)
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"rid": backtest_run_id}).fetchone()
    if not row:
        return None
    return {
        "backtest_run_id": row[0],
        "start_date": row[1],
        "end_date": row[2],
        "param_version": row[3],
        "status": row[4],
        "notes": row[5],
        "started_at": row[6],
    }


def _load_metrics(engine: Engine, backtest_run_id: str) -> pd.DataFrame:
    """Load all metric rows for this backtest run."""
    sql = text(f"""
        SELECT factor_name, metric_name, window_days, group_label, metric_value, n_samples
        FROM {SCHEMA}.backtest_metrics
        WHERE backtest_run_id = cast(:rid AS uuid)
        ORDER BY factor_name, metric_name, window_days
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"rid": backtest_run_id}).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=[
        "factor_name", "metric_name", "window_days", "group_label", "metric_value", "n_samples"
    ])


def _pivot_metrics(metrics_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Pivot metrics into {factor → {metric → value}} for window=1."""
    result: dict[str, dict[str, Any]] = {}
    day1 = metrics_df[metrics_df["window_days"] == 1]
    for factor, grp in day1.groupby("factor_name"):
        factor_metrics: dict[str, Any] = {}
        for _, row in grp.iterrows():
            key = row["metric_name"]
            lbl = row["group_label"]
            full_key = f"{key}_{lbl}" if lbl else key
            factor_metrics[full_key] = float(row["metric_value"]) if row["metric_value"] is not None else None
        result[str(factor)] = factor_metrics
    return result


def _load_regime_context(
    engine: Engine,
    start_date: dt.date,
    end_date: dt.date,
) -> str:
    """Load regime distribution over the backtest window if available."""
    try:
        sql = text(f"""
            SELECT regime_label, COUNT(*) AS n_days,
                   ROUND(AVG(confidence)::numeric, 2) AS avg_conf
            FROM {SCHEMA}.llm_regime_states
            WHERE trade_date BETWEEN :start AND :end
            GROUP BY regime_label
            ORDER BY n_days DESC
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"start": start_date, "end": end_date}).fetchall()
        if not rows:
            return "no regime data available for this period"
        lines = [f"{r[0]}: {r[1]} days (avg_conf={r[2]})" for r in rows]
        return "Regime distribution:\n" + "\n".join(lines)
    except Exception:  # noqa: BLE001
        return "regime data unavailable"


def _load_market_context(
    engine: Engine,
    start_date: dt.date,
    end_date: dt.date,
) -> str:
    """Load market state distribution over the backtest period."""
    try:
        sql = text(f"""
            SELECT market_state, COUNT(*) AS n_days
            FROM {SCHEMA}.market_state_daily
            WHERE trade_date BETWEEN :start AND :end
            GROUP BY market_state
            ORDER BY n_days DESC
            LIMIT 8
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"start": start_date, "end": end_date}).fetchall()
        if not rows:
            return "no market state data"
        lines = [f"{r[0]}: {r[1]} days" for r in rows]
        return "Market states:\n" + "\n".join(lines)
    except Exception:  # noqa: BLE001
        return "market state data unavailable"


# ── Prompt + LLM ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是一位资深量化策略研究员，擅长A股Smart Money因子回测的深度诊断分析。

你的任务是对回测结果进行全面的"法证分析"（Forensics）：
1. 评估每个因子的有效性（是否真的有alpha，在哪些市场环境下有效）
2. 识别潜在问题（过拟合、因子失效期、市场体制变化影响）
3. 给出可执行的改进建议

四个因子含义：
- heat_score: 短期资金热度（主动买入强度）
- trend_score: 价格趋势动量（突破和延续性）
- persistence_score: 多日持续流入（聪明钱跟踪）
- crowding_score: 拥挤度（高拥挤=可能反转，通常负相关）

判断标准（window=1d）：
- IC IR > 0.5: 因子有效
- IC IR 0.2-0.5: 边缘有效，需要结合市场环境
- IC IR < 0.2 或 IC Mean ≈ 0: 因子基本无效
- IC 正率 > 55%: 因子稳定性好

输出纯 JSON：
{
  "factor_assessments": [
    {
      "factor": "heat_score",
      "verdict": "working" | "marginal" | "failing",
      "strength": "strong" | "moderate" | "weak" | "inverse" | "noisy",
      "regime_sensitivity": "在哪些市场环境表现最好/最差（CN）",
      "notes": "关键发现（CN，1-2句）"
    }
  ],
  "top_findings": ["发现1（CN）", "发现2（CN）", ...],
  "regime_attribution": "哪些市场环境驱动/拖累了整体表现（CN，2-3句）",
  "improvement_recs": ["建议1（CN）", "建议2（CN）", ...],
  "overall_verdict": "strong" | "acceptable" | "weak" | "overfit_suspected"
}
"""

_USER_TEMPLATE = """\
回测区间：{start_date} → {end_date}（共 {n_days} 个自然日）
参数版本：{param_version}
状态：{status}
备注：{notes}

因子指标汇总（window=1d）：
{metrics_block}

市场环境背景：
{market_ctx}

{regime_ctx}

请对上述回测结果进行法证分析，输出 JSON。
"""


def _build_metrics_block(pivoted: dict[str, dict[str, Any]]) -> str:
    lines = []
    header = f"{'因子':<25} {'IC Mean':>9} {'IC IR':>8} {'IC Pos%':>8} {'RankIC':>9} {'TopN%':>7}"
    lines.append(header)
    lines.append("-" * 72)
    for factor, m in sorted(pivoted.items()):
        ic_m = m.get("ic", None)
        ic_ir = m.get("ic_ir", None)
        ic_pos = m.get("ic_positive_rate", None)
        ric = m.get("rank_ic", None)
        topn = m.get("topn_hit", None)

        def _f(v: float | None, fmt: str = ".4f") -> str:
            return f"{v:{fmt}}" if v is not None else "n/a"

        lines.append(
            f"{factor:<25} {_f(ic_m, '+.4f'):>9} {_f(ic_ir, '+.3f'):>8} "
            f"{_f(ic_pos, '.1%') if ic_pos is not None else 'n/a':>8} "
            f"{_f(ric, '+.4f'):>9} "
            f"{_f(topn, '.1%') if topn is not None else 'n/a':>7}"
        )
        # Group returns
        grp_vals = {k.replace("group_return_", ""): v for k, v in m.items() if k.startswith("group_return_Q")}
        if grp_vals:
            grp_str = "  groups: " + "  ".join(
                f"{lbl}={v:+.3f}" for lbl, v in sorted(grp_vals.items()) if v is not None
            )
            lines.append(grp_str)
    return "\n".join(lines)


def _call_llm(
    run_info: dict[str, Any],
    pivoted: dict[str, dict[str, Any]],
    market_ctx: str,
    regime_ctx: str,
    *,
    client: LLMClient,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> tuple[dict[str, Any], str, float]:
    metrics_block = _build_metrics_block(pivoted)
    start = run_info["start_date"]
    end = run_info["end_date"]
    if isinstance(start, dt.date):
        n_days = (end - start).days
    else:
        n_days = "?"
    user_msg = _USER_TEMPLATE.format(
        start_date=start,
        end_date=end,
        n_days=n_days,
        param_version=run_info.get("param_version") or "default",
        status=run_info.get("status", "unknown"),
        notes=run_info.get("notes") or "",
        metrics_block=metrics_block,
        market_ctx=market_ctx,
        regime_ctx=regime_ctx,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    resp = client.chat(messages, temperature=temperature, max_tokens=max_tokens)
    parsed = resp.parse_json()
    return parsed, resp.model, resp.latency_seconds


# ── Assembly ──────────────────────────────────────────────────────────────────

def _assemble_report(
    parsed: dict[str, Any],
    run_info: dict[str, Any],
    pivoted: dict[str, dict[str, Any]],
    model_used: str,
    latency_seconds: float,
) -> ForensicsReport:
    raw_fas = parsed.get("factor_assessments", [])
    factor_assessments: list[FactorAssessment] = []
    for fa in raw_fas:
        factor = fa.get("factor", "unknown")
        m = pivoted.get(factor, {})
        fa_obj = FactorAssessment(
            factor=factor,
            ic_mean=float(m.get("ic") or 0),
            ic_ir=float(m.get("ic_ir") or 0),
            ic_positive_rate=float(m.get("ic_positive_rate") or 0),
            rank_ic_mean=float(m.get("rank_ic") or 0),
            topn_hit_rate=float(m.get("topn_hit") or 0),
            verdict=fa.get("verdict", "marginal"),
            strength=fa.get("strength", "noisy") if fa.get("strength") in FACTOR_STRENGTHS else "noisy",
            regime_sensitivity=fa.get("regime_sensitivity", ""),
            notes=fa.get("notes", ""),
        )
        factor_assessments.append(fa_obj)

    overall = parsed.get("overall_verdict", "acceptable")
    if overall not in OVERALL_VERDICTS:
        overall = "acceptable"

    return ForensicsReport(
        forensics_id=str(uuid.uuid4()),
        backtest_run_id=run_info["backtest_run_id"],
        analysis_date=dt.date.today(),
        factor_assessments=factor_assessments,
        top_findings=parsed.get("top_findings", []),
        regime_attribution=parsed.get("regime_attribution", ""),
        improvement_recs=parsed.get("improvement_recs", []),
        overall_verdict=overall,
        model_used=model_used,
        latency_seconds=latency_seconds,
    )


# ── DB persistence ────────────────────────────────────────────────────────────

def _persist_forensics(engine: Engine, report: ForensicsReport) -> None:
    sql = text(f"""
        INSERT INTO {SCHEMA}.llm_backtest_forensics
            (forensics_id, backtest_run_id, analysis_date,
             factor_assessments, top_findings, regime_attribution,
             improvement_recs, overall_verdict, model_used, latency_seconds)
        VALUES
            (:fid, cast(:rid AS uuid), :ad,
             cast(:fa AS jsonb), cast(:tf AS jsonb), :ra,
             cast(:ir AS jsonb), :ov, :model, :latency)
        ON CONFLICT (backtest_run_id) DO UPDATE SET
            factor_assessments = EXCLUDED.factor_assessments,
            top_findings       = EXCLUDED.top_findings,
            regime_attribution = EXCLUDED.regime_attribution,
            improvement_recs   = EXCLUDED.improvement_recs,
            overall_verdict    = EXCLUDED.overall_verdict,
            model_used         = EXCLUDED.model_used,
            latency_seconds    = EXCLUDED.latency_seconds,
            created_at         = now()
    """)
    fa_json = json.dumps(
        [
            {
                "factor": fa.factor,
                "ic_mean": fa.ic_mean,
                "ic_ir": fa.ic_ir,
                "verdict": fa.verdict,
                "strength": fa.strength,
                "regime_sensitivity": fa.regime_sensitivity,
                "notes": fa.notes,
            }
            for fa in report.factor_assessments
        ],
        ensure_ascii=False,
    )
    with engine.begin() as conn:
        conn.execute(sql, {
            "fid": report.forensics_id,
            "rid": report.backtest_run_id,
            "ad": report.analysis_date,
            "fa": fa_json,
            "tf": json.dumps(report.top_findings, ensure_ascii=False),
            "ra": report.regime_attribution,
            "ir": json.dumps(report.improvement_recs, ensure_ascii=False),
            "ov": report.overall_verdict,
            "model": report.model_used,
            "latency": report.latency_seconds,
        })
    log.info("[backtest_forensics] persisted forensics for run_id=%s (verdict=%s)",
             report.backtest_run_id, report.overall_verdict)


# ── Public entry point ────────────────────────────────────────────────────────

def run_backtest_forensics(
    engine: Engine,
    *,
    backtest_run_id: str,
    persist: bool = True,
    llm_client: LLMClient | None = None,
    on_log: Any = None,
) -> ForensicsReport:
    """Run LLM forensics analysis on a completed backtest.

    Args:
        engine:           SQLAlchemy engine.
        backtest_run_id:  UUID of the backtest run to analyze.
        persist:          Write forensics to DB.
        llm_client:       LLMClient instance; creates from settings if None.
        on_log:           Optional callable(str) for progress logging.

    Returns:
        ForensicsReport dataclass.

    Raises:
        ValueError: If backtest_run_id not found in DB.
    """
    def _emit(msg: str) -> None:
        if on_log:
            on_log(msg)
        log.info(msg)

    _emit(f"[backtest_forensics] loading run {backtest_run_id} …")
    run_info = _load_backtest_run(engine, backtest_run_id)
    if run_info is None:
        raise ValueError(f"backtest_run_id '{backtest_run_id}' not found")

    _emit(f"[backtest_forensics] run: {run_info['start_date']} → {run_info['end_date']} "
          f"status={run_info['status']}")

    metrics_df = _load_metrics(engine, backtest_run_id)
    if metrics_df.empty:
        raise ValueError(f"No metrics found for backtest_run_id '{backtest_run_id}'")

    pivoted = _pivot_metrics(metrics_df)
    _emit(f"[backtest_forensics] {len(pivoted)} factors with metrics")

    start = run_info["start_date"]
    end = run_info["end_date"]
    market_ctx = _load_market_context(engine, start, end)
    regime_ctx = _load_regime_context(engine, start, end)

    _emit("[backtest_forensics] calling LLM for forensics analysis …")
    if llm_client is None:
        llm_client = LLMClient()

    try:
        parsed, model_used, latency = _call_llm(
            run_info, pivoted, market_ctx, regime_ctx,
            client=llm_client,
        )
    except Exception as exc:
        _emit(f"[backtest_forensics] LLM call failed: {exc}")
        raise

    _emit(f"[backtest_forensics] LLM done in {latency:.1f}s (model={model_used})")

    report = _assemble_report(parsed, run_info, pivoted, model_used, latency)

    _emit(f"[backtest_forensics] overall_verdict={report.overall_verdict}")
    for finding in report.top_findings:
        _emit(f"  • {finding}")
    for rec in report.improvement_recs:
        _emit(f"  → {rec}")

    if persist:
        ensure_table(engine)
        _persist_forensics(engine, report)

    return report


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_latest_forensics(engine: Engine) -> dict[str, Any] | None:
    """Return the most recently created forensics report."""
    sql = text(f"""
        SELECT f.forensics_id::text, f.backtest_run_id::text, f.analysis_date,
               f.overall_verdict, f.top_findings, f.improvement_recs,
               f.regime_attribution, f.factor_assessments
        FROM {SCHEMA}.llm_backtest_forensics f
        ORDER BY f.created_at DESC LIMIT 1
    """)
    with engine.connect() as conn:
        row = conn.execute(sql).fetchone()
    if not row:
        return None
    return {
        "forensics_id": row[0],
        "backtest_run_id": row[1],
        "analysis_date": row[2],
        "overall_verdict": row[3],
        "top_findings": row[4],
        "improvement_recs": row[5],
        "regime_attribution": row[6],
        "factor_assessments": row[7],
    }
