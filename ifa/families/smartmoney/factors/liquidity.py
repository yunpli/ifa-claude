"""市场资金水位计算 → market_state_daily.

Input raw tables:
  smartmoney.raw_daily       — 全市场日线 (amount, pct_chg)
  smartmoney.raw_limit_list_d — 涨跌停明细 (limit_, open_times, limit_times)
  smartmoney.raw_kpl_list    — 开盘啦榜单 (lu_desc, status)

Output:
  MarketStateSnapshot dataclass (plain Python; no DB side effects).
  write_market_state()  — upsert into smartmoney.market_state_daily.

Market state classification (rule-based, thresholds in params/default.yaml):
  进攻 — 放量 + 普涨 + 连板高度高 + 炸板率低
  中性 — 量能平稳 + 涨跌均衡 + 无极端结构
  防守 — 缩量 + 跌多涨少 + 连板低迷
  退潮 — 大幅缩量 + 跌多炸板 + 资金撤离
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .common import load_scalar_series, percentile_rank, rolling_mean

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"


# ── Data snapshot ─────────────────────────────────────────────────────────────

@dataclass
class MarketStateSnapshot:
    trade_date: dt.date

    # Amount
    total_amount: float          # 全市场总成交额（万元）
    amount_10d_avg: float        # 10日均值
    amount_percentile_60d: float # 在60日历史中的分位 (0–1)

    # Breadth
    up_count: int                # 上涨家数
    down_count: int              # 下跌家数
    flat_count: int              # 平盘家数

    # Limit structure
    limit_up_count: int          # 涨停数
    limit_down_count: int        # 跌停数
    max_consecutive_limit_up: int  # 最高连板高度（来自 kpl_list）
    blow_up_count: int           # 炸板数（曾涨停后打开）
    blow_up_rate: float          # 炸板率 = blow_up_count / limit_up_count

    # Classification
    market_state: str            # 进攻 / 中性 / 防守 / 退潮

    # Derived extras stored in JSON
    derived: dict[str, Any] = field(default_factory=dict)


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_amount_history(engine: Engine, trade_date: dt.date, n_days: int) -> list[float]:
    """Load total daily amount for the last n_days from raw_daily, returned as 万元.

    NOTE: raw_daily.amount is in 千元 per TuShare convention; we divide by 10 here
    so that all downstream values (MarketStateSnapshot.total_amount, amount_10d_avg,
    and the persisted market_state_daily rows) are in 万元 as documented on the
    MarketStateSnapshot dataclass. Previously this returned 千元, producing a 10x
    error in heat/attack/defense thresholds and in any displayed totals.
    """
    sql = f"""
        SELECT trade_date, SUM(amount) / 10.0 AS total_amount_wan
        FROM {SCHEMA}.raw_daily
        WHERE trade_date <= :d
        GROUP BY trade_date
        ORDER BY trade_date ASC
        LIMIT :n
    """
    # We want a slightly larger window to guarantee n_days after trimming
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "n": n_days + 5}).fetchall()
    return [float(r[1]) for r in rows if r[1] is not None]


def _load_breadth(engine: Engine, trade_date: dt.date) -> dict[str, int]:
    """Count up/down/flat stocks for a given trade date from raw_daily."""
    sql = f"""
        SELECT
            COUNT(*) FILTER (WHERE pct_chg >  0.1)  AS up_count,
            COUNT(*) FILTER (WHERE pct_chg < -0.1)  AS down_count,
            COUNT(*) FILTER (WHERE pct_chg BETWEEN -0.1 AND 0.1) AS flat_count
        FROM {SCHEMA}.raw_daily
        WHERE trade_date = :d
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"d": trade_date}).fetchone()
    if row is None:
        return {"up_count": 0, "down_count": 0, "flat_count": 0}
    return {
        "up_count": int(row[0] or 0),
        "down_count": int(row[1] or 0),
        "flat_count": int(row[2] or 0),
    }


def _load_limit_stats(engine: Engine, trade_date: dt.date) -> dict[str, int]:
    """Derive limit-up/down counts and blow-up count from raw_limit_list_d."""
    sql = f"""
        SELECT
            COUNT(*) FILTER (WHERE limit_ = 'U')                            AS limit_up_count,
            COUNT(*) FILTER (WHERE limit_ = 'D')                            AS limit_down_count,
            COUNT(*) FILTER (WHERE limit_ = 'U' AND open_times > 0)        AS blow_up_count
        FROM {SCHEMA}.raw_limit_list_d
        WHERE trade_date = :d
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"d": trade_date}).fetchone()
    if row is None:
        return {"limit_up_count": 0, "limit_down_count": 0, "blow_up_count": 0}
    return {
        "limit_up_count": int(row[0] or 0),
        "limit_down_count": int(row[1] or 0),
        "blow_up_count": int(row[2] or 0),
    }


def _load_max_consecutive(engine: Engine, trade_date: dt.date) -> int:
    """Derive max consecutive limit-up (连板高度) from raw_kpl_list.

    raw_kpl_list.lu_desc values: '首板', '2连板', '3连板', '4天2板', '一字板', etc.
    We parse numeric prefix from strings like '3连板' → 3.
    """
    sql = f"""
        SELECT lu_desc
        FROM {SCHEMA}.raw_kpl_list
        WHERE trade_date = :d AND lu_desc IS NOT NULL
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date}).fetchall()

    max_consec = 1  # 首板 is 1 by definition
    for (lu_desc,) in rows:
        if not lu_desc:
            continue
        # Extract leading integer from "N连板"
        stripped = lu_desc.strip()
        if "连板" in stripped:
            try:
                n = int(stripped.split("连板")[0])
                max_consec = max(max_consec, n)
            except ValueError:
                pass
        elif stripped == "首板" or stripped == "一字板":
            max_consec = max(max_consec, 1)
    return max_consec


# ── Classification ────────────────────────────────────────────────────────────

def _classify_market_state(
    *,
    amount_percentile_60d: float,
    amount_ratio_10d: float,      # today / 10d avg
    up_ratio: float,              # up_count / total_count
    limit_up_count: int,
    limit_down_count: int,
    max_consecutive_limit_up: int,
    blow_up_rate: float,
    params: dict[str, Any],
) -> str:
    """Rule-based market state classification.

    Rules are evaluated in priority order; first match wins.
    Thresholds come from params dict (see params/default.yaml).
    """
    p = params.get("market_state", {})
    attack = p.get("attack", {})
    retreat = p.get("retreat", {})
    defense = p.get("defense", {})

    def _clip01(x: float) -> float:
        return max(0.0, min(1.0, x))

    # ── Continuous scoring (each component returns 0..1 strength) ────────────
    # Higher value = stronger signal in that direction.

    # 进攻 (attack): 量能放大 + 普涨 + 涨停高度 + 炸板低
    attack_score = (
        # 量能分位（high = stronger attack）：0.65→0, 1.0→2.0
        2.0 * _clip01((amount_percentile_60d - attack.get("amount_pct_min", 0.65))
                       / (1.0 - attack.get("amount_pct_min", 0.65)))
        + 1.0 * _clip01((amount_ratio_10d - attack.get("amount_ratio_min", 1.10)) / 0.40)
        + 1.0 * _clip01((up_ratio - attack.get("up_ratio_min", 0.55))
                         / (1.0 - attack.get("up_ratio_min", 0.55)))
        + 1.0 * _clip01((limit_up_count - attack.get("limit_up_min", 20)) / 80.0)
        + 1.0 * _clip01((max_consecutive_limit_up - attack.get("consec_min", 3)) / 7.0)
        + 1.0 * _clip01((attack.get("blow_up_rate_max", 0.35) - blow_up_rate) / 0.35)
    )

    # 退潮 (retreat): 缩量 + 炸板多 + 跌多
    retreat_score = (
        2.0 * _clip01((retreat.get("amount_pct_max", 0.20) - amount_percentile_60d)
                       / retreat.get("amount_pct_max", 0.20))
        + 2.0 * _clip01((blow_up_rate - retreat.get("blow_up_rate_min", 0.50)) / 0.50)
        + 1.0 * _clip01(((1 - retreat.get("down_ratio_min", 0.55)) - up_ratio)
                         / (1 - retreat.get("down_ratio_min", 0.55)))
        + 1.0 * (1.0 if limit_down_count > limit_up_count else 0.0)
    )

    # 防守 (defense): 中等缩量 + 跌略多 + 涨停弱
    defense_score = (
        1.0 * _clip01((defense.get("amount_pct_max", 0.35) - amount_percentile_60d)
                       / defense.get("amount_pct_max", 0.35))
        + 1.0 * _clip01(((1 - defense.get("down_ratio_min", 0.50)) - up_ratio)
                         / (1 - defense.get("down_ratio_min", 0.50)))
        + 1.0 * (1.0 if limit_down_count > limit_up_count * 0.8 else 0.0)
        + 1.0 * (1.0 if max_consecutive_limit_up <= 1 else 0.0)
    )

    # Threshold-based final classification — but THRESHOLDS now compare against
    # continuous accumulated scores, so a weak attack (3.5) won't trigger 进攻
    # while a borderline strong (4.8) might.
    if attack_score >= attack.get("min_score", 5):
        return "进攻"
    if retreat_score >= retreat.get("min_score", 4):
        return "退潮"
    if defense_score >= defense.get("min_score", 3):
        return "防守"
    return "中性"


# ── Main public function ──────────────────────────────────────────────────────

def compute_market_state(
    engine: Engine,
    trade_date: dt.date,
    *,
    params: dict[str, Any],
) -> MarketStateSnapshot:
    """Compute all market water-level metrics for one trade date.

    Reads from raw_daily, raw_limit_list_d, raw_kpl_list.
    Returns a MarketStateSnapshot (no DB write; caller decides when to persist).
    """
    liquidity_params = params.get("liquidity", {})
    history_days = int(liquidity_params.get("amount_history_days", 60))
    short_avg_days = int(liquidity_params.get("amount_short_avg_days", 10))

    # Load rolling amount history (sorted ascending, last value = today)
    amount_history = _load_amount_history(engine, trade_date, history_days)
    if not amount_history:
        log.warning("[liquidity] No raw_daily data for %s; returning empty snapshot", trade_date)
        return MarketStateSnapshot(
            trade_date=trade_date,
            total_amount=0.0, amount_10d_avg=0.0, amount_percentile_60d=0.5,
            up_count=0, down_count=0, flat_count=0,
            limit_up_count=0, limit_down_count=0,
            max_consecutive_limit_up=0, blow_up_count=0, blow_up_rate=0.0,
            market_state="中性",
        )

    total_amount = amount_history[-1]
    history_excl_today = amount_history[:-1]
    amount_10d_avg = rolling_mean(history_excl_today, short_avg_days) if history_excl_today else total_amount
    amount_pct = percentile_rank(history_excl_today or [total_amount], total_amount)
    amount_ratio_10d = (total_amount / amount_10d_avg) if amount_10d_avg > 0 else 1.0

    # Breadth
    breadth = _load_breadth(engine, trade_date)
    total_count = breadth["up_count"] + breadth["down_count"] + breadth["flat_count"]
    up_ratio = breadth["up_count"] / total_count if total_count > 0 else 0.5

    # Limit structure
    limits = _load_limit_stats(engine, trade_date)
    blow_up_rate = (
        limits["blow_up_count"] / limits["limit_up_count"]
        if limits["limit_up_count"] > 0
        else 0.0
    )

    # Consecutive limit-up height
    max_consec = _load_max_consecutive(engine, trade_date)

    market_state = _classify_market_state(
        amount_percentile_60d=amount_pct,
        amount_ratio_10d=amount_ratio_10d,
        up_ratio=up_ratio,
        limit_up_count=limits["limit_up_count"],
        limit_down_count=limits["limit_down_count"],
        max_consecutive_limit_up=max_consec,
        blow_up_rate=blow_up_rate,
        params=liquidity_params,
    )

    derived = {
        "amount_ratio_10d": round(amount_ratio_10d, 4),
        "up_ratio": round(up_ratio, 4),
        "total_stock_count": total_count,
    }

    return MarketStateSnapshot(
        trade_date=trade_date,
        total_amount=round(total_amount, 2),
        amount_10d_avg=round(amount_10d_avg, 2),
        amount_percentile_60d=round(amount_pct, 4),
        up_count=breadth["up_count"],
        down_count=breadth["down_count"],
        flat_count=breadth["flat_count"],
        limit_up_count=limits["limit_up_count"],
        limit_down_count=limits["limit_down_count"],
        max_consecutive_limit_up=max_consec,
        blow_up_count=limits["blow_up_count"],
        blow_up_rate=round(blow_up_rate, 4),
        market_state=market_state,
        derived=derived,
    )


def write_market_state(engine: Engine, snap: MarketStateSnapshot) -> None:
    """Upsert a MarketStateSnapshot into smartmoney.market_state_daily."""
    import json

    sql = text(f"""
        INSERT INTO {SCHEMA}.market_state_daily (
            trade_date, total_amount, amount_10d_avg, amount_percentile_60d,
            up_count, down_count, flat_count,
            limit_up_count, limit_down_count, max_consecutive_limit_up,
            blow_up_count, blow_up_rate, market_state,
            derived_json, computed_at
        ) VALUES (
            :trade_date, :total_amount, :amount_10d_avg, :amount_percentile_60d,
            :up_count, :down_count, :flat_count,
            :limit_up_count, :limit_down_count, :max_consecutive_limit_up,
            :blow_up_count, :blow_up_rate, :market_state,
            :derived_json, now()
        )
        ON CONFLICT (trade_date) DO UPDATE SET
            total_amount            = EXCLUDED.total_amount,
            amount_10d_avg          = EXCLUDED.amount_10d_avg,
            amount_percentile_60d   = EXCLUDED.amount_percentile_60d,
            up_count                = EXCLUDED.up_count,
            down_count              = EXCLUDED.down_count,
            flat_count              = EXCLUDED.flat_count,
            limit_up_count          = EXCLUDED.limit_up_count,
            limit_down_count        = EXCLUDED.limit_down_count,
            max_consecutive_limit_up = EXCLUDED.max_consecutive_limit_up,
            blow_up_count           = EXCLUDED.blow_up_count,
            blow_up_rate            = EXCLUDED.blow_up_rate,
            market_state            = EXCLUDED.market_state,
            derived_json            = EXCLUDED.derived_json,
            computed_at             = now()
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "trade_date": snap.trade_date,
            "total_amount": snap.total_amount,
            "amount_10d_avg": snap.amount_10d_avg,
            "amount_percentile_60d": snap.amount_percentile_60d,
            "up_count": snap.up_count,
            "down_count": snap.down_count,
            "flat_count": snap.flat_count,
            "limit_up_count": snap.limit_up_count,
            "limit_down_count": snap.limit_down_count,
            "max_consecutive_limit_up": snap.max_consecutive_limit_up,
            "blow_up_count": snap.blow_up_count,
            "blow_up_rate": snap.blow_up_rate,
            "market_state": snap.market_state,
            "derived_json": json.dumps(snap.derived, ensure_ascii=False),
        })
    log.info("[liquidity] %s → market_state=%s  amount=%.0f (pct=%.2f)",
             snap.trade_date, snap.market_state, snap.total_amount, snap.amount_percentile_60d)
