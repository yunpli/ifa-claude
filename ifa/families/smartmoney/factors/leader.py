"""板块内龙头股识别 → stock_signals_daily.

For each *active* sector (role ∈ {主线, 中军, 轮动, 催化}) we identify the
top-scoring constituent stocks and assign one of three role tags:

  龙头 (lead)             — top composite score: 资金 + 价格 + 涨停 + 机构席位
  中军 (core)             — top tier without limit-up (steady leader)
  情绪先锋 (sentiment vanguard) — highest 连板/一字板 (drives the emotion)

P0 scope: DC concepts + KPL concepts only.  SW/THS lack stock-membership tables
in our raw_* schema (would require index_member backfill — deferred to P1).

Scoring components (each cross-sectionally normalized to [0, 1] within the
sector's member set, then weighted):

  pct_chg_rs     ── relative strength: stock_pct_chg − sector_pct_chg
  amount_rank    ── trading-amount rank within sector
  elg_buy_rank   ── 超大单净流入 rank (from raw_moneyflow)
  limit_bonus    ── derived from raw_limit_list_d / raw_kpl_list
  top_inst_bonus ── institutional-seat appearance from raw_top_inst

Final score = weighted sum (weights from params.leader.weights).

The top score per sector becomes 龙头.  Among next ranks: highest 连板 score
is 情绪先锋; the rest with no limit-up are 中军.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import math

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"


# ── Output ────────────────────────────────────────────────────────────────────

@dataclass
class StockSignal:
    trade_date: dt.date
    ts_code: str
    name: str | None
    primary_sector_code: str
    primary_sector_source: str
    role: str                # 龙头 / 中军 / 情绪先锋 (this module) - 补涨/趋势 (candidate.py)
    score: float
    theme: str | None = None
    lu_desc: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_active_sectors(
    engine: Engine,
    trade_date: dt.date,
) -> pd.DataFrame:
    """Sectors with role ∈ {主线, 中军, 轮动, 催化} and source ∈ {dc, kpl, sw_l2}."""
    sql = f"""
        SELECT sector_code, sector_source, sector_name, role,
               cycle_phase, role_confidence
        FROM {SCHEMA}.sector_state_daily
        WHERE trade_date = :d
          AND role IN ('主线', '中军', '轮动', '催化')
          AND sector_source IN ('dc', 'kpl', 'sw_l2')
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date}).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=[
        "sector_code", "sector_source", "sector_name", "role",
        "cycle_phase", "role_confidence",
    ])


def _load_dc_members(
    engine: Engine,
    trade_date: dt.date,
    sector_codes: list[str],
) -> pd.DataFrame:
    """Stock constituents for DC concept sectors on the given date."""
    _COLS = ["sector_code", "ts_code", "name"]
    if not sector_codes:
        return pd.DataFrame(columns=_COLS)
    sql = f"""
        SELECT ts_code AS sector_code, con_code AS ts_code, name
        FROM {SCHEMA}.raw_dc_member
        WHERE trade_date = :d AND ts_code = ANY(:codes)
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "codes": sector_codes}).fetchall()
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows, columns=_COLS)


def _load_kpl_members(
    engine: Engine,
    trade_date: dt.date,
    sector_codes: list[str],
) -> pd.DataFrame:
    """Stock constituents for KPL concept sectors on the given date."""
    _COLS = ["sector_code", "ts_code", "name", "hot_num", "description"]
    if not sector_codes:
        return pd.DataFrame(columns=_COLS)
    sql = f"""
        SELECT con_code AS sector_code, ts_code, name, hot_num, description
        FROM {SCHEMA}.raw_kpl_concept_cons
        WHERE trade_date = :d AND con_code = ANY(:codes)
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "codes": sector_codes}).fetchall()
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows, columns=_COLS)


def _load_sw_members(
    engine: Engine,
    trade_date: dt.date,
    sector_codes: list[str],
) -> pd.DataFrame:
    """Stock constituents for SW L2 sectors using PIT-correct monthly snapshot."""
    _COLS = ["sector_code", "ts_code", "name"]
    if not sector_codes:
        return pd.DataFrame(columns=_COLS)
    snapshot_month = trade_date.replace(day=1)
    sql = f"""
        SELECT l2_code AS sector_code, ts_code, name
        FROM {SCHEMA}.sw_member_monthly
        WHERE snapshot_month = :sm
          AND l2_code = ANY(:codes)
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"sm": snapshot_month, "codes": sector_codes}).fetchall()
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows, columns=_COLS)


def _load_stock_data(
    engine: Engine,
    trade_date: dt.date,
    ts_codes: list[str],
) -> pd.DataFrame:
    """Load raw_daily + raw_moneyflow + raw_daily_basic for the candidate stocks."""
    if not ts_codes:
        return pd.DataFrame()
    sql = f"""
        SELECT
            d.ts_code, d.pct_chg, d.amount, d.close, d.vol,
            db.turnover_rate, db.total_mv, db.circ_mv,
            mf.buy_elg_amount, mf.sell_elg_amount,
            mf.net_mf_amount
        FROM {SCHEMA}.raw_daily d
        LEFT JOIN {SCHEMA}.raw_daily_basic db
               ON db.ts_code = d.ts_code AND db.trade_date = d.trade_date
        LEFT JOIN {SCHEMA}.raw_moneyflow mf
               ON mf.ts_code = d.ts_code AND mf.trade_date = d.trade_date
        WHERE d.trade_date = :d AND d.ts_code = ANY(:codes)
    """
    _COLS = ["ts_code", "pct_chg", "amount", "close", "vol",
             "turnover_rate", "total_mv", "circ_mv",
             "buy_elg_amount", "sell_elg_amount", "net_mf_amount"]
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "codes": ts_codes}).fetchall()
    if not rows:
        return pd.DataFrame(columns=_COLS + ["elg_net"])
    df = pd.DataFrame(rows, columns=_COLS)
    for c in df.columns:
        if c == "ts_code":
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["elg_net"] = df["buy_elg_amount"].fillna(0) - df["sell_elg_amount"].fillna(0)
    return df


def _load_limit_status(
    engine: Engine,
    trade_date: dt.date,
    ts_codes: list[str],
) -> pd.DataFrame:
    """Limit-up status with N-board info from raw_limit_list_d + raw_kpl_list."""
    if not ts_codes:
        return pd.DataFrame()
    sql = f"""
        SELECT
            l.ts_code,
            l.limit_ AS limit_state,
            l.limit_times,
            l.open_times,
            k.lu_desc,
            k.theme,
            k.tag
        FROM {SCHEMA}.raw_limit_list_d l
        LEFT JOIN {SCHEMA}.raw_kpl_list k
               ON k.ts_code = l.ts_code AND k.trade_date = l.trade_date
        WHERE l.trade_date = :d AND l.ts_code = ANY(:codes)
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "codes": ts_codes}).fetchall()
    _COLS = ["ts_code", "limit_state", "limit_times", "open_times", "lu_desc", "theme", "tag"]
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows, columns=_COLS)


def _load_top_inst_codes(
    engine: Engine,
    trade_date: dt.date,
    ts_codes: list[str],
) -> set[str]:
    """Return the set of ts_codes that had institutional seats on the
    dragon-tiger list today (买卖任一方有机构席位)."""
    if not ts_codes:
        return set()
    sql = f"""
        SELECT DISTINCT ts_code
        FROM {SCHEMA}.raw_top_inst
        WHERE trade_date = :d AND ts_code = ANY(:codes)
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "codes": ts_codes}).fetchall()
    return {r[0] for r in rows}


# ── Scoring ───────────────────────────────────────────────────────────────────

def _parse_consecutive_boards(lu_desc: str | None) -> int:
    """Parse '3连板' → 3, '一字板' → 1, '首板' → 1, None/NaN → 0."""
    if not lu_desc or not isinstance(lu_desc, str):
        return 0
    s = lu_desc.strip()
    if "连板" in s:
        try:
            return int(s.split("连板")[0])
        except ValueError:
            return 1
    if s in ("首板", "一字板"):
        return 1
    return 0


def _limit_bonus(
    limit_state: str | None,
    limit_times: int | None,
    open_times: int | None,
    lu_desc: str | None,
) -> tuple[float, int]:
    """Compute limit-up bonus score (0–1) and parse N-board count.

    一字板 (one-word board, no opening) is the strongest emotion signal.
    """
    consec = _parse_consecutive_boards(lu_desc)

    if limit_state != "U":
        return (0.0, consec)

    bonus = 0.5  # baseline for any 涨停
    if isinstance(lu_desc, str) and "一字板" in lu_desc:
        bonus = 1.0
    elif consec >= 4:
        bonus = 0.95
    elif consec >= 3:
        bonus = 0.85
    elif consec == 2:
        bonus = 0.70
    # Penalize 烂板 (opened multiple times)
    if open_times and open_times >= 2:
        bonus *= 0.65
    return (bonus, consec)


def _score_sector_members(
    members_df: pd.DataFrame,
    stock_data: pd.DataFrame,
    limit_data: pd.DataFrame,
    top_inst: set[str],
    sector_pct_chg: float,
    weights: dict[str, float],
) -> pd.DataFrame:
    """Compute composite score for each stock in this sector.

    Returns a DataFrame indexed by ts_code with columns:
      composite_score, components, limit_bonus, consec, has_top_inst
    """
    df = members_df.merge(stock_data, on="ts_code", how="left")
    df = df.merge(limit_data, on="ts_code", how="left")
    df["has_top_inst"] = df["ts_code"].isin(top_inst)

    # Skip stocks with no price data (suspended / new listing)
    df = df[df["pct_chg"].notna()].copy()
    if df.empty:
        return df

    # ── Component normalization (within sector) ───────────────────────────────
    df["rs"] = df["pct_chg"] - sector_pct_chg
    # Cross-sectional ranks (0–1)
    df["rs_rank"] = df["rs"].rank(pct=True, na_option="keep").fillna(0.5)
    df["amount_rank"] = df["amount"].rank(pct=True, na_option="keep").fillna(0.5)
    df["elg_rank"] = df["elg_net"].rank(pct=True, na_option="keep").fillna(0.5)

    # Limit-up bonus + consecutive count parsing
    parsed = df.apply(
        lambda r: _limit_bonus(r["limit_state"], r["limit_times"],
                                r["open_times"], r["lu_desc"]),
        axis=1,
    )
    df["limit_bonus"] = [t[0] for t in parsed]
    df["consec_boards"] = [t[1] for t in parsed]

    # Top-inst bonus (binary)
    df["top_inst_bonus"] = df["has_top_inst"].astype(float)

    # ── Composite score ───────────────────────────────────────────────────────
    w_rs = weights.get("rs_weight", 0.30)
    w_amt = weights.get("amount_weight", 0.20)
    w_elg = weights.get("elg_weight", 0.20)
    w_lim = weights.get("limit_weight", 0.20)
    w_inst = weights.get("top_inst_weight", 0.10)

    df["composite_score"] = (
        df["rs_rank"] * w_rs
        + df["amount_rank"] * w_amt
        + df["elg_rank"] * w_elg
        + df["limit_bonus"] * w_lim
        + df["top_inst_bonus"] * w_inst
    )
    return df.sort_values("composite_score", ascending=False)


def _assign_roles(
    scored: pd.DataFrame,
    *,
    top_n: int,
) -> list[tuple[str, str, dict[str, Any]]]:
    """From the scored member DataFrame, return list of (ts_code, role, evidence).

    Logic:
      - rank 1 → 龙头 (always)
      - among rank 2..top_n: highest consec_boards → 情绪先锋 (if consec >= 2)
      - remaining without limit-up → 中军 (steady leaders)
    """
    out: list[tuple[str, str, dict[str, Any]]] = []
    if scored.empty:
        return out

    top = scored.head(top_n).copy()
    top = top.reset_index(drop=True)

    # 龙头: rank 1
    head = top.iloc[0]
    out.append((head["ts_code"], "龙头", {
        "rank_within_sector": 1,
        "composite_score": round(float(head["composite_score"]), 4),
        "rs": round(float(head["rs"]), 4),
        "amount_rank": round(float(head["amount_rank"]), 4),
        "elg_rank": round(float(head["elg_rank"]), 4),
        "limit_bonus": round(float(head["limit_bonus"]), 4),
        "consec_boards": int(head["consec_boards"]),
        "has_top_inst": bool(head["has_top_inst"]),
        "lu_desc": head["lu_desc"],
    }))

    # Among the rest, find the strongest emotion driver
    rest = top.iloc[1:].copy()
    if not rest.empty:
        rest_with_consec = rest[rest["consec_boards"] >= 2]
        if not rest_with_consec.empty:
            vanguard = rest_with_consec.sort_values(
                ["consec_boards", "composite_score"], ascending=False
            ).iloc[0]
            out.append((vanguard["ts_code"], "情绪先锋", {
                "rank_within_sector": int(rest.index[rest["ts_code"] == vanguard["ts_code"]][0]) + 2,
                "composite_score": round(float(vanguard["composite_score"]), 4),
                "consec_boards": int(vanguard["consec_boards"]),
                "lu_desc": vanguard["lu_desc"],
                "limit_bonus": round(float(vanguard["limit_bonus"]), 4),
            }))
            # Mark vanguard ts_code so we don't double-assign as 中军
            assigned = {head["ts_code"], vanguard["ts_code"]}
        else:
            assigned = {head["ts_code"]}

        # 中军: top tier without limit-up
        for _, r in rest.iterrows():
            if r["ts_code"] in assigned:
                continue
            if r["limit_state"] == "U":
                continue  # don't tag 涨停 as 中军 (中军 should be steady)
            if float(r["composite_score"]) < 0.50:
                continue  # not strong enough
            out.append((r["ts_code"], "中军", {
                "composite_score": round(float(r["composite_score"]), 4),
                "rs": round(float(r["rs"]), 4),
                "amount_rank": round(float(r["amount_rank"]), 4),
                "elg_rank": round(float(r["elg_rank"]), 4),
                "limit_state": r["limit_state"],
                "has_top_inst": bool(r["has_top_inst"]),
            }))

    return out


# ── Orchestrator ──────────────────────────────────────────────────────────────

def compute_leaders_for_date(
    engine: Engine,
    trade_date: dt.date,
    *,
    params: dict[str, Any],
) -> list[StockSignal]:
    """Identify 龙头/中军/情绪先锋 for each active sector.

    Note: a stock may appear in multiple sectors; we tag it under the *highest-
    role-confidence* sector via primary_sector_code.  Duplicates are de-duped
    by (ts_code, role) — the PK of stock_signals_daily.
    """
    leader_params = params.get("leader", {})
    top_n = int(leader_params.get("top_n", 5))
    weights = leader_params.get("weights", {})

    sectors = _load_active_sectors(engine, trade_date)
    if sectors.empty:
        log.info("[leader] no active sectors on %s", trade_date)
        return []

    dc_codes = sectors[sectors["sector_source"] == "dc"]["sector_code"].tolist()
    kpl_codes = sectors[sectors["sector_source"] == "kpl"]["sector_code"].tolist()
    sw_codes = sectors[sectors["sector_source"] == "sw_l2"]["sector_code"].tolist()

    dc_members = _load_dc_members(engine, trade_date, dc_codes)
    kpl_members = _load_kpl_members(engine, trade_date, kpl_codes)
    sw_members = _load_sw_members(engine, trade_date, sw_codes)

    if dc_members.empty and kpl_members.empty and sw_members.empty:
        log.warning("[leader] no member data for active sectors on %s", trade_date)
        return []

    # Combine all unique member ts_codes for bulk loading
    all_ts_codes = sorted(set(
        dc_members["ts_code"].tolist() if not dc_members.empty else []
    ) | set(
        kpl_members["ts_code"].tolist() if not kpl_members.empty else []
    ) | set(
        sw_members["ts_code"].tolist() if not sw_members.empty else []
    ))

    stock_data = _load_stock_data(engine, trade_date, all_ts_codes)
    limit_data = _load_limit_status(engine, trade_date, all_ts_codes)
    top_inst = _load_top_inst_codes(engine, trade_date, all_ts_codes)

    if stock_data.empty:
        log.warning("[leader] no stock data on %s", trade_date)
        return []

    # Sector-level pct_chg for relative-strength baseline
    sector_pct_map: dict[tuple[str, str], float] = {}
    sql_pct = f"""
        SELECT ts_code, pct_change FROM {SCHEMA}.raw_dc_index WHERE trade_date = :d
        UNION ALL
        SELECT ts_code, NULL FROM {SCHEMA}.raw_kpl_concept WHERE trade_date = :d
    """
    with engine.connect() as conn:
        for code, pct in conn.execute(text(sql_pct), {"d": trade_date}):
            sector_pct_map[(code, "dc")] = float(pct) if pct is not None else 0.0
            sector_pct_map[(code, "kpl")] = 0.0

    # SW L2: V2.1.2 — use L2's own pct_change from raw_sw_daily (backfilled
    # in V2.1.1), with L1 fallback for the ~6 deprecated L2 codes lacking rows.
    if sw_codes:
        sql_sw_pct = text(f"""
            SELECT sf.l2_code,
                   COALESCE(sw_l2.pct_change, sw_l1.pct_change) AS pct_change
            FROM {SCHEMA}.sector_moneyflow_sw_daily sf
            LEFT JOIN {SCHEMA}.raw_sw_daily sw_l2
                  ON sw_l2.ts_code = sf.l2_code
                 AND sw_l2.trade_date = sf.trade_date
            LEFT JOIN {SCHEMA}.raw_sw_daily sw_l1
                  ON sw_l1.ts_code = sf.l1_code
                 AND sw_l1.trade_date = sf.trade_date
            WHERE sf.trade_date = :d AND sf.l2_code = ANY(:codes)
        """)
        with engine.connect() as conn:
            for code, pct in conn.execute(sql_sw_pct, {"d": trade_date, "codes": sw_codes}):
                sector_pct_map[(code, "sw_l2")] = float(pct) if pct is not None else 0.0

    # Score each sector's members and assign roles
    output: list[StockSignal] = []
    seen_pairs: set[tuple[str, str]] = set()  # (ts_code, role) PK guard

    # Process by role-confidence priority so higher-confidence sector wins
    # when a stock is a member of multiple sectors
    sectors_sorted = sectors.sort_values(
        by=["role_confidence", "role"],
        key=lambda col: col.map({"high": 0, "medium": 1, "low": 2}).fillna(2)
                         if col.name == "role_confidence" else col,
        ascending=True,
    )

    for _, sec in sectors_sorted.iterrows():
        src = sec["sector_source"]
        sec_code = sec["sector_code"]
        sec_name = sec["sector_name"]
        if src == "dc":
            mem = dc_members[dc_members["sector_code"] == sec_code]
        elif src == "kpl":
            mem = kpl_members[kpl_members["sector_code"] == sec_code]
        else:  # sw_l2
            mem = sw_members[sw_members["sector_code"] == sec_code]
        if mem.empty:
            continue

        sector_pct = sector_pct_map.get((sec_code, src), 0.0)
        scored = _score_sector_members(
            members_df=mem[["ts_code", "name"]] if "name" in mem.columns else mem[["ts_code"]],
            stock_data=stock_data,
            limit_data=limit_data,
            top_inst=top_inst,
            sector_pct_chg=sector_pct,
            weights=weights,
        )
        if scored.empty:
            continue

        assignments = _assign_roles(scored, top_n=top_n)
        for ts_code, role, ev in assignments:
            key = (ts_code, role)
            if key in seen_pairs:
                continue  # higher-confidence sector already claimed this stock
            seen_pairs.add(key)

            # Look up the stock name + theme
            name_row = mem[mem["ts_code"] == ts_code]
            stock_name = name_row.iloc[0]["name"] if not name_row.empty else None
            theme_val = None
            lu_desc_val = None
            lim_row = limit_data[limit_data["ts_code"] == ts_code]
            if not lim_row.empty:
                theme_val = lim_row.iloc[0].get("theme")
                lu_desc_val = lim_row.iloc[0].get("lu_desc")

            ev["sector_code"] = sec_code
            ev["sector_source"] = src
            ev["sector_name"] = sec_name
            ev["sector_role"] = sec["role"]

            output.append(StockSignal(
                trade_date=trade_date,
                ts_code=ts_code,
                name=stock_name,
                primary_sector_code=sec_code,
                primary_sector_source=src,
                role=role,
                score=float(ev.get("composite_score", 0.0)),
                theme=theme_val,
                lu_desc=lu_desc_val,
                evidence=ev,
            ))

    log.info("[leader] %s: %d stock signals (龙头/中军/情绪先锋)", trade_date, len(output))
    return output


# ── DB write ──────────────────────────────────────────────────────────────────

def write_stock_signals(engine: Engine, signals: list[StockSignal]) -> int:
    """Upsert stock signals into smartmoney.stock_signals_daily."""
    if not signals:
        return 0

    sql = text(f"""
        INSERT INTO {SCHEMA}.stock_signals_daily (
            trade_date, ts_code, name,
            primary_sector_code, primary_sector_source,
            role, score, theme, lu_desc, evidence_json, computed_at
        ) VALUES (
            :trade_date, :ts_code, :name,
            :primary_sector_code, :primary_sector_source,
            :role, :score, :theme, :lu_desc, :evidence_json, now()
        )
        ON CONFLICT (trade_date, ts_code, role) DO UPDATE SET
            name                  = EXCLUDED.name,
            primary_sector_code   = EXCLUDED.primary_sector_code,
            primary_sector_source = EXCLUDED.primary_sector_source,
            score                 = EXCLUDED.score,
            theme                 = EXCLUDED.theme,
            lu_desc               = EXCLUDED.lu_desc,
            evidence_json         = EXCLUDED.evidence_json,
            computed_at           = now()
    """)
    def _nan_to_none(v: object) -> object:
        """Replace float NaN/Inf with None — catches pandas NaN leaking into dicts."""
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        if isinstance(v, dict):
            return {k: _nan_to_none(w) for k, w in v.items()}
        return v

    rows = [
        {
            "trade_date": s.trade_date,
            "ts_code": s.ts_code,
            "name": s.name,
            "primary_sector_code": s.primary_sector_code,
            "primary_sector_source": s.primary_sector_source,
            "role": s.role,
            "score": s.score,
            "theme": s.theme if isinstance(s.theme, str) else None,
            "lu_desc": s.lu_desc if isinstance(s.lu_desc, str) else None,
            "evidence_json": json.dumps(_nan_to_none(s.evidence), ensure_ascii=False),
        }
        for s in signals
    ]
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)
