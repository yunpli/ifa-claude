"""Position state machine for TA candidates.

Walks candidate → fill → exit using raw_daily OHLC, mirroring how an
institutional trader would actually have managed the position:

  T+1 fill check
    · if raw_daily[T+1].low ≤ entry_price            → 'filled' at entry_price
    · elif raw_daily[T+1].open > entry × (1+premium) → 'unfilled'  (gap-up too far)
    · else                                            → still attempt at open
    · 24h limit on the挂单, else 'expired'

  T+1 .. T+horizon (default 15 trade days) walk
    · low  ≤ stop_loss     → 'stop_hit',   exit at stop_loss
    · high ≥ target_price  → 'target_hit', exit at target_price
    · last day reached     → 'time_exit',  exit at close
    · still open           → 'still_holding'

Outputs realized_return_pct (vs fill_price), days_held, and max drawdown.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

DEFAULT_HORIZON_TRADE_DAYS = 15


@dataclass
class PositionEvent:
    candidate_id: str
    generation_date: date
    ts_code: str
    setup_name: str | None
    entry_price: float
    stop_loss: float
    target_price: float
    fill_status: str                # 'filled' / 'unfilled' / 'expired'
    fill_date: date | None
    fill_price: float | None
    exit_status: str | None         # 'stop_hit' / 'target_hit' / 'time_exit' / 'still_holding'
    exit_date: date | None
    exit_price: float | None
    realized_return_pct: float | None
    max_drawdown_pct: float | None
    days_held: int | None
    # Fixed-horizon close-based returns (no stop/target — used by backtest objective).
    return_t5_pct: float | None = None
    return_t10_pct: float | None = None
    return_t15_pct: float | None = None


def _load_ohlc(engine: Engine, ts_code: str, after: date,
                horizon_days: int = DEFAULT_HORIZON_TRADE_DAYS + 1) -> list[tuple]:
    """Returns list of (trade_date, open, high, low, close) for next ≤horizon trade days."""
    sql = text("""
        SELECT trade_date, open, high, low, close
        FROM smartmoney.raw_daily
        WHERE ts_code = :ts AND trade_date > :d
        ORDER BY trade_date
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "ts": ts_code, "d": after, "limit": horizon_days + 5,
        }).fetchall()
    return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            for r in rows if r[1] and r[2] and r[3] and r[4]]


def evaluate_candidate(
    engine: Engine,
    *,
    candidate_id: str,
    generation_date: date,
    ts_code: str,
    setup_name: str | None,
    entry_price: float,
    stop_loss: float,
    target_price: float,
    horizon: int = DEFAULT_HORIZON_TRADE_DAYS,
    unfilled_premium_pct: float = 0.5,
) -> PositionEvent:
    """Run the state machine for one candidate. Returns a PositionEvent.

    Caller persists via position_repo.upsert_position_events. If insufficient
    forward data exists (fewer than horizon trade days available), exit_status
    is set to 'still_holding' and outcome fields are None.
    """
    rows = _load_ohlc(engine, ts_code, generation_date, horizon + 1)
    if not rows:
        return PositionEvent(
            candidate_id=candidate_id,
            generation_date=generation_date, ts_code=ts_code,
            setup_name=setup_name,
            entry_price=entry_price, stop_loss=stop_loss, target_price=target_price,
            fill_status="expired", fill_date=None, fill_price=None,
            exit_status=None, exit_date=None, exit_price=None,
            realized_return_pct=None, max_drawdown_pct=None, days_held=None,
        )

    # ── Fill check on T+1 ──
    t1_date, t1_open, t1_high, t1_low, t1_close = rows[0]
    fill_status = "unfilled"
    fill_price = None
    fill_date = None

    # If T+1 open already trades through entry → filled at MAX(entry, t1_open)
    # (gap-down opens fill better than entry; gap-up too far → unfilled).
    if t1_open <= entry_price * (1.0 + unfilled_premium_pct / 100.0):
        if t1_low <= entry_price:
            fill_status = "filled"
            fill_price = max(entry_price, t1_open)
            fill_date = t1_date
        elif t1_open <= entry_price:
            # Gap-down filled at open
            fill_status = "filled"
            fill_price = t1_open
            fill_date = t1_date

    if fill_status != "filled":
        return PositionEvent(
            candidate_id=candidate_id,
            generation_date=generation_date, ts_code=ts_code,
            setup_name=setup_name,
            entry_price=entry_price, stop_loss=stop_loss, target_price=target_price,
            fill_status=fill_status, fill_date=None, fill_price=None,
            exit_status=None, exit_date=None, exit_price=None,
            realized_return_pct=None, max_drawdown_pct=None, days_held=None,
        )

    # ── Walk forward looking for stop / target / time-exit ──
    walk_rows = rows[:horizon]
    exit_status = "still_holding"
    exit_date = None
    exit_price = None
    max_drawdown_pct = 0.0

    # Inspect T+1 first (fill day) then T+2..
    # Note: stop/target can hit on the fill day itself (intraday spread).
    for i, (d, op, hi, lo, cl) in enumerate(walk_rows):
        # max drawdown vs fill_price so far (rolling min low / fill - 1)
        dd_today = (lo - fill_price) / fill_price * 100
        if dd_today < max_drawdown_pct:
            max_drawdown_pct = dd_today
        # Stop check first (conservative — prefer stop over target on same bar).
        if lo <= stop_loss:
            exit_status = "stop_hit"
            exit_date = d
            exit_price = stop_loss
            break
        if hi >= target_price:
            exit_status = "target_hit"
            exit_date = d
            exit_price = target_price
            break
    else:
        # Loop ended naturally — no stop/target hit within horizon.
        if len(walk_rows) >= horizon:
            d, op, hi, lo, cl = walk_rows[horizon - 1]
            exit_status = "time_exit"
            exit_date = d
            exit_price = cl
        # Else: fewer than horizon days → still_holding (data not yet ready)

    if exit_status == "still_holding" or exit_price is None:
        return PositionEvent(
            candidate_id=candidate_id,
            generation_date=generation_date, ts_code=ts_code,
            setup_name=setup_name,
            entry_price=entry_price, stop_loss=stop_loss, target_price=target_price,
            fill_status="filled", fill_date=fill_date, fill_price=fill_price,
            exit_status="still_holding", exit_date=None, exit_price=None,
            realized_return_pct=None,
            max_drawdown_pct=round(max_drawdown_pct, 4) if max_drawdown_pct < 0 else 0.0,
            days_held=len(walk_rows),
        )

    realized = (exit_price - fill_price) / fill_price * 100
    days_held = (exit_date - fill_date).days if (exit_date and fill_date) else None

    # Fixed-horizon returns (close at T+5/T+10/T+15 vs fill_price; no stop/target).
    # Indices: rows[0] = T+1, so T+N = rows[N-1] when len(rows) >= N.
    def _ret_at(n: int) -> float | None:
        if len(rows) < n:
            return None
        close_at = rows[n - 1][4]
        return round((close_at - fill_price) / fill_price * 100, 4)

    return PositionEvent(
        candidate_id=candidate_id,
        generation_date=generation_date, ts_code=ts_code,
        setup_name=setup_name,
        entry_price=entry_price, stop_loss=stop_loss, target_price=target_price,
        fill_status="filled", fill_date=fill_date, fill_price=round(fill_price, 4),
        exit_status=exit_status, exit_date=exit_date, exit_price=round(exit_price, 4),
        realized_return_pct=round(realized, 4),
        max_drawdown_pct=round(max_drawdown_pct, 4),
        days_held=days_held,
        return_t5_pct=_ret_at(5),
        return_t10_pct=_ret_at(10),
        return_t15_pct=_ret_at(15),
    )


_UPSERT = text("""
    INSERT INTO ta.position_events_daily
        (candidate_id, generation_date, ts_code, setup_name,
         entry_price, stop_loss, target_price,
         fill_status, fill_date, fill_price,
         exit_status, exit_date, exit_price,
         realized_return_pct, max_drawdown_pct, days_held,
         return_t5_pct, return_t10_pct, return_t15_pct)
    VALUES
        (:candidate_id, :generation_date, :ts_code, :setup_name,
         :entry_price, :stop_loss, :target_price,
         :fill_status, :fill_date, :fill_price,
         :exit_status, :exit_date, :exit_price,
         :realized_return_pct, :max_drawdown_pct, :days_held,
         :return_t5_pct, :return_t10_pct, :return_t15_pct)
    ON CONFLICT (candidate_id) DO UPDATE SET
        fill_status = EXCLUDED.fill_status,
        fill_date = EXCLUDED.fill_date,
        fill_price = EXCLUDED.fill_price,
        exit_status = EXCLUDED.exit_status,
        exit_date = EXCLUDED.exit_date,
        exit_price = EXCLUDED.exit_price,
        realized_return_pct = EXCLUDED.realized_return_pct,
        max_drawdown_pct = EXCLUDED.max_drawdown_pct,
        days_held = EXCLUDED.days_held,
        return_t5_pct = EXCLUDED.return_t5_pct,
        return_t10_pct = EXCLUDED.return_t10_pct,
        return_t15_pct = EXCLUDED.return_t15_pct,
        evaluated_at = NOW()
""")


def evaluate_for_date(
    engine: Engine, generation_date: date, *,
    horizon: int = DEFAULT_HORIZON_TRADE_DAYS,
) -> int:
    """Evaluate all candidates generated on `generation_date`. Returns row count."""
    sql = text("""
        SELECT candidate_id, ts_code, setup_name, entry_price, stop_loss, target_price
        FROM ta.candidates_daily
        WHERE trade_date = :d AND entry_price IS NOT NULL AND in_top_watchlist
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"d": generation_date}).fetchall()

    n = 0
    with engine.begin() as conn:
        for r in rows:
            ev = evaluate_candidate(
                engine,
                candidate_id=str(r[0]),
                generation_date=generation_date,
                ts_code=r[1],
                setup_name=r[2],
                entry_price=float(r[3]),
                stop_loss=float(r[4]) if r[4] else 0.0,
                target_price=float(r[5]) if r[5] else 0.0,
                horizon=horizon,
            )
            conn.execute(_UPSERT, {
                "candidate_id": ev.candidate_id,
                "generation_date": ev.generation_date,
                "ts_code": ev.ts_code,
                "setup_name": ev.setup_name,
                "entry_price": ev.entry_price,
                "stop_loss": ev.stop_loss,
                "target_price": ev.target_price,
                "fill_status": ev.fill_status,
                "fill_date": ev.fill_date,
                "fill_price": ev.fill_price,
                "exit_status": ev.exit_status,
                "exit_date": ev.exit_date,
                "exit_price": ev.exit_price,
                "realized_return_pct": ev.realized_return_pct,
                "max_drawdown_pct": ev.max_drawdown_pct,
                "days_held": ev.days_held,
                "return_t5_pct": ev.return_t5_pct,
                "return_t10_pct": ev.return_t10_pct,
                "return_t15_pct": ev.return_t15_pct,
            })
            n += 1
    log.info("evaluate_for_date(%s): wrote %d position events", generation_date, n)
    return n
