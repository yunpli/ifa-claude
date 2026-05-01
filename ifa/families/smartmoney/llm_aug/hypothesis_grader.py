"""SmartMoney LLM Augmentation — B2: Hypothesis Grader.

Grades the SmartMoney evening report's sector hypotheses (from E12 validation
points) against actual market outcomes N days later.

Workflow
--------
1. Load ungraded hypotheses from report_judgments (judgment_type='hypothesis',
   review_status='pending' or NULL, created > N+1 days ago).
2. For each hypothesis, fetch the actual sector price/flow outcome for its
   target on its horizon date.
3. Call LLM to grade each hypothesis: correct / partial / incorrect, with
   explanation and confidence calibration notes.
4. Persist grades to smartmoney.llm_hypothesis_grades.
5. Update report_judgments.review_status to 'graded'.
6. Return summary stats (accuracy, calibration) for monitoring.

DB Table
--------
    smartmoney.llm_hypothesis_grades
    ─────────────────────────────────
    grade_id            UUID PK
    judgment_id         UUID FK → report_judgments.judgment_id
    trade_date          DATE     (hypothesis was made on this date)
    horizon_date        DATE     (the date being predicted)
    target_sector       TEXT
    hypothesis_text     TEXT
    outcome_summary     TEXT     (factual: what actually happened)
    verdict             TEXT     'correct' | 'partial' | 'incorrect' | 'unverifiable'
    verdict_reasoning   TEXT     LLM explanation
    confidence_stated   TEXT     stated confidence from original hypothesis
    confidence_correct  BOOL     was stated confidence directionally right?
    model_used          TEXT
    latency_seconds     FLOAT
    created_at          TIMESTAMPTZ

Usage
-----
    from ifa.families.smartmoney.llm_aug.hypothesis_grader import run_hypothesis_grader

    summary = run_hypothesis_grader(engine, as_of_date=dt.date.today())
    print(summary)
    # {'graded': 6, 'correct': 3, 'partial': 2, 'incorrect': 1, 'accuracy': 0.50}
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

VERDICTS = frozenset({"correct", "partial", "incorrect", "unverifiable"})

# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.llm_hypothesis_grades (
    grade_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    judgment_id         UUID NOT NULL,
    trade_date          DATE NOT NULL,
    horizon_date        DATE,
    target_sector       TEXT,
    hypothesis_text     TEXT NOT NULL,
    outcome_summary     TEXT,
    verdict             TEXT NOT NULL CHECK (verdict IN (
                            'correct', 'partial', 'incorrect', 'unverifiable')),
    verdict_reasoning   TEXT,
    confidence_stated   TEXT,
    confidence_correct  BOOLEAN,
    model_used          TEXT,
    latency_seconds     FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_llm_hypothesis_grades_judgment
    ON {SCHEMA}.llm_hypothesis_grades (judgment_id);
"""


def ensure_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
    log.debug("[hypothesis_grader] table ensured")


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class HypothesisGrade:
    grade_id: str
    judgment_id: str
    trade_date: dt.date
    horizon_date: dt.date | None
    target_sector: str | None
    hypothesis_text: str
    outcome_summary: str
    verdict: str          # correct | partial | incorrect | unverifiable
    verdict_reasoning: str
    confidence_stated: str | None
    confidence_correct: bool | None
    model_used: str
    latency_seconds: float


@dataclass
class GraderSummary:
    graded: int = 0
    correct: int = 0
    partial: int = 0
    incorrect: int = 0
    unverifiable: int = 0
    accuracy: float = 0.0      # correct / (correct + partial + incorrect)
    partial_credit: float = 0.0  # (correct + 0.5*partial) / verifiable

    def to_dict(self) -> dict[str, Any]:
        return {
            "graded": self.graded,
            "correct": self.correct,
            "partial": self.partial,
            "incorrect": self.incorrect,
            "unverifiable": self.unverifiable,
            "accuracy": round(self.accuracy, 3),
            "partial_credit": round(self.partial_credit, 3),
        }


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_pending_hypotheses(
    engine: Engine,
    as_of_date: dt.date,
    min_horizon_days: int,
) -> list[dict[str, Any]]:
    """Load hypotheses whose horizon has passed and haven't been graded yet.

    A hypothesis is "ready to grade" when:
      - created at least min_horizon_days ago (so the forecast window has elapsed)
      - review_status is NULL or 'pending'
      - not already in llm_hypothesis_grades
    """
    cutoff = as_of_date - dt.timedelta(days=min_horizon_days)
    sql = text(f"""
        SELECT
            j.judgment_id::text,
            j.report_run_id::text,
            j.judgment_text,
            j.target,
            j.horizon,
            j.confidence,
            j.validation_method,
            j.created_at::date AS trade_date
        FROM report_judgments j
        WHERE j.judgment_type = 'hypothesis'
          AND (j.review_status IS NULL OR j.review_status = 'pending')
          AND j.created_at::date <= :cutoff
          AND NOT EXISTS (
              SELECT 1 FROM {SCHEMA}.llm_hypothesis_grades g
              WHERE g.judgment_id = j.judgment_id
          )
        ORDER BY j.created_at ASC
        LIMIT 50
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"cutoff": cutoff}).fetchall()
    return [
        {
            "judgment_id": r[0],
            "report_run_id": r[1],
            "hypothesis_text": r[2] or "",
            "target": r[3] or "",
            "horizon": r[4] or "1d",
            "confidence": r[5] or "",
            "validation_method": r[6] or "",
            "trade_date": r[7],
        }
        for r in rows
    ]


def _load_sector_outcome(
    engine: Engine,
    sector_name_hint: str,
    horizon_date: dt.date,
    lookback: int = 3,
) -> str:
    """Fetch actual sector price/flow outcome around the horizon date.

    Returns a concise summary string for the LLM prompt. Best-effort —
    returns 'no data available' if the sector can't be matched.
    """
    try:
        start = horizon_date - dt.timedelta(days=lookback)
        sql = text(f"""
            SELECT fd.trade_date, fd.sector_name,
                   fd.heat_score, fd.trend_score,
                   fd.persistence_score, fd.crowding_score,
                   rsd.pct_change
            FROM {SCHEMA}.factor_daily fd
            LEFT JOIN {SCHEMA}.raw_sw_daily rsd
                ON rsd.trade_date = fd.trade_date
                AND rsd.ts_code = fd.sector_code
            WHERE fd.trade_date BETWEEN :start AND :end
              AND fd.sector_name ILIKE :name
            ORDER BY fd.trade_date
            LIMIT 10
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {
                "start": start,
                "end": horizon_date + dt.timedelta(days=1),
                "name": f"%{sector_name_hint[:6]}%",
            }).fetchall()
        if not rows:
            return "no sector data found for this period"
        lines = []
        for r in rows:
            pct = f"{float(r[6]):.1f}%" if r[6] is not None else "n/a"
            lines.append(
                f"{r[0]}: {r[1]} | heat={float(r[2] or 0):+.2f} "
                f"trend={float(r[3] or 0):+.2f} pct={pct}"
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        log.warning("[hypothesis_grader] outcome load failed for '%s': %s", sector_name_hint, exc)
        return "data load error"


def _parse_horizon_date(trade_date: dt.date, horizon_str: str) -> dt.date | None:
    """Convert horizon string like '1d', '2025-11-02', 'tomorrow' to a date."""
    if not horizon_str:
        return trade_date + dt.timedelta(days=1)
    # ISO date format
    try:
        return dt.date.fromisoformat(horizon_str)
    except ValueError:
        pass
    # Relative: "1d", "5d", "1w"
    horizon_str = horizon_str.lower().strip()
    if horizon_str.endswith("d"):
        try:
            return trade_date + dt.timedelta(days=int(horizon_str[:-1]))
        except ValueError:
            pass
    if horizon_str.endswith("w"):
        try:
            return trade_date + dt.timedelta(weeks=int(horizon_str[:-1]))
        except ValueError:
            pass
    if "tomorrow" in horizon_str or "明日" in horizon_str or "次日" in horizon_str:
        return trade_date + dt.timedelta(days=1)
    # Default: next day
    return trade_date + dt.timedelta(days=1)


# ── Prompt + LLM ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是一位量化策略分析师，负责评估A股板块预测假设的准确性。

对于每个假设，你会收到：
1. 原始假设文本（在预测日生成）
2. 该假设的目标板块和验证方法
3. 实际发生的市场数据

评级规则：
- correct: 假设方向正确，验证条件基本满足
- partial: 部分正确，方向对但幅度或时机偏差较大
- incorrect: 假设方向或主要判断错误
- unverifiable: 由于数据缺失或假设描述模糊，无法判断

输出纯 JSON：
{
  "grades": [
    {
      "hypothesis_index": 0,
      "verdict": "correct" | "partial" | "incorrect" | "unverifiable",
      "verdict_reasoning": "2-3句中文解释，引用具体数据",
      "confidence_correct": true | false | null,
      "outcome_summary": "一句话总结实际发生了什么"
    }
  ]
}
"""

_USER_TEMPLATE = """\
评估日期：{as_of_date}
需要评级的假设（共 {n} 条）：

{hyp_block}

请输出 JSON 评级结果。
"""


def _build_hyp_block(hyps: list[dict[str, Any]], outcomes: list[str]) -> str:
    lines = []
    for i, (h, outcome) in enumerate(zip(hyps, outcomes)):
        lines.append(f"[{i}] 假设（生成于 {h['trade_date']}，验证期 {h['horizon']}）：")
        lines.append(f"    预测：{h['hypothesis_text']}")
        lines.append(f"    目标：{h['target']}  置信度：{h['confidence']}")
        lines.append(f"    验证方法：{h['validation_method']}")
        lines.append(f"    实际结果：")
        for outcome_line in outcome.split("\n"):
            lines.append(f"      {outcome_line}")
        lines.append("")
    return "\n".join(lines)


def _call_llm(
    hyps: list[dict[str, Any]],
    outcomes: list[str],
    as_of_date: dt.date,
    *,
    client: LLMClient,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> tuple[list[dict[str, Any]], str, float]:
    hyp_block = _build_hyp_block(hyps, outcomes)
    user_msg = _USER_TEMPLATE.format(
        as_of_date=as_of_date.isoformat(),
        n=len(hyps),
        hyp_block=hyp_block,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    resp = client.chat(messages, temperature=temperature, max_tokens=max_tokens)
    parsed = resp.parse_json()
    grades_raw = parsed.get("grades", [])
    return grades_raw, resp.model, resp.latency_seconds


# ── Assembly ──────────────────────────────────────────────────────────────────

def _assemble_grades(
    hyps: list[dict[str, Any]],
    grades_raw: list[dict[str, Any]],
    model_used: str,
    latency_seconds: float,
) -> list[HypothesisGrade]:
    grade_by_idx = {g.get("hypothesis_index", i): g for i, g in enumerate(grades_raw)}
    result: list[HypothesisGrade] = []
    for i, h in enumerate(hyps):
        g = grade_by_idx.get(i, {})
        verdict = g.get("verdict", "unverifiable")
        if verdict not in VERDICTS:
            verdict = "unverifiable"
        horizon_date = _parse_horizon_date(h["trade_date"], h.get("horizon", "1d"))
        cc = g.get("confidence_correct")
        if cc is not None:
            cc = bool(cc)
        result.append(HypothesisGrade(
            grade_id=str(uuid.uuid4()),
            judgment_id=h["judgment_id"],
            trade_date=h["trade_date"],
            horizon_date=horizon_date,
            target_sector=h.get("target"),
            hypothesis_text=h["hypothesis_text"],
            outcome_summary=g.get("outcome_summary", ""),
            verdict=verdict,
            verdict_reasoning=g.get("verdict_reasoning", ""),
            confidence_stated=h.get("confidence"),
            confidence_correct=cc,
            model_used=model_used,
            latency_seconds=latency_seconds / max(len(hyps), 1),
        ))
    return result


# ── DB persistence ────────────────────────────────────────────────────────────

def _persist_grades(engine: Engine, grades: list[HypothesisGrade]) -> None:
    if not grades:
        return
    sql_insert = text(f"""
        INSERT INTO {SCHEMA}.llm_hypothesis_grades
            (grade_id, judgment_id, trade_date, horizon_date,
             target_sector, hypothesis_text, outcome_summary,
             verdict, verdict_reasoning, confidence_stated,
             confidence_correct, model_used, latency_seconds)
        VALUES
            (:gid, cast(:jid AS uuid), :td, :hd,
             :tgt, :hyp, :outcome,
             :verdict, :reason, :conf_stated,
             :conf_correct, :model, :latency)
        ON CONFLICT (judgment_id) DO UPDATE SET
            verdict           = EXCLUDED.verdict,
            verdict_reasoning = EXCLUDED.verdict_reasoning,
            outcome_summary   = EXCLUDED.outcome_summary,
            confidence_correct= EXCLUDED.confidence_correct,
            model_used        = EXCLUDED.model_used,
            created_at        = now()
    """)
    sql_update = text("""
        UPDATE report_judgments
        SET review_status = 'graded'
        WHERE judgment_id = cast(:jid AS uuid)
    """)
    rows = [
        {
            "gid": g.grade_id,
            "jid": g.judgment_id,
            "td": g.trade_date,
            "hd": g.horizon_date,
            "tgt": g.target_sector,
            "hyp": g.hypothesis_text,
            "outcome": g.outcome_summary,
            "verdict": g.verdict,
            "reason": g.verdict_reasoning,
            "conf_stated": g.confidence_stated,
            "conf_correct": g.confidence_correct,
            "model": g.model_used,
            "latency": g.latency_seconds,
        }
        for g in grades
    ]
    with engine.begin() as conn:
        conn.execute(sql_insert, rows)
        for g in grades:
            conn.execute(sql_update, {"jid": g.judgment_id})
    log.info("[hypothesis_grader] persisted %d grades", len(grades))


# ── Summary computation ───────────────────────────────────────────────────────

def _compute_summary(grades: list[HypothesisGrade]) -> GraderSummary:
    s = GraderSummary(graded=len(grades))
    for g in grades:
        if g.verdict == "correct":
            s.correct += 1
        elif g.verdict == "partial":
            s.partial += 1
        elif g.verdict == "incorrect":
            s.incorrect += 1
        else:
            s.unverifiable += 1
    verifiable = s.correct + s.partial + s.incorrect
    if verifiable > 0:
        s.accuracy = s.correct / verifiable
        s.partial_credit = (s.correct + 0.5 * s.partial) / verifiable
    return s


# ── Public entry point ────────────────────────────────────────────────────────

def run_hypothesis_grader(
    engine: Engine,
    *,
    as_of_date: dt.date | None = None,
    min_horizon_days: int = 1,
    batch_size: int = 10,
    persist: bool = True,
    llm_client: LLMClient | None = None,
    on_log: Any = None,
) -> GraderSummary:
    """Grade pending hypotheses from evening report E12 sections.

    Args:
        engine:            SQLAlchemy engine.
        as_of_date:        Grade as of this date (default: today).
        min_horizon_days:  Only grade hypotheses at least this many days old.
        batch_size:        Max hypotheses per LLM call (to avoid context overflow).
        persist:           Write grades to DB and update report_judgments.
        llm_client:        LLMClient instance; creates from settings if None.
        on_log:            Optional callable(str) for progress logging.

    Returns:
        GraderSummary with accuracy and counts.
    """
    def _emit(msg: str) -> None:
        if on_log:
            on_log(msg)
        log.info(msg)

    if as_of_date is None:
        as_of_date = dt.date.today()

    _emit(f"[hypothesis_grader] {as_of_date}: loading pending hypotheses …")
    hyps = _load_pending_hypotheses(engine, as_of_date, min_horizon_days)
    if not hyps:
        _emit("[hypothesis_grader] no pending hypotheses to grade")
        return GraderSummary()

    _emit(f"[hypothesis_grader] {len(hyps)} hypotheses to grade")

    if llm_client is None:
        llm_client = LLMClient()

    all_grades: list[HypothesisGrade] = []

    # Process in batches
    for batch_start in range(0, len(hyps), batch_size):
        batch = hyps[batch_start: batch_start + batch_size]
        _emit(f"[hypothesis_grader] grading batch {batch_start // batch_size + 1} "
              f"({len(batch)} hypotheses) …")

        # Fetch outcomes for each hypothesis in batch
        outcomes: list[str] = []
        for h in batch:
            horizon_date = _parse_horizon_date(h["trade_date"], h.get("horizon", "1d"))
            outcome = _load_sector_outcome(engine, h.get("target", ""), horizon_date)
            outcomes.append(outcome)

        try:
            grades_raw, model_used, latency = _call_llm(
                batch, outcomes, as_of_date,
                client=llm_client,
            )
        except Exception as exc:
            _emit(f"[hypothesis_grader] LLM call failed: {exc}")
            raise

        grades = _assemble_grades(batch, grades_raw, model_used, latency)
        all_grades.extend(grades)

        for g in grades:
            _emit(f"  [{g.verdict:13s}] {g.hypothesis_text[:60]}…")

    if persist:
        ensure_table(engine)
        _persist_grades(engine, all_grades)

    summary = _compute_summary(all_grades)
    _emit(
        f"[hypothesis_grader] done: {summary.graded} graded | "
        f"correct={summary.correct} partial={summary.partial} "
        f"incorrect={summary.incorrect} unverifiable={summary.unverifiable} | "
        f"accuracy={summary.accuracy:.1%} partial_credit={summary.partial_credit:.1%}"
    )
    return summary


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_grade_stats(
    engine: Engine,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> dict[str, Any]:
    """Return aggregate grading stats over a date window."""
    conditions = []
    params: dict[str, Any] = {}
    if start:
        conditions.append("trade_date >= :start")
        params["start"] = start
    if end:
        conditions.append("trade_date <= :end")
        params["end"] = end
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = text(f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN verdict = 'correct' THEN 1 ELSE 0 END) AS correct,
            SUM(CASE WHEN verdict = 'partial' THEN 1 ELSE 0 END) AS partial,
            SUM(CASE WHEN verdict = 'incorrect' THEN 1 ELSE 0 END) AS incorrect,
            SUM(CASE WHEN verdict = 'unverifiable' THEN 1 ELSE 0 END) AS unverifiable
        FROM {SCHEMA}.llm_hypothesis_grades
        {where}
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row or not row[0]:
        return {"total": 0, "correct": 0, "partial": 0, "incorrect": 0, "accuracy": None}
    total, correct, partial, incorrect, unverif = [int(v or 0) for v in row]
    verifiable = correct + partial + incorrect
    return {
        "total": total,
        "correct": correct,
        "partial": partial,
        "incorrect": incorrect,
        "unverifiable": unverif,
        "accuracy": round(correct / verifiable, 3) if verifiable else None,
        "partial_credit": round((correct + 0.5 * partial) / verifiable, 3) if verifiable else None,
    }
