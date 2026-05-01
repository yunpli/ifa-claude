"""SmartMoney report data loaders.

Each function returns a typed dataclass with the data for one report section.
No LLM calls here — purely DB reads.  All functions are read-only and safe to
call from a transaction-less context.

Source priority for sector views:
  - DC concepts/industries  (richest moneyflow data)
  - SW industries           (industry skeleton)
  - KPL concepts            (limit-up tracking)
  - THS                     (cross-check)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class MarketPulse:
    """S2 — Market water level snapshot."""
    trade_date: dt.date | None = None
    total_amount: float = 0.0
    amount_10d_avg: float = 0.0
    amount_percentile_60d: float = 0.5
    amount_ratio_10d: float = 1.0
    up_count: int = 0
    down_count: int = 0
    flat_count: int = 0
    limit_up_count: int = 0
    limit_down_count: int = 0
    max_consecutive_limit_up: int = 0
    blow_up_count: int = 0
    blow_up_rate: float = 0.0
    market_state: str = "中性"
    derived: dict[str, Any] = field(default_factory=dict)


@dataclass
class SectorFlowRow:
    """One row in the flow table (in or out)."""
    sector_code: str
    sector_source: str
    sector_name: str
    pct_change: float | None
    net_amount: float | None       # 万元
    net_amount_rate: float | None  # %
    elg_buy_rate: float | None     # 超大单占比
    role: str | None
    cycle_phase: str | None


@dataclass
class CycleGridRow:
    sector_code: str
    sector_source: str
    sector_name: str
    role: str
    cycle_phase: str
    role_confidence: str | None
    phase_confidence: str | None
    heat_score: float | None
    persistence_score: float | None
    crowding_score: float | None


@dataclass
class TomorrowTarget:
    """A sector picked as a tomorrow-watch candidate."""
    sector_code: str
    sector_source: str
    sector_name: str
    role: str
    cycle_phase: str
    heat_score: float | None
    trend_score: float | None
    persistence_score: float | None
    crowding_score: float | None
    leaders: list[dict[str, Any]] = field(default_factory=list)  # [{ts_code,name,role,score}]


@dataclass
class SectorStructureRow:
    """One sector's leader/core/vanguard structure."""
    sector_code: str
    sector_source: str
    sector_name: str
    role: str
    cycle_phase: str
    leader: dict[str, Any] | None = None       # {ts_code, name, score, lu_desc}
    core_troops: list[dict[str, Any]] = field(default_factory=list)
    vanguard: dict[str, Any] | None = None


@dataclass
class CandidateStock:
    ts_code: str
    name: str | None
    role: str                           # 补涨 / 趋势
    score: float
    primary_sector_name: str | None
    theme: str | None
    pct_chg_today: float | None
    evidence: dict[str, Any] = field(default_factory=dict)


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_market_pulse(engine: Engine, trade_date: dt.date) -> MarketPulse:
    sql = text(f"""
        SELECT trade_date, total_amount, amount_10d_avg, amount_percentile_60d,
               up_count, down_count, flat_count,
               limit_up_count, limit_down_count, max_consecutive_limit_up,
               blow_up_count, blow_up_rate, market_state, derived_json
        FROM {SCHEMA}.market_state_daily
        WHERE trade_date = :d
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"d": trade_date}).fetchone()

    if row is None:
        log.warning("[data] no market_state_daily for %s", trade_date)
        return MarketPulse(trade_date=trade_date)

    derived = row[13] if isinstance(row[13], dict) else (json.loads(row[13]) if row[13] else {})
    return MarketPulse(
        trade_date=row[0],
        total_amount=float(row[1] or 0),
        amount_10d_avg=float(row[2] or 0),
        amount_percentile_60d=float(row[3] or 0.5),
        amount_ratio_10d=float(derived.get("amount_ratio_10d", 1.0)),
        up_count=int(row[4] or 0),
        down_count=int(row[5] or 0),
        flat_count=int(row[6] or 0),
        limit_up_count=int(row[7] or 0),
        limit_down_count=int(row[8] or 0),
        max_consecutive_limit_up=int(row[9] or 0),
        blow_up_count=int(row[10] or 0),
        blow_up_rate=float(row[11] or 0),
        market_state=row[12] or "中性",
        derived=derived,
    )


def load_sector_flows(
    engine: Engine,
    trade_date: dt.date,
    *,
    direction: str = "in",
    source: str = "dc",
    top_n: int = 10,
) -> list[SectorFlowRow]:
    """Load top-N inflow / outflow sectors by net_amount.

    direction='in' → highest net_amount; direction='out' → most negative.
    source='dc' uses raw_moneyflow_ind_dc (richest); 'ths' available too.
    """
    if direction not in ("in", "out"):
        raise ValueError(f"direction must be 'in' or 'out', got {direction}")

    order_dir = "DESC" if direction == "in" else "ASC"

    if source == "dc":
        sql = text(f"""
            SELECT mf.ts_code, 'dc' AS sector_source, mf.name,
                   mf.pct_change, mf.net_amount, mf.net_amount_rate,
                   mf.buy_elg_amount_rate,
                   ss.role, ss.cycle_phase
            FROM {SCHEMA}.raw_moneyflow_ind_dc mf
            LEFT JOIN {SCHEMA}.sector_state_daily ss
                   ON ss.sector_code = mf.ts_code
                  AND ss.sector_source = 'dc'
                  AND ss.trade_date = mf.trade_date
            WHERE mf.trade_date = :d
              AND mf.content_type IN ('概念','行业')
            ORDER BY mf.net_amount {order_dir} NULLS LAST
            LIMIT :n
        """)
    elif source == "ths":
        sql = text(f"""
            SELECT mf.ts_code, 'ths' AS sector_source, mf.industry AS name,
                   mf.pct_change, mf.net_amount, NULL AS net_amount_rate,
                   NULL AS elg_buy_rate,
                   ss.role, ss.cycle_phase
            FROM {SCHEMA}.raw_moneyflow_ind_ths mf
            LEFT JOIN {SCHEMA}.sector_state_daily ss
                   ON ss.sector_code = mf.ts_code
                  AND ss.sector_source = 'ths'
                  AND ss.trade_date = mf.trade_date
            WHERE mf.trade_date = :d
            ORDER BY mf.net_amount {order_dir} NULLS LAST
            LIMIT :n
        """)
    else:
        raise ValueError(f"source must be 'dc' or 'ths', got {source}")

    with engine.connect() as conn:
        rows = conn.execute(sql, {"d": trade_date, "n": top_n}).fetchall()

    return [
        SectorFlowRow(
            sector_code=r[0],
            sector_source=r[1],
            sector_name=r[2] or "",
            pct_change=float(r[3]) if r[3] is not None else None,
            net_amount=float(r[4]) if r[4] is not None else None,
            net_amount_rate=float(r[5]) if r[5] is not None else None,
            elg_buy_rate=float(r[6]) if r[6] is not None else None,
            role=r[7],
            cycle_phase=r[8],
        )
        for r in rows
    ]


def load_quality_flows(
    engine: Engine,
    trade_date: dt.date,
    *,
    top_n: int = 8,
) -> list[SectorFlowRow]:
    """High-quality inflow sectors:放量 + 上涨 + 高 elg_rate + 趋势确认.

    Heuristic: heat_score >= 0.65 AND trend_score >= 0.60 AND
               role IN ('主线','中军','轮动','催化') AND pct_change > 0.5.
    """
    sql = text(f"""
        SELECT mf.ts_code, 'dc' AS sector_source, mf.name,
               mf.pct_change, mf.net_amount, mf.net_amount_rate,
               mf.buy_elg_amount_rate,
               ss.role, ss.cycle_phase
        FROM {SCHEMA}.raw_moneyflow_ind_dc mf
        JOIN {SCHEMA}.factor_daily fd
              ON fd.sector_code = mf.ts_code
             AND fd.sector_source = 'dc'
             AND fd.trade_date = mf.trade_date
        JOIN {SCHEMA}.sector_state_daily ss
              ON ss.sector_code = mf.ts_code
             AND ss.sector_source = 'dc'
             AND ss.trade_date = mf.trade_date
        WHERE mf.trade_date = :d
          AND mf.content_type IN ('概念','行业')
          AND fd.heat_score >= 0.65
          AND fd.trend_score >= 0.60
          AND ss.role IN ('主线','中军','轮动','催化')
          AND mf.pct_change > 0.5
        ORDER BY fd.heat_score DESC
        LIMIT :n
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"d": trade_date, "n": top_n}).fetchall()

    return [
        SectorFlowRow(
            sector_code=r[0], sector_source=r[1], sector_name=r[2] or "",
            pct_change=float(r[3]) if r[3] is not None else None,
            net_amount=float(r[4]) if r[4] is not None else None,
            net_amount_rate=float(r[5]) if r[5] is not None else None,
            elg_buy_rate=float(r[6]) if r[6] is not None else None,
            role=r[7], cycle_phase=r[8],
        )
        for r in rows
    ]


def load_crowded_sectors(
    engine: Engine,
    trade_date: dt.date,
    *,
    top_n: int = 6,
) -> list[SectorFlowRow]:
    """Crowded sectors: 高 crowding_score AND (heat 高位 + pct 滞涨)."""
    sql = text(f"""
        SELECT mf.ts_code, 'dc' AS sector_source, mf.name,
               mf.pct_change, mf.net_amount, mf.net_amount_rate,
               mf.buy_elg_amount_rate,
               ss.role, ss.cycle_phase
        FROM {SCHEMA}.factor_daily fd
        JOIN {SCHEMA}.raw_moneyflow_ind_dc mf
              ON fd.sector_code = mf.ts_code
             AND fd.sector_source = 'dc'
             AND fd.trade_date = mf.trade_date
        LEFT JOIN {SCHEMA}.sector_state_daily ss
              ON ss.sector_code = fd.sector_code
             AND ss.sector_source = fd.sector_source
             AND ss.trade_date = fd.trade_date
        WHERE fd.trade_date = :d
          AND fd.crowding_score >= 0.55
          AND mf.content_type IN ('概念','行业')
        ORDER BY fd.crowding_score DESC
        LIMIT :n
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"d": trade_date, "n": top_n}).fetchall()
    return [
        SectorFlowRow(
            sector_code=r[0], sector_source=r[1], sector_name=r[2] or "",
            pct_change=float(r[3]) if r[3] is not None else None,
            net_amount=float(r[4]) if r[4] is not None else None,
            net_amount_rate=float(r[5]) if r[5] is not None else None,
            elg_buy_rate=float(r[6]) if r[6] is not None else None,
            role=r[7], cycle_phase=r[8],
        )
        for r in rows
    ]


def load_cycle_grid(
    engine: Engine,
    trade_date: dt.date,
    *,
    sources: tuple[str, ...] = ("sw", "dc"),
    top_n: int = 25,
) -> list[CycleGridRow]:
    """Sector cycle phases — top by heat_score within active phases."""
    placeholders = ", ".join(f"'{s}'" for s in sources)
    sql = text(f"""
        SELECT ss.sector_code, ss.sector_source, ss.sector_name,
               ss.role, ss.cycle_phase, ss.role_confidence, ss.phase_confidence,
               fd.heat_score, fd.persistence_score, fd.crowding_score
        FROM {SCHEMA}.sector_state_daily ss
        JOIN {SCHEMA}.factor_daily fd
              ON fd.sector_code = ss.sector_code
             AND fd.sector_source = ss.sector_source
             AND fd.trade_date = ss.trade_date
        WHERE ss.trade_date = :d
          AND ss.sector_source IN ({placeholders})
          AND ss.cycle_phase IN ('点火','确认','扩散','高潮','分歧','退潮')
        ORDER BY
          CASE ss.cycle_phase
            WHEN '高潮' THEN 1 WHEN '扩散' THEN 2 WHEN '确认' THEN 3
            WHEN '点火' THEN 4 WHEN '分歧' THEN 5 WHEN '退潮' THEN 6
          END ASC,
          fd.heat_score DESC
        LIMIT :n
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"d": trade_date, "n": top_n}).fetchall()
    return [
        CycleGridRow(
            sector_code=r[0], sector_source=r[1], sector_name=r[2] or "",
            role=r[3] or "未识别", cycle_phase=r[4] or "未识别",
            role_confidence=r[5], phase_confidence=r[6],
            heat_score=float(r[7]) if r[7] is not None else None,
            persistence_score=float(r[8]) if r[8] is not None else None,
            crowding_score=float(r[9]) if r[9] is not None else None,
        )
        for r in rows
    ]


def load_tomorrow_target_pool(
    engine: Engine,
    trade_date: dt.date,
    *,
    top_n: int = 8,
) -> list[TomorrowTarget]:
    """Pre-filter the top sectors that *could* be tomorrow's candidates.

    The LLM (S7) will pick 3–5 of these to annotate with reasoning + risk +
    validation point.  We pre-filter to avoid feeding the LLM 1000+ sectors.

    Selection: role ∈ {主线, 中军, 轮动, 催化}, sorted by composite of
    heat_score + trend_score + (1 − crowding_score) for fresher signals.
    """
    sql = text(f"""
        SELECT ss.sector_code, ss.sector_source, ss.sector_name,
               ss.role, ss.cycle_phase,
               fd.heat_score, fd.trend_score, fd.persistence_score, fd.crowding_score
        FROM {SCHEMA}.sector_state_daily ss
        JOIN {SCHEMA}.factor_daily fd
              ON fd.sector_code = ss.sector_code
             AND fd.sector_source = ss.sector_source
             AND fd.trade_date = ss.trade_date
        WHERE ss.trade_date = :d
          AND ss.role IN ('主线','中军','轮动','催化')
          AND ss.sector_source IN ('dc','sw')
        ORDER BY
          (COALESCE(fd.heat_score,0) * 0.40
           + COALESCE(fd.trend_score,0) * 0.35
           + (1 - COALESCE(fd.crowding_score,0)) * 0.25) DESC
        LIMIT :n
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"d": trade_date, "n": top_n}).fetchall()

    targets: list[TomorrowTarget] = []
    for r in rows:
        sector_code, sector_source = r[0], r[1]
        # Look up leaders (top 3 for this sector)
        lsql = text(f"""
            SELECT ts_code, name, role, score, lu_desc
            FROM {SCHEMA}.stock_signals_daily
            WHERE trade_date = :d
              AND primary_sector_code = :sc
              AND primary_sector_source = :src
              AND role IN ('龙头','中军','情绪先锋')
            ORDER BY score DESC
            LIMIT 3
        """)
        with engine.connect() as conn:
            leader_rows = conn.execute(lsql, {
                "d": trade_date, "sc": sector_code, "src": sector_source,
            }).fetchall()

        targets.append(TomorrowTarget(
            sector_code=sector_code, sector_source=sector_source,
            sector_name=r[2] or "",
            role=r[3] or "未识别",
            cycle_phase=r[4] or "未识别",
            heat_score=float(r[5]) if r[5] is not None else None,
            trend_score=float(r[6]) if r[6] is not None else None,
            persistence_score=float(r[7]) if r[7] is not None else None,
            crowding_score=float(r[8]) if r[8] is not None else None,
            leaders=[
                {"ts_code": lr[0], "name": lr[1], "role": lr[2],
                 "score": float(lr[3]) if lr[3] is not None else None,
                 "lu_desc": lr[4]}
                for lr in leader_rows
            ],
        ))
    return targets


def load_sector_structures(
    engine: Engine,
    trade_date: dt.date,
    *,
    top_n_sectors: int = 6,
) -> list[SectorStructureRow]:
    """Per-sector internal structure: leader / core troops / vanguard.

    Restricted to top N active sectors (by heat) to keep the table compact.
    """
    sql_sec = text(f"""
        SELECT ss.sector_code, ss.sector_source, ss.sector_name,
               ss.role, ss.cycle_phase
        FROM {SCHEMA}.sector_state_daily ss
        JOIN {SCHEMA}.factor_daily fd
              ON fd.sector_code = ss.sector_code
             AND fd.sector_source = ss.sector_source
             AND fd.trade_date = ss.trade_date
        WHERE ss.trade_date = :d
          AND ss.role IN ('主线','中军','轮动','催化')
        ORDER BY fd.heat_score DESC NULLS LAST
        LIMIT :n
    """)
    out: list[SectorStructureRow] = []
    with engine.connect() as conn:
        sectors = conn.execute(sql_sec, {"d": trade_date, "n": top_n_sectors}).fetchall()

    for s in sectors:
        sec_code, sec_src, sec_name, role, cycle = s

        # Pull stocks tagged as 龙头 / 中军 / 情绪先锋 for this sector
        sig_sql = text(f"""
            SELECT ts_code, name, role, score, lu_desc, theme, evidence_json
            FROM {SCHEMA}.stock_signals_daily
            WHERE trade_date = :d
              AND primary_sector_code = :sc
              AND primary_sector_source = :src
              AND role IN ('龙头','中军','情绪先锋')
        """)
        with engine.connect() as conn:
            sig_rows = conn.execute(sig_sql, {
                "d": trade_date, "sc": sec_code, "src": sec_src,
            }).fetchall()

        leader = None
        cores: list[dict[str, Any]] = []
        vanguard = None
        for sr in sig_rows:
            ev = sr[6] if isinstance(sr[6], dict) else (json.loads(sr[6]) if sr[6] else {})
            stock_dict = {
                "ts_code": sr[0], "name": sr[1], "role": sr[2],
                "score": float(sr[3]) if sr[3] is not None else None,
                "lu_desc": sr[4], "theme": sr[5],
                "consec_boards": ev.get("consec_boards"),
                "rs": ev.get("rs"),
                "has_top_inst": ev.get("has_top_inst"),
            }
            if sr[2] == "龙头":
                leader = stock_dict
            elif sr[2] == "中军":
                cores.append(stock_dict)
            elif sr[2] == "情绪先锋":
                vanguard = stock_dict

        out.append(SectorStructureRow(
            sector_code=sec_code, sector_source=sec_src,
            sector_name=sec_name or "",
            role=role or "未识别",
            cycle_phase=cycle or "未识别",
            leader=leader, core_troops=cores, vanguard=vanguard,
        ))
    return out


def load_candidate_pool(
    engine: Engine,
    trade_date: dt.date,
    *,
    fillers_n: int = 12,
    trending_n: int = 12,
) -> list[CandidateStock]:
    """Stock signals where role ∈ {补涨, 趋势}."""
    sql = text(f"""
        SELECT ss.ts_code, ss.name, ss.role, ss.score,
               sd.sector_name, ss.theme, ss.evidence_json,
               rd.pct_chg
        FROM {SCHEMA}.stock_signals_daily ss
        LEFT JOIN {SCHEMA}.sector_state_daily sd
              ON sd.sector_code = ss.primary_sector_code
             AND sd.sector_source = ss.primary_sector_source
             AND sd.trade_date = ss.trade_date
        LEFT JOIN {SCHEMA}.raw_daily rd
              ON rd.ts_code = ss.ts_code
             AND rd.trade_date = ss.trade_date
        WHERE ss.trade_date = :d
          AND ss.role IN ('补涨','趋势')
        ORDER BY ss.role, ss.score DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"d": trade_date}).fetchall()

    out: list[CandidateStock] = []
    fillers = 0
    trending = 0
    for r in rows:
        if r[2] == "补涨" and fillers >= fillers_n:
            continue
        if r[2] == "趋势" and trending >= trending_n:
            continue
        ev = r[6] if isinstance(r[6], dict) else (json.loads(r[6]) if r[6] else {})
        out.append(CandidateStock(
            ts_code=r[0], name=r[1], role=r[2],
            score=float(r[3]) if r[3] is not None else 0.0,
            primary_sector_name=r[4],
            theme=r[5],
            pct_chg_today=float(r[7]) if r[7] is not None else None,
            evidence=ev,
        ))
        if r[2] == "补涨":
            fillers += 1
        else:
            trending += 1
    return out


def load_yesterday_hypotheses(
    engine: Engine,
    *,
    report_date: dt.date,
    family: str = "smartmoney",
    report_type: str = "evening_long",
) -> list[dict[str, Any]]:
    """Load yesterday's evening report hypotheses for review."""
    sql = text("""
        SELECT j.judgment_id, j.judgment_text, j.target, j.horizon,
               j.validation_method, j.confidence, r.report_date
          FROM report_judgments j
          JOIN report_runs r ON r.report_run_id = j.report_run_id
         WHERE r.report_family = :fam
           AND r.report_type = :rt
           AND r.report_date < :rd
           AND r.report_date >= :rd_min
           AND j.judgment_type = 'hypothesis'
           AND r.status = 'succeeded'
         ORDER BY r.report_date DESC, j.created_at
         LIMIT 8
    """)
    rd_min = report_date - dt.timedelta(days=4)
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "fam": family, "rt": report_type,
            "rd": report_date, "rd_min": rd_min,
        }).all()
    return [
        {
            "judgment_id": str(r.judgment_id),
            "hypothesis": r.judgment_text,
            "target": r.target,
            "horizon": r.horizon,
            "validation_method": r.validation_method,
            "confidence": r.confidence,
            "from_date": str(r.report_date),
        }
        for r in rows
    ]


def load_today_outcome_for_review(
    engine: Engine,
    trade_date: dt.date,
) -> dict[str, Any]:
    """Snapshot today's market for evaluating yesterday's hypotheses.

    Returns a small dict the LLM can use to compare predictions vs outcomes:
      market_state, top_inflow_sectors, top_outflow_sectors,
      top_limit_up_sectors, max_consec_limit_up.
    """
    pulse = load_market_pulse(engine, trade_date)
    inflow = load_sector_flows(engine, trade_date, direction="in", top_n=8)
    outflow = load_sector_flows(engine, trade_date, direction="out", top_n=5)

    return {
        "trade_date": str(trade_date),
        "market_state": pulse.market_state,
        "limit_up_count": pulse.limit_up_count,
        "limit_down_count": pulse.limit_down_count,
        "max_consecutive_limit_up": pulse.max_consecutive_limit_up,
        "top_inflow_sectors": [
            {"name": s.sector_name, "pct_change": s.pct_change, "net_amount": s.net_amount}
            for s in inflow
        ],
        "top_outflow_sectors": [
            {"name": s.sector_name, "pct_change": s.pct_change, "net_amount": s.net_amount}
            for s in outflow
        ],
    }


# ── Trading-day resolution helper ─────────────────────────────────────────────

def find_latest_trade_date(engine: Engine, *, on_or_before: dt.date) -> dt.date | None:
    """Find the latest trade date in market_state_daily on or before the given
    date.  Useful when the report_date isn't a trading day (weekend/holiday).
    """
    sql = text(f"""
        SELECT MAX(trade_date) FROM {SCHEMA}.market_state_daily
        WHERE trade_date <= :d
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"d": on_or_before}).fetchone()
    return row[0] if row and row[0] else None
