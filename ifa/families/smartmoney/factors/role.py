"""板块六类角色识别 → sector_state_daily.role.

The "role" answers the question: *what is this sector doing right now in the
market structure?*  Six possible roles + 未识别:

  主线 (mainline)     ── 当日资金共识；是市场的核心叙事
  中军 (core troop)   ── 大资金确认型；非情绪型，强势但不极端
  轮动 (rotation)     ── 次级流入；资金刚切过来，趋势刚转正
  防守 (defense)      ── 风险规避型；表现稳定但不爆发
  催化 (catalyst)     ── 事件驱动；突发热度（短持续 + 高趋势）
  退潮 (retreat)      ── 资金撤离；高拥挤 + 弱趋势 + 低持续

Inputs:
  smartmoney.factor_daily  (heat/trend/persistence/crowding scores)
  smartmoney.market_state_daily  (for context: 进攻/中性/防守/退潮)
  smartmoney.raw_kpl_concept  (z_t_num — for catalyst signal cross-check)

Output:
  sector_state_daily rows (role + role_confidence + evidence_json).

Design philosophy:
  - Rules are evaluated by tier; each tier has STRONG / MEDIUM / WEAK matchers.
  - First strong match wins; otherwise medium; otherwise weak; else 未识别.
  - evidence_json captures *which conditions fired* so a human can audit.
  - All thresholds live in params.role.* so backtesting can tune them.

Trade-offs documented in code:
  - 主线 vs 中军: 主线 has the highest heat AND is in 进攻 market state.
  - 中军 fires when sector is strong but market is mixed (中性/防守).
  - 催化 is identified when persistence is LOW but heat & trend are HIGH —
    classic "news-driven spike" signature.
  - 退潮 requires both fading momentum AND trapped capital (crowding) — to
    avoid mis-classifying brand-new cold sectors as 退潮.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"


# ── Output ────────────────────────────────────────────────────────────────────

@dataclass
class SectorRoleAssignment:
    trade_date: dt.date
    sector_code: str
    sector_source: str
    sector_name: str | None
    role: str                      # 主线/中军/轮动/防守/催化/退潮/未识别
    confidence: str                # high / medium / low
    evidence: dict[str, Any] = field(default_factory=dict)


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_factor_panel(engine: Engine, trade_date: dt.date) -> pd.DataFrame:
    """Load all factor_daily rows for the trade date across all sources."""
    sql = f"""
        SELECT trade_date, sector_code, sector_source, sector_name,
               heat_score, trend_score, persistence_score, crowding_score,
               derived_json
        FROM {SCHEMA}.factor_daily
        WHERE trade_date = :d
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "trade_date", "sector_code", "sector_source", "sector_name",
        "heat_score", "trend_score", "persistence_score", "crowding_score",
        "derived_json",
    ])
    for c in ["heat_score", "trend_score", "persistence_score", "crowding_score"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _load_market_state(engine: Engine, trade_date: dt.date) -> str | None:
    """Look up the day's market_state from market_state_daily."""
    sql = f"SELECT market_state FROM {SCHEMA}.market_state_daily WHERE trade_date = :d"
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"d": trade_date}).fetchone()
    return row[0] if row else None


def _load_kpl_concepts(engine: Engine, trade_date: dt.date) -> dict[str, dict[str, int]]:
    """Map sector_name → {z_t_num, up_num} for catalyst cross-check.

    KPL concept names are short and may not match SW/DC names exactly, so
    we use a substring match downstream.
    """
    sql = f"""
        SELECT name, z_t_num, up_num
        FROM {SCHEMA}.raw_kpl_concept
        WHERE trade_date = :d
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date}).fetchall()
    return {
        r[0]: {"z_t_num": int(r[1] or 0), "up_num": int(r[2] or 0)}
        for r in rows if r[0]
    }


def _load_factor_history(
    engine: Engine,
    trade_date: dt.date,
    n_days: int = 5,
) -> pd.DataFrame:
    """Load last n_days of factor_daily for momentum/delta calculations."""
    start = trade_date - dt.timedelta(days=n_days * 2 + 5)
    sql = f"""
        SELECT trade_date, sector_code, sector_source,
               heat_score, trend_score, crowding_score
        FROM {SCHEMA}.factor_daily
        WHERE trade_date >= :start AND trade_date < :d
        ORDER BY trade_date ASC
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "start": start}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "trade_date", "sector_code", "sector_source",
        "heat_score", "trend_score", "crowding_score",
    ])
    for c in ["heat_score", "trend_score", "crowding_score"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ── Catalyst detection helper ─────────────────────────────────────────────────

def _is_catalyst_named(sector_name: str | None, kpl_concepts: dict[str, dict[str, int]]) -> tuple[bool, int]:
    """Return (matched, max_z_t_num) by checking if any KPL concept name
    overlaps with this sector's name.

    Substring match in either direction; threshold of 2+ chars overlap.
    """
    if not sector_name:
        return (False, 0)
    best = 0
    matched = False
    for kpl_name, stats in kpl_concepts.items():
        if not kpl_name or len(kpl_name) < 2:
            continue
        # Substring match either way (e.g. "AI算力" matches "算力" sector)
        if kpl_name in sector_name or sector_name in kpl_name:
            matched = True
            best = max(best, stats["z_t_num"])
    return (matched, best)


# ── Core classification ───────────────────────────────────────────────────────

def _classify_sector(
    *,
    sector_code: str,
    sector_source: str,
    sector_name: str | None,
    heat: float | None,
    trend: float | None,
    persistence: float | None,
    crowding: float | None,
    heat_delta: float | None,         # heat today - heat yesterday
    market_state: str | None,
    catalyst_match: bool,
    catalyst_z_t: int,
    params: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Return (role, confidence, evidence) for a single sector.

    Decision tree (priority order):
      1. 催化  — short-duration spike with news/limit-up confirmation
      2. 主线  — top-tier strength + market in attack mode
      3. 中军  — confirmed strength even when market is not in attack
      4. 轮动  — rising heat, low crowding, trend turning positive
      5. 退潮  — declining heat + trapped capital
      6. 防守  — stable persistence with low crowding (utility-like)
      7. 未识别 — none of the above; ambiguous
    """
    rp = params.get("role", {})

    # Treat None as missing (low confidence by default)
    heat = float(heat) if heat is not None else 0.0
    trend = float(trend) if trend is not None else 0.0
    persistence = float(persistence) if persistence is not None else 0.0
    crowding = float(crowding) if crowding is not None else 0.0
    heat_delta = float(heat_delta) if heat_delta is not None else 0.0

    fired: list[str] = []  # accumulates rule-fired tags into evidence

    # ── 1. 催化 (catalyst) ────────────────────────────────────────────────────
    cat = rp.get("catalyst", {})
    cat_heat_min = cat.get("heat_score_min", 0.60)
    cat_persist_max = cat.get("persistence_score_max", 0.40)
    if heat >= cat_heat_min and persistence <= cat_persist_max and trend >= 0.55:
        # Strong catalyst: matched a KPL hot concept with limit-ups
        if catalyst_match and catalyst_z_t >= 3:
            fired.append("catalyst_kpl_match")
            ev = {
                "rule": "catalyst",
                "heat": heat, "trend": trend, "persistence": persistence,
                "kpl_z_t_num": catalyst_z_t, "kpl_match": True,
                "fired": fired,
            }
            return ("催化", "high", ev)
        # Medium catalyst: factor signature fits but no KPL confirmation
        if heat >= 0.70 and heat_delta >= 0.15:
            fired.append("catalyst_factor_only")
            ev = {
                "rule": "catalyst",
                "heat": heat, "trend": trend, "persistence": persistence,
                "heat_delta": heat_delta, "kpl_match": catalyst_match,
                "fired": fired,
            }
            return ("催化", "medium", ev)

    # ── 2. 主线 (mainline) ────────────────────────────────────────────────────
    ml = rp.get("mainline", {})
    ml_heat = ml.get("heat_score_min", 0.75)
    ml_persist = ml.get("persistence_score_min", 0.60)
    ml_trend = ml.get("trend_score_min", 0.65)
    if heat >= ml_heat and persistence >= ml_persist and trend >= ml_trend:
        if market_state == "进攻":
            fired.append("mainline_strong")
            return ("主线", "high", {
                "rule": "mainline",
                "heat": heat, "trend": trend, "persistence": persistence,
                "crowding": crowding, "market_state": market_state,
                "fired": fired,
            })
        if market_state in ("中性", None):
            fired.append("mainline_medium")
            return ("主线", "medium", {
                "rule": "mainline",
                "heat": heat, "trend": trend, "persistence": persistence,
                "market_state": market_state, "fired": fired,
            })
        # In 防守/退潮 market a "main line" is suspicious — downgrade to 中军
        fired.append("mainline_downgraded_to_core_troop")

    # ── 3. 中军 (core troop) ──────────────────────────────────────────────────
    ct = rp.get("core_troop", {})
    ct_heat = ct.get("heat_score_min", 0.65)
    ct_trend = ct.get("trend_score_min", 0.60)
    if heat >= ct_heat and trend >= ct_trend and persistence >= 0.45:
        # 中军 should be 大资金确认 — check elg_rate hint via DC
        confidence = "high" if (heat >= 0.75 and crowding <= 0.55) else "medium"
        fired.append("core_troop")
        return ("中军", confidence, {
            "rule": "core_troop",
            "heat": heat, "trend": trend, "persistence": persistence,
            "crowding": crowding, "market_state": market_state, "fired": fired,
        })

    # ── 4. 轮动 (rotation) ────────────────────────────────────────────────────
    rot = rp.get("rotation", {})
    rot_heat_min = rot.get("heat_score_min", 0.55)
    rot_heat_max = rot.get("heat_score_max", 0.75)
    rot_trend = rot.get("trend_score_min", 0.50)
    rot_crowd_max = rot.get("crowding_score_max", 0.45)
    # Rotation = capital recently turned positive; trend lifting; not crowded
    if (rot_heat_min <= heat <= rot_heat_max
            and trend >= rot_trend
            and crowding <= rot_crowd_max
            and heat_delta >= 0.05):
        confidence = "high" if heat_delta >= 0.15 else "medium"
        fired.append("rotation")
        return ("轮动", confidence, {
            "rule": "rotation",
            "heat": heat, "trend": trend, "persistence": persistence,
            "crowding": crowding, "heat_delta": heat_delta,
            "fired": fired,
        })

    # ── 5. 退潮 (retreat) ─────────────────────────────────────────────────────
    rt = rp.get("retreat", {})
    rt_heat_max = rt.get("heat_score_max", 0.35)
    rt_persist_max = rt.get("persistence_score_max", 0.30)
    if heat <= rt_heat_max and persistence <= rt_persist_max:
        # Strong retreat: also crowded (trapped capital) AND heat declining
        if crowding >= 0.55 and heat_delta <= -0.10:
            fired.append("retreat_with_trapped_capital")
            return ("退潮", "high", {
                "rule": "retreat",
                "heat": heat, "trend": trend, "persistence": persistence,
                "crowding": crowding, "heat_delta": heat_delta,
                "fired": fired,
            })
        if heat_delta <= -0.05:
            fired.append("retreat_basic")
            return ("退潮", "medium", {
                "rule": "retreat",
                "heat": heat, "persistence": persistence, "heat_delta": heat_delta,
                "fired": fired,
            })

    # ── 6. 防守 (defense) ─────────────────────────────────────────────────────
    df_p = rp.get("defense", {})
    df_crowd_max = df_p.get("crowding_score_max", 0.30)
    df_persist_min = df_p.get("persistence_score_min", 0.40)
    # Defense = stable persistent positive flow but not exciting; low crowding
    if (persistence >= df_persist_min
            and crowding <= df_crowd_max
            and 0.35 <= heat <= 0.65
            and abs(heat_delta) <= 0.10):
        fired.append("defense")
        return ("防守", "medium", {
            "rule": "defense",
            "heat": heat, "trend": trend, "persistence": persistence,
            "crowding": crowding, "fired": fired,
        })

    # ── 7. 未识别 ──────────────────────────────────────────────────────────────
    return ("未识别", "low", {
        "rule": "none_matched",
        "heat": heat, "trend": trend, "persistence": persistence,
        "crowding": crowding, "heat_delta": heat_delta,
        "market_state": market_state,
        "fired": fired,
    })


# ── Orchestrator ──────────────────────────────────────────────────────────────

def compute_roles_for_date(
    engine: Engine,
    trade_date: dt.date,
    *,
    params: dict[str, Any],
) -> list[SectorRoleAssignment]:
    """Compute role for every sector with factor data on ``trade_date``.

    Reads:
        factor_daily (today + last few days for delta)
        market_state_daily (today)
        raw_kpl_concept (today, for catalyst confirmation)

    Returns:
        list of SectorRoleAssignment.  Caller persists via write_sector_states.
    """
    today = _load_factor_panel(engine, trade_date)
    if today.empty:
        log.warning("[role] no factor_daily for %s", trade_date)
        return []

    market_state = _load_market_state(engine, trade_date)
    kpl = _load_kpl_concepts(engine, trade_date)
    history = _load_factor_history(engine, trade_date, n_days=3)

    # Build heat_delta lookup: today.heat - prior_day.heat
    if not history.empty:
        latest_prior = (history.sort_values("trade_date")
                        .groupby(["sector_code", "sector_source"])
                        .tail(1)
                        .set_index(["sector_code", "sector_source"])["heat_score"])
    else:
        latest_prior = pd.Series(dtype=float)

    out: list[SectorRoleAssignment] = []
    for _, row in today.iterrows():
        key = (row["sector_code"], row["sector_source"])
        prior_heat = latest_prior.get(key) if not latest_prior.empty else None
        heat_delta = (
            float(row["heat_score"]) - float(prior_heat)
            if (prior_heat is not None
                and pd.notna(prior_heat)
                and pd.notna(row["heat_score"]))
            else None
        )

        match, z_t = _is_catalyst_named(row.get("sector_name"), kpl)

        role, conf, evidence = _classify_sector(
            sector_code=row["sector_code"],
            sector_source=row["sector_source"],
            sector_name=row.get("sector_name"),
            heat=row["heat_score"],
            trend=row["trend_score"],
            persistence=row["persistence_score"],
            crowding=row["crowding_score"],
            heat_delta=heat_delta,
            market_state=market_state,
            catalyst_match=match,
            catalyst_z_t=z_t,
            params=params,
        )

        out.append(SectorRoleAssignment(
            trade_date=trade_date,
            sector_code=row["sector_code"],
            sector_source=row["sector_source"],
            sector_name=row.get("sector_name"),
            role=role,
            confidence=conf,
            evidence=evidence,
        ))

    log.info("[role] %s: classified %d sectors (market=%s)",
             trade_date, len(out), market_state)
    return out
