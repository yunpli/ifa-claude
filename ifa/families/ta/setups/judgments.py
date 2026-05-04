"""ta.report_judgments — record + auto-evaluate falsifiable next-day hypotheses.

Pipeline:
  1. record_judgment(...)        — when generating evening report §14
  2. evaluate_judgments(on_date) — next trade day, score review_status

Judgment types we currently auto-evaluate:
  · "stock_up"     statement targets a ts_code; passes if T+1 return >= threshold
  · "sector_up"    target = SW L2 code; passes if L2 daily return >= threshold
  · "regime_hold"  target = regime name; passes if next day's regime equals it
"""
from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.calendar import next_trading_day

log = logging.getLogger(__name__)


def record_judgment(
    engine: Engine,
    *,
    judgment_type: str,
    statement: str,
    target: str,
    horizon_days: int,
    validation_rule: dict,
    report_run_id: str | None = None,
) -> str:
    """Insert a falsifiable judgment; return the new judgment_id (UUID str)."""
    sql = text("""
        INSERT INTO ta.report_judgments
            (report_run_id, judgment_type, statement, target,
             horizon_days, validation_rule_json, review_status)
        VALUES
            (:run_id, :jt, :stmt, :tgt, :h, :rule, 'pending')
        RETURNING judgment_id
    """)
    with engine.begin() as conn:
        row = conn.execute(sql, {
            "run_id": report_run_id,
            "jt": judgment_type,
            "stmt": statement,
            "tgt": target,
            "h": horizon_days,
            "rule": json.dumps(validation_rule, ensure_ascii=False),
        }).fetchone()
    return str(row[0])


def evaluate_judgments(engine: Engine, judgment_date: date) -> int:
    """Evaluate every pending judgment created on `judgment_date` against
    realized data at judgment_date + horizon_days. Returns # evaluated."""
    sql_pending = text("""
        SELECT judgment_id, judgment_type, target, horizon_days, validation_rule_json
        FROM ta.report_judgments
        WHERE review_status = 'pending'
          AND reviewed_at IS NULL
          AND DATE(NOW() AT TIME ZONE 'Asia/Shanghai') >= :d
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql_pending, {"d": judgment_date}).fetchall()
    if not rows:
        return 0

    n_evaluated = 0
    for jid, jtype, target, h, rule in rows:
        try:
            eval_date = next_trading_day(engine, judgment_date)
        except RuntimeError:
            continue
        if h and h > 1:
            # walk forward h days
            cur = judgment_date
            for _ in range(h):
                try:
                    cur = next_trading_day(engine, cur)
                except RuntimeError:
                    cur = None
                    break
            if cur is None:
                continue
            eval_date = cur

        rule_dict = rule if isinstance(rule, dict) else (json.loads(rule) if rule else {})
        threshold = float(rule_dict.get("threshold_pct", 0.0))

        passed: bool | None = None
        evidence: dict = {"eval_date": eval_date.isoformat()}

        if jtype == "stock_up":
            with engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT close, pre_close FROM smartmoney.raw_daily
                    WHERE ts_code = :tc AND trade_date = :d
                """), {"tc": target, "d": eval_date}).fetchone()
            if row and row[0] and row[1]:
                ret_pct = (float(row[0]) / float(row[1]) - 1) * 100
                evidence["return_pct"] = ret_pct
                passed = ret_pct >= threshold
        elif jtype == "sector_up":
            with engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT pct_change FROM smartmoney.raw_sw_daily
                    WHERE ts_code = :tc AND trade_date = :d
                """), {"tc": target, "d": eval_date}).fetchone()
            if row and row[0] is not None:
                ret_pct = float(row[0])
                evidence["pct_change"] = ret_pct
                passed = ret_pct >= threshold
        elif jtype == "regime_hold":
            with engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT regime FROM ta.regime_daily WHERE trade_date = :d
                """), {"d": eval_date}).fetchone()
            if row:
                evidence["regime_observed"] = row[0]
                passed = (row[0] == target)
        else:
            log.warning("unknown judgment_type %s for %s", jtype, jid)
            continue

        if passed is None:
            continue

        review_status = "confirmed" if passed else "invalidated"
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE ta.report_judgments
                SET review_status = :s, reviewed_at = NOW(), review_evidence = :e
                WHERE judgment_id = :jid
            """), {"s": review_status, "e": json.dumps(evidence, ensure_ascii=False),
                   "jid": jid})
        n_evaluated += 1

    return n_evaluated
