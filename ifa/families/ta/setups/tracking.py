"""Candidate outcome tracking — evaluate candidates at fixed horizons.

For each candidate generated on `start_date`, look forward N trade days, compute:
  · return_pct       — close[eval_date] / close[start_date] - 1
  · max_return_pct   — max(high) over (start_date, eval_date]
  · max_drawdown_pct — min(low)  over (start_date, eval_date]
  · validation_status — coarse outcome bucket

Validation buckets (current candidate-class agnostic — simple rules):
  · confirmed     return_pct >= 5%
  · partial       2% <= return_pct < 5%
  · invalidated   return_pct <= -3%
  · timeout       -3% < return_pct < 2%
  · pending       eval_date in future (not yet computable)

Note: some setups (e.g. C2) are warning/sell signals; for those an inverse
mapping makes sense. We keep one rule for now and let M6.5 specialize.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.calendar import trading_days_between

log = logging.getLogger(__name__)


@dataclass
class TrackingResult:
    candidate_id: str
    horizon_days: int
    eval_date: date
    return_pct: float | None
    max_return_pct: float | None
    max_drawdown_pct: float | None
    validation_status: str          # confirmed/partial/invalidated/timeout/pending
    evidence: dict


def _classify(return_pct: float) -> str:
    if return_pct >= 5.0:
        return "confirmed"
    if return_pct >= 2.0:
        return "partial"
    if return_pct <= -3.0:
        return "invalidated"
    return "timeout"


def evaluate_for_date(
    engine: Engine,
    start_date: date,
    *,
    horizon_days: int,
) -> int:
    """Evaluate every candidate from `start_date` at horizon `horizon_days`.

    Returns the number of tracking rows written. Skips candidates whose
    eval_date is beyond available raw_daily data (rows stay un-tracked
    until next run).
    """
    with engine.connect() as conn:
        candidates = conn.execute(
            text("""
                SELECT candidate_id, ts_code
                FROM ta.candidates_daily
                WHERE trade_date = :sd
            """),
            {"sd": start_date},
        ).fetchall()
    if not candidates:
        log.info("no candidates on %s", start_date)
        return 0

    # Eval date = h-th trading day strictly after start_date, per smartmoney.trade_cal.
    # Look up to ~horizon * 2 calendar days forward to absorb weekends + holidays.
    from datetime import timedelta
    window_end = start_date + timedelta(days=horizon_days * 2 + 14)
    forward_days = trading_days_between(engine, start_date, window_end)
    forward_days = [d for d in forward_days if d > start_date]
    if len(forward_days) < horizon_days:
        log.info("only %d trade days available after %s; need %d — refresh trade_cal "
                 "or wait for raw_daily to load",
                 len(forward_days), start_date, horizon_days)
        return 0

    eval_date = forward_days[horizon_days - 1]

    # Per-stock prices: entry close (start_date), exit close (eval_date),
    # max_high and min_low between (start_date, eval_date].
    sql_prices = text("""
        WITH entry AS (
            SELECT ts_code, close AS entry_close
            FROM smartmoney.raw_daily
            WHERE trade_date = :sd
        ),
        exit_ AS (
            SELECT ts_code, close AS exit_close
            FROM smartmoney.raw_daily
            WHERE trade_date = :ed
        ),
        window_ AS (
            SELECT ts_code, MAX(high) AS max_high, MIN(low) AS min_low
            FROM smartmoney.raw_daily
            WHERE trade_date > :sd AND trade_date <= :ed
            GROUP BY ts_code
        )
        SELECT e.ts_code, e.entry_close, x.exit_close, w.max_high, w.min_low
        FROM entry e
        JOIN exit_ x ON x.ts_code = e.ts_code
        LEFT JOIN window_ w ON w.ts_code = e.ts_code
    """)
    with engine.connect() as conn:
        prices = {
            r[0]: (r[1], r[2], r[3], r[4])
            for r in conn.execute(sql_prices, {"sd": start_date, "ed": eval_date})
        }

    n_written = 0
    sql_upsert = text("""
        INSERT INTO ta.candidate_tracking
            (candidate_id, horizon_days, eval_date,
             return_pct, max_return_pct, max_drawdown_pct,
             validation_status, confirmation_evidence)
        VALUES
            (:candidate_id, :h, :ed, :ret, :mret, :mdd, :status, :ev)
        ON CONFLICT (candidate_id, horizon_days) DO UPDATE SET
            eval_date = EXCLUDED.eval_date,
            return_pct = EXCLUDED.return_pct,
            max_return_pct = EXCLUDED.max_return_pct,
            max_drawdown_pct = EXCLUDED.max_drawdown_pct,
            validation_status = EXCLUDED.validation_status,
            confirmation_evidence = EXCLUDED.confirmation_evidence
    """)
    with engine.begin() as conn:
        for cid, ts_code in candidates:
            row = prices.get(ts_code)
            if not row or row[0] is None or row[1] is None:
                continue
            entry, exit_, mx_high, mn_low = row
            entry = float(entry)
            exit_ = float(exit_)
            if entry == 0:
                continue
            ret_pct = (exit_ / entry - 1.0) * 100
            mret_pct = ((float(mx_high) / entry - 1.0) * 100) if mx_high is not None else None
            mdd_pct = ((float(mn_low) / entry - 1.0) * 100) if mn_low is not None else None
            status = _classify(ret_pct)
            evidence = {
                "entry_close": entry,
                "exit_close": exit_,
                "ret_pct": ret_pct,
                "max_return_pct": mret_pct,
                "max_drawdown_pct": mdd_pct,
                "horizon_days": horizon_days,
            }
            conn.execute(sql_upsert, {
                "candidate_id": cid,
                "h": horizon_days,
                "ed": eval_date,
                "ret": ret_pct,
                "mret": mret_pct,
                "mdd": mdd_pct,
                "status": status,
                "ev": json.dumps(evidence, ensure_ascii=False, default=str),
            })
            n_written += 1
    log.info("tracked %d candidates from %s at h=%d (eval_date=%s)",
             n_written, start_date, horizon_days, eval_date)
    return n_written
