"""板块情绪周期状态机 → sector_state_daily.cycle_phase.

Seven-stage cycle (with hysteresis to avoid noise-driven flapping):

    冷 ──► 点火 ──► 确认 ──► 扩散 ──► 高潮 ──► 分歧 ──► 退潮 ──► 冷
                              │                    │           ▲
                              └────► (stays in 扩散) ─────┘

State semantics:
  冷 (cold)        : 资金未流入，无关注度 — 长期低 heat + 低 persistence
  点火 (ignition)  : 突然出现资金流入信号 — heat 显著抬升
  确认 (confirm)   : 多日资金共识 — persistence 建立
  扩散 (expand)    : 上涨家数扩大 — breadth 增加，heat 持续
  高潮 (peak)      : 资金极度共识但价格滞涨 — crowding 顶点
  分歧 (diverge)   : heat 开始下降但 crowding 仍高 — 接力失败
  退潮 (retire)    : 资金撤离 — heat 跌、crowding 仍高、持续性破

Implementation:
  - Read yesterday's phase from sector_state_daily.
  - For each sector, evaluate the *allowed* transitions (state-machine).
  - Add hysteresis: a transition only fires when the new condition is met
    by a clear margin (set in params.cycle.hysteresis_margin).
  - "未识别" is the bootstrap state when no prior phase exists.

Why a state machine:
  - Cycles only go forward (mostly): 冷→点火→确认→...
  - Without a state machine, a noisy day can flip a sector from 扩散 to 退潮
    and back, ruining the report's narrative.
  - Backwards transitions are deliberately rare: only allowed for
    {点火 → 冷} (failed ignition) and {扩散/高潮 → 分歧} (top forming).
"""
from __future__ import annotations

import datetime as dt
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
class SectorPhaseAssignment:
    trade_date: dt.date
    sector_code: str
    sector_source: str
    sector_name: str | None
    cycle_phase: str               # 冷/点火/确认/扩散/高潮/分歧/退潮/未识别
    confidence: str
    evidence: dict[str, Any] = field(default_factory=dict)


# ── State machine: allowed transitions from each state ────────────────────────

# Each state lists the states that can be reached from it (plus self-loop).
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "冷":   {"冷", "点火"},
    "点火": {"点火", "确认", "冷"},                  # ignition can fail
    "确认": {"确认", "扩散", "分歧", "退潮"},        # rare retreat from confirm
    "扩散": {"扩散", "高潮", "分歧"},
    "高潮": {"高潮", "分歧", "退潮"},
    "分歧": {"分歧", "退潮", "扩散"},                # 扩散 only on recovery
    "退潮": {"退潮", "冷"},
    "未识别": {"冷", "点火", "确认", "扩散", "高潮", "分歧", "退潮", "未识别"},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_prior_phase(
    engine: Engine,
    trade_date: dt.date,
) -> dict[tuple[str, str], str]:
    """Load yesterday's cycle_phase for each sector.

    Looks back up to 5 calendar days (to handle weekends / holidays).
    """
    sql = f"""
        WITH latest AS (
            SELECT sector_code, sector_source,
                   MAX(trade_date) AS prev_date
            FROM {SCHEMA}.sector_state_daily
            WHERE trade_date < :d AND trade_date >= :start
            GROUP BY sector_code, sector_source
        )
        SELECT s.sector_code, s.sector_source, s.cycle_phase
        FROM {SCHEMA}.sector_state_daily s
        JOIN latest l
          ON s.sector_code = l.sector_code
         AND s.sector_source = l.sector_source
         AND s.trade_date = l.prev_date
    """
    start = trade_date - dt.timedelta(days=10)
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "start": start}).fetchall()
    return {(r[0], r[1]): r[2] for r in rows if r[2]}


def _load_factor_panel_with_history(
    engine: Engine,
    trade_date: dt.date,
    n_days: int = 7,
) -> pd.DataFrame:
    """Load today's factor row + last n_days history for each sector."""
    start = trade_date - dt.timedelta(days=n_days * 2 + 5)
    sql = f"""
        SELECT trade_date, sector_code, sector_source, sector_name,
               heat_score, trend_score, persistence_score, crowding_score
        FROM {SCHEMA}.factor_daily
        WHERE trade_date >= :start AND trade_date <= :d
        ORDER BY trade_date ASC
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "start": start}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "trade_date", "sector_code", "sector_source", "sector_name",
        "heat_score", "trend_score", "persistence_score", "crowding_score",
    ])
    for c in ["heat_score", "trend_score", "persistence_score", "crowding_score"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ── Phase classification helpers (open-loop, no state-machine yet) ────────────

def _phase_signature(
    *,
    heat: float,
    trend: float,
    persistence: float,
    crowding: float,
    heat_delta_1d: float,
    heat_delta_3d: float,
    params: dict[str, Any],
) -> str:
    """Classify the *intrinsic* phase from current factor values.

    This function ignores prior state — it's the "open-loop" signal.  The
    state machine then constrains this signal by allowed transitions.
    """
    cp = params.get("cycle", {})

    cold = cp.get("cold", {})
    if heat <= cold.get("heat_score_max", 0.30) and persistence <= cold.get("persistence_score_max", 0.20):
        return "冷"

    ign = cp.get("ignition", {})
    if (heat_delta_1d >= ign.get("heat_score_delta_min", 0.20)
            and heat >= ign.get("heat_score_min", 0.40)
            and persistence <= ign.get("persistence_score_max", 0.30)):
        return "点火"

    peak = cp.get("peak", {})
    if (heat >= peak.get("heat_score_min", 0.80)
            and crowding >= peak.get("crowding_score_min", 0.50)):
        return "高潮"

    div = cp.get("diverge", {})
    if (crowding >= div.get("crowding_score_min", 0.65)
            and heat_delta_1d <= div.get("heat_score_delta_max", -0.10)):
        return "分歧"

    ret = cp.get("retire", {})
    if (heat <= ret.get("heat_score_max", 0.35)
            and crowding >= ret.get("crowding_score_min", 0.60)
            and persistence <= ret.get("persistence_score_max", 0.25)):
        return "退潮"

    exp = cp.get("expand", {})
    if (heat >= exp.get("heat_score_min", 0.60)
            and trend >= exp.get("trend_score_min", 0.60)
            and persistence >= exp.get("persistence_score_min", 0.60)):
        return "扩散"

    conf = cp.get("confirm", {})
    if (heat >= conf.get("heat_score_min", 0.55)
            and trend >= conf.get("trend_score_min", 0.55)
            and persistence >= 0.40):
        return "确认"

    return "未识别"


def _apply_state_machine(
    *,
    proposed: str,
    prior: str | None,
    confidence: str,
) -> tuple[str, str]:
    """Apply allowed-transition logic.  If the proposed transition isn't
    allowed from the prior state, the sector stays in its prior state with
    downgraded confidence.

    Returns (final_phase, final_confidence).
    """
    if prior is None or prior == "未识别":
        # Bootstrap: accept any signal on first observation
        return (proposed, confidence)

    allowed = ALLOWED_TRANSITIONS.get(prior, set())
    if proposed in allowed:
        return (proposed, confidence)

    # Disallowed transition → hold prior state but record the conflict
    return (prior, "low")


# ── Orchestrator ──────────────────────────────────────────────────────────────

def compute_phases_for_date(
    engine: Engine,
    trade_date: dt.date,
    *,
    params: dict[str, Any],
) -> list[SectorPhaseAssignment]:
    """Compute cycle_phase for every sector with factor data on ``trade_date``.

    Combines today's factors with prior phase + multi-day deltas.
    Caller persists results via write_sector_states.
    """
    panel = _load_factor_panel_with_history(engine, trade_date, n_days=5)
    if panel.empty:
        log.warning("[cycle] no factor history for %s", trade_date)
        return []

    today = panel[panel["trade_date"] == trade_date]
    if today.empty:
        log.warning("[cycle] no factor_daily for today=%s", trade_date)
        return []

    prior_phases = _load_prior_phase(engine, trade_date)

    # Pre-compute heat history per (code, source)
    history_by_key: dict[tuple[str, str], list[float]] = {}
    for (code, src), grp in panel[panel["trade_date"] < trade_date].groupby(
            ["sector_code", "sector_source"]):
        history_by_key[(code, src)] = grp.sort_values("trade_date")["heat_score"].tolist()

    out: list[SectorPhaseAssignment] = []
    for _, row in today.iterrows():
        key = (row["sector_code"], row["sector_source"])
        history = history_by_key.get(key, [])
        today_heat = float(row["heat_score"]) if pd.notna(row["heat_score"]) else 0.0
        heat_delta_1d = today_heat - history[-1] if history else 0.0
        heat_delta_3d = today_heat - history[-3] if len(history) >= 3 else heat_delta_1d

        proposed = _phase_signature(
            heat=today_heat,
            trend=float(row["trend_score"]) if pd.notna(row["trend_score"]) else 0.0,
            persistence=float(row["persistence_score"]) if pd.notna(row["persistence_score"]) else 0.0,
            crowding=float(row["crowding_score"]) if pd.notna(row["crowding_score"]) else 0.0,
            heat_delta_1d=heat_delta_1d,
            heat_delta_3d=heat_delta_3d,
            params=params,
        )

        prior = prior_phases.get(key)
        confidence = "medium"  # default; high if very strong signal, low if borderline
        if proposed in ("高潮", "退潮", "冷") and (
            today_heat >= 0.85 or today_heat <= 0.20
        ):
            confidence = "high"
        if proposed == "未识别":
            confidence = "low"

        final_phase, final_conf = _apply_state_machine(
            proposed=proposed,
            prior=prior,
            confidence=confidence,
        )

        evidence: dict[str, Any] = {
            "proposed_phase": proposed,
            "prior_phase": prior,
            "transition_allowed": proposed == final_phase,
            "heat": round(today_heat, 4),
            "heat_delta_1d": round(heat_delta_1d, 4),
            "heat_delta_3d": round(heat_delta_3d, 4),
            "trend": round(float(row["trend_score"] or 0), 4),
            "persistence": round(float(row["persistence_score"] or 0), 4),
            "crowding": round(float(row["crowding_score"] or 0), 4),
        }

        out.append(SectorPhaseAssignment(
            trade_date=trade_date,
            sector_code=row["sector_code"],
            sector_source=row["sector_source"],
            sector_name=row.get("sector_name"),
            cycle_phase=final_phase,
            confidence=final_conf,
            evidence=evidence,
        ))

    log.info("[cycle] %s: classified %d phases", trade_date, len(out))
    return out


# ── DB write (combines role + phase into sector_state_daily) ──────────────────

def _sanitize_for_json(obj: Any) -> Any:
    """Recursively replace float NaN/Inf with None so json.dumps produces valid JSON."""
    import math
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def write_sector_states(
    engine: Engine,
    *,
    roles: list,        # SectorRoleAssignment from role.py
    phases: list,       # SectorPhaseAssignment from cycle.py
) -> int:
    """Merge role and phase assignments into sector_state_daily.

    Uses (sector_code, sector_source) as join key; trade_date should be the
    same for all entries.  If only role or only phase is provided, the other
    column is set to '未识别'.
    """
    import json

    # Index by key for join
    role_by_key = {(r.sector_code, r.sector_source): r for r in (roles or [])}
    phase_by_key = {(p.sector_code, p.sector_source): p for p in (phases or [])}
    all_keys = set(role_by_key) | set(phase_by_key)

    if not all_keys:
        return 0

    sql = text(f"""
        INSERT INTO {SCHEMA}.sector_state_daily (
            trade_date, sector_code, sector_source, sector_name,
            role, cycle_phase, role_confidence, phase_confidence,
            evidence_json, computed_at
        ) VALUES (
            :trade_date, :sector_code, :sector_source, :sector_name,
            :role, :cycle_phase, :role_conf, :phase_conf,
            :evidence_json, now()
        )
        ON CONFLICT (trade_date, sector_code, sector_source) DO UPDATE SET
            sector_name      = EXCLUDED.sector_name,
            role             = EXCLUDED.role,
            cycle_phase      = EXCLUDED.cycle_phase,
            role_confidence  = EXCLUDED.role_confidence,
            phase_confidence = EXCLUDED.phase_confidence,
            evidence_json    = EXCLUDED.evidence_json,
            computed_at      = now()
    """)

    rows: list[dict[str, Any]] = []
    for key in all_keys:
        r = role_by_key.get(key)
        p = phase_by_key.get(key)
        rows.append({
            "trade_date": (r or p).trade_date,
            "sector_code": key[0],
            "sector_source": key[1],
            "sector_name": (r or p).sector_name,
            "role": r.role if r else "未识别",
            "cycle_phase": p.cycle_phase if p else "未识别",
            "role_conf": r.confidence if r else "low",
            "phase_conf": p.confidence if p else "low",
            "evidence_json": json.dumps(_sanitize_for_json({
                "role": r.evidence if r else None,
                "phase": p.evidence if p else None,
            }), ensure_ascii=False),
        })

    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)
