"""ningbo evening report — main pipeline.

Daily flow (16:30 BJT, after smartmoney ETL):

    1. Resolve trade date (handle weekends/holidays)
    2. Load universe (200 calendar days lookback) + weekly bars
    3. Run 4 strategies → candidate pools
    4. Apply HeuristicScorer + select top-5 (per_strategy_cap=2)
    5. Insert today's recommendations + initialise outcomes
    6. Run tracking batch (updates existing in-progress recs)
    7. Detect today's alerts (stop_loss / take_profit triggered today)
    8. Fetch in-progress summary for tracking section
    9. LLM narrative for top picks (Phase 1.12 — same LLMClient as other families)
    10. Render HTML/PDF
"""
from __future__ import annotations

import datetime as dt
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sqlalchemy import text

from ifa.config import RunMode, get_settings
from ifa.core.db import get_engine
from ifa.core.llm import LLMClient
from ifa.core.report.output import output_dir_for_run
from ifa.core.report.run import (
    ReportRun, finalize_report_run, insert_report_run, insert_section,
)
from ifa.core.report.timezones import fmt_bjt, to_bjt, utc_now
from ifa.core.render.html import HtmlRenderer
from ifa.families.ningbo.data import load_universe, load_weekly_bars
from ifa.families.ningbo.signals.alerts import (
    detect_today_alerts, fetch_in_progress_summary,
)
from ifa.families.ningbo.signals.confidence import HeuristicScorer
from ifa.families.ningbo.signals.selection import select_top_n
from ifa.families.ningbo.ml.dual_scorer import (
    score_with_active_models, build_consensus_matrix,
)
from ifa.families.ningbo.strategies import (
    half_year_double, six_step, sniper, treasure_basin,
)
from ifa.families.ningbo.tracking.batch import (
    insert_recommendations, run_tracking_batch,
)
from ifa.families.ningbo.tracking.sparkline import (
    cum_returns_from_tracking, render_sparkline,
)

MARKET = "china_a"
REPORT_FAMILY = "ningbo"
REPORT_TYPE = "ningbo_evening"
SLOT = "evening"
TEMPLATE_VERSION = "v0.1.0-ningbo"
PROMPT_VERSION = "ningbo_v1.0"
# DB persists top-N picks per day (audit + replay + future ML reranking).
# Report display only shows the top REPORT_DISPLAY_TOP_N (currently 5).
TOP_N = 10
PER_STRATEGY_CAP = 3
REPORT_DISPLAY_TOP_N = 5

# ── Stock-name lookup helper (small in-memory cache for the run) ───────────────


def _load_names(engine, ts_codes: list[str]) -> dict[str, str]:
    """Fetch ts_code → display name from any table that carries the name field.

    Tries (in order): sw_member_monthly (most recent month), raw_kpl_list,
    raw_limit_list_d. Returns dict of available names; missing codes simply
    won't appear in the dict (caller should fall back to ts_code).
    """
    if not ts_codes:
        return {}
    codes = list(set(ts_codes))
    out: dict[str, str] = {}

    sources = [
        # (table, name_col, date_col)
        ("smartmoney.sw_member_monthly", "name", "snapshot_month"),
        ("smartmoney.raw_kpl_list", "name", "trade_date"),
        ("smartmoney.raw_limit_list_d", "name", "trade_date"),
        ("smartmoney.stock_signals_daily", "name", "trade_date"),
    ]
    with engine.connect() as c:
        for tbl, name_col, date_col in sources:
            missing = [c0 for c0 in codes if c0 not in out]
            if not missing:
                break
            try:
                sql = text(f"""
                    SELECT ts_code, {name_col} FROM (
                        SELECT ts_code, {name_col},
                               ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY {date_col} DESC) AS rn
                        FROM {tbl}
                        WHERE ts_code = ANY(:codes) AND {name_col} IS NOT NULL
                    ) x WHERE rn = 1
                """)
                rows = c.execute(sql, {"codes": missing}).fetchall()
                for r in rows:
                    if r[0] not in out:
                        out[r[0]] = r[1]
            except Exception:
                continue
    return out


def _fetch_close(engine, ts_codes: list[str], on_date: dt.date) -> dict[str, float]:
    if not ts_codes:
        return {}
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT ts_code, close FROM smartmoney.raw_daily
            WHERE ts_code = ANY(:codes) AND trade_date = :d
        """), {"codes": list(set(ts_codes)), "d": on_date}).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def _index_pct_chg(engine, on_date: dt.date) -> float:
    with engine.connect() as c:
        r = c.execute(text("""
            SELECT pct_chg FROM smartmoney.raw_index_daily
            WHERE ts_code='000001.SH' AND trade_date=:d
        """), {"d": on_date}).fetchone()
    return float(r[0]) if r and r[0] is not None else 0.0


# ── Builders ──────────────────────────────────────────────────────────────────


def _build_market_brief(
    engine, on_date: dt.date,
    universe_size: int, six_step_passed: int,
    sniper_n: int, basin_n: int, hyd_n: int,
) -> dict[str, Any]:
    """Section 1: short market briefing + scan funnel."""
    with engine.connect() as c:
        idx_row = c.execute(text("""
            SELECT close, pct_chg FROM smartmoney.raw_index_daily
            WHERE ts_code='000001.SH' AND trade_date=:d
        """), {"d": on_date}).fetchone()
        cs1000 = c.execute(text("""
            SELECT pct_chg FROM smartmoney.raw_index_daily
            WHERE ts_code='000852.SH' AND trade_date=:d
        """), {"d": on_date}).fetchone()
        gem = c.execute(text("""
            SELECT pct_chg FROM smartmoney.raw_index_daily
            WHERE ts_code='399006.SZ' AND trade_date=:d
        """), {"d": on_date}).fetchone()

    return {
        "key": "ningbo.s1_brief",
        "title": "今日市场简报与扫描漏斗",
        "order": 1,
        "type": "ningbo_market_brief",
        "content_json": {
            "index_close": float(idx_row[0]) if idx_row and idx_row[0] is not None else None,
            "index_pct_chg": float(idx_row[1]) if idx_row and idx_row[1] is not None else 0.0,
            "cs1000_pct_chg": float(cs1000[0]) if cs1000 and cs1000[0] is not None else None,
            "gem_pct_chg": float(gem[0]) if gem and gem[0] is not None else None,
            "funnel": {
                "universe_size": int(universe_size),
                "six_step_pass": int(six_step_passed),
                "sniper": int(sniper_n),
                "basin": int(basin_n),
                "hyd": int(hyd_n),
            },
            "notes": (
                "宁波短线策略报告基于纯 EOD 数据生成。"
                "推荐目标持仓 5-15 个交易日，止盈 +20% 或跌破 24 日生命线立即止损。"
            ),
        },
    }


def _build_today_recs_section(
    heuristic_picks: pd.DataFrame, ml_picks: pd.DataFrame | None,
    rec_date: dt.date, names: dict[str, str], prices: dict[str, float],
) -> dict[str, Any]:
    def to_pane(picks: pd.DataFrame | None, mode: str, label: str, version: str):
        recs_list = []
        if picks is not None and not picks.empty:
            # Report shows only the top REPORT_DISPLAY_TOP_N; DB has all TOP_N.
            picks_display = picks.head(REPORT_DISPLAY_TOP_N)
            for _, r in picks_display.iterrows():
                ts = r["ts_code"]
                meta = r["rec_signal_meta"] if isinstance(r["rec_signal_meta"], dict) else {}
                recs_list.append({
                    "ts_code": ts,
                    "name": names.get(ts, ""),
                    "strategy": r["strategy"],
                    "strategies_hit": r.get("strategies_hit", []),
                    "rec_price": prices.get(ts, 0.0),
                    "confidence_score": float(r["confidence_score"]),
                    "llm_narrative": r.get("llm_narrative") or "",
                })
        return {"scoring_mode": mode, "label": label, "param_version": version, "recs": recs_list}

    panes = [
        to_pane(heuristic_picks, "heuristic", "启发式 (Heuristic) Top 5", "heuristic_v1.0"),
        to_pane(ml_picks, "ml", "ML 评分 Top 5", "ml_v_TBD_phase3"),
    ]
    return {
        "key": "ningbo.s2_recs",
        "title": "今日推荐 (启发式 / ML 双板块)",
        "order": 2,
        "type": "ningbo_today_recs",
        "content_json": {"panes": panes},
    }


def _build_alerts_section(alerts_dict: dict, names: dict[str, str] | None = None) -> dict[str, Any]:
    sl = alerts_dict["stop_loss"].to_dict("records") if not alerts_dict["stop_loss"].empty else []
    tp = alerts_dict["take_profit"].to_dict("records") if not alerts_dict["take_profit"].empty else []
    # serialize dates to strings + attach name
    names = names or {}
    for lst in (sl, tp):
        for row in lst:
            if isinstance(row.get("rec_date"), dt.date):
                row["rec_date"] = row["rec_date"].isoformat()
            row["name"] = names.get(row.get("ts_code"), "")
            for k, v in row.items():
                if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
                    row[k] = float(v)
    return {
        "key": "ningbo.s3_alerts",
        "title": "持仓警报 (今日触发的止损 / 止盈)",
        "order": 3,
        "type": "ningbo_alerts",
        "content_json": {"stop_loss": sl, "take_profit": tp},
    }


def _build_tracking_consensus_section(
    engine, on_date: dt.date, names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build by-date click-to-expand tracking — top-5 consensus per past day.

    For each past rec_date in the 15-day window:
      1. Pull all picks (all 3 scoring_modes)
      2. Compute per-track ranks (within each mode, sorted by confidence_score)
      3. Compute consensus star score per ts_code (same formula as today's matrix)
      4. Take top-5 by star score
      5. Render with sparkline + current cum_return + outcome status
    """
    from sqlalchemy import text as _text
    names = names or {}

    # Pull all recs from past 15 trading days, all scoring_modes
    earliest = on_date - dt.timedelta(days=30)  # ~15 trading days buffer
    sql = _text("""
        SELECT
            r.rec_date, r.ts_code, r.strategy, r.scoring_mode,
            r.rec_price, r.confidence_score,
            o.outcome_status, o.outcome_track_day,
            o.peak_cum_return, o.trough_cum_return, o.final_cum_return
        FROM ningbo.recommendations_daily r
        LEFT JOIN ningbo.recommendation_outcomes o USING (rec_date, ts_code, strategy, scoring_mode)
        WHERE r.rec_date >= :earliest AND r.rec_date <= :on_date
          AND r.scoring_mode IN ('heuristic', 'ml_aggressive', 'ml_conservative')
        ORDER BY r.rec_date DESC, r.scoring_mode, r.confidence_score DESC
    """)
    all_recs = pd.read_sql(sql, engine, params={
        "earliest": earliest, "on_date": on_date,
    })
    if all_recs.empty:
        return {
            "key": "ningbo.s4_tracking_consensus",
            "title": "近 15 交易日推荐复盘 (按日期展开 · 仅 top-5 共识)",
            "order": 4, "type": "ningbo_tracking_consensus",
            "content_json": {"by_date": []},
        }

    # Per-track rank within each (rec_date, scoring_mode), sorted by confidence DESC
    all_recs["rank_in_track"] = (
        all_recs.groupby(["rec_date", "scoring_mode"])["confidence_score"]
                .rank(method="min", ascending=False).astype(int)
    )

    by_date_payload: list[dict] = []

    for rd, day_df in all_recs.groupby("rec_date", sort=False):
        day_df = day_df.copy()
        # Compute consensus score per ts_code
        score_per_ts: dict[str, dict] = {}
        for _, r in day_df.iterrows():
            ts = r["ts_code"]
            mode = r["scoring_mode"]
            rk = int(r["rank_in_track"])
            if rk > 5:
                continue   # top-5 only contributes to consensus
            entry = score_per_ts.setdefault(ts, {
                "ts_code": ts,
                "rank_heuristic": None, "rank_aggressive": None, "rank_conservative": None,
                "score_total": 0,
                "rec_price": float(r["rec_price"]),
                "outcome_status": r["outcome_status"] or "in_progress",
                "outcome_track_day": int(r["outcome_track_day"]) if pd.notna(r["outcome_track_day"]) else None,
                "peak_cum_return": float(r["peak_cum_return"]) if pd.notna(r["peak_cum_return"]) else 0.0,
                "final_cum_return": float(r["final_cum_return"]) if pd.notna(r["final_cum_return"]) else 0.0,
                "strategies": set(),
            })
            entry["strategies"].add(r["strategy"])
            entry["score_total"] += max(0, 6 - rk)
            if mode == "heuristic":      entry["rank_heuristic"]    = rk
            elif mode == "ml_aggressive":   entry["rank_aggressive"]   = rk
            elif mode == "ml_conservative": entry["rank_conservative"] = rk

        if not score_per_ts:
            continue

        # Top-5 by score
        top5 = sorted(score_per_ts.values(), key=lambda x: x["score_total"], reverse=True)[:5]

        # Compute stars (same formula as today's matrix)
        for p in top5:
            sc = p["score_total"]
            if   sc >= 13: p["stars"] = 5
            elif sc >= 10: p["stars"] = 4
            elif sc >= 7:  p["stars"] = 3
            elif sc >= 4:  p["stars"] = 2
            else:          p["stars"] = 1
            p["name"] = names.get(p["ts_code"], "")
            p["strategies"] = ",".join(sorted(p["strategies"]))

        # Per-day summary
        n_take_profit  = sum(1 for p in top5 if p["outcome_status"] == "take_profit")
        n_stop_loss    = sum(1 for p in top5 if p["outcome_status"] == "stop_loss")
        n_expired      = sum(1 for p in top5 if p["outcome_status"] == "expired")
        n_in_progress  = sum(1 for p in top5 if p["outcome_status"] == "in_progress")

        by_date_payload.append({
            "rec_date": rd.isoformat() if hasattr(rd, "isoformat") else str(rd),
            "n_picks": len(top5),
            "summary": {
                "take_profit": n_take_profit,
                "stop_loss":   n_stop_loss,
                "expired":     n_expired,
                "in_progress": n_in_progress,
                "max_stars":   max((p["stars"] for p in top5), default=0),
            },
            "picks": top5,
        })

    return {
        "key": "ningbo.s4_tracking_consensus",
        "title": "近 15 交易日推荐复盘 (按日期展开 · 仅 top-5 共识)",
        "order": 4,
        "type": "ningbo_tracking_consensus",
        "content_json": {"by_date": by_date_payload},
    }


def _build_tracking_section(engine, on_date: dt.date) -> dict[str, Any]:
    """LEGACY 3-pane tracking by scoring_mode (kept for backward compat)."""
    in_prog = fetch_in_progress_summary(engine, on_date)

    def make_pane(mode: str, label: str):
        sub = in_prog[in_prog["scoring_mode"] == mode] if not in_prog.empty else pd.DataFrame()
        if sub.empty:
            return {"scoring_mode": mode, "label": label, "recs": [], "summary": None}

        # DB now stores TOP_N=10 per day, but the recap display limits to the
        # top-5 by confidence_score within each origin rec_date — keeps the
        # report readable.  rank 6-10 is still in DB for audit/replay.
        sub = sub.copy()
        sub["_rank_in_day"] = sub.groupby("rec_date")["confidence_score"].rank(
            method="min", ascending=False
        )
        sub = sub[sub["_rank_in_day"] <= REPORT_DISPLAY_TOP_N].drop(columns=["_rank_in_day"])

        # Fetch tracking rows for these recs and render sparkline per row
        keys = sub[["rec_date", "ts_code", "strategy", "scoring_mode"]].to_dict("records")
        if not keys:
            return {"scoring_mode": mode, "label": label, "recs": [], "summary": None}

        with engine.connect() as c:
            # one query for all tracking rows
            rec_dates = list({k["rec_date"] for k in keys})
            ts_codes = list({k["ts_code"] for k in keys})
            track_rows = c.execute(text("""
                SELECT rec_date, ts_code, strategy, scoring_mode, track_day,
                       track_date, cum_return
                FROM ningbo.recommendation_tracking
                WHERE rec_date = ANY(:rd) AND ts_code = ANY(:tc)
                  AND scoring_mode = :sm
                ORDER BY rec_date, ts_code, strategy, track_day
            """), {"rd": rec_dates, "tc": ts_codes, "sm": mode}).fetchall()

        # group tracking rows by (rec_date, ts_code, strategy)
        track_lookup: dict[tuple, list[dict]] = {}
        for tr in track_rows:
            k = (tr[0], tr[1], tr[2])
            track_lookup.setdefault(k, []).append({
                "track_day": tr[4], "track_date": tr[5], "cum_return": tr[6],
            })

        recs_list = []
        summary = {"total": 0, "in_progress": 0, "stop_loss": 0, "take_profit": 0, "expired": 0}
        for _, r in sub.iterrows():
            k = (r["rec_date"], r["ts_code"], r["strategy"])
            tracks = track_lookup.get(k, [])
            cum_returns = cum_returns_from_tracking(tracks, expected_days=15)
            terminal_status = r["outcome_status"] if r["outcome_status"] != "in_progress" else None
            terminal_day = int(r["outcome_track_day"]) if (
                terminal_status and r["outcome_track_day"] is not None
            ) else None
            spark = render_sparkline(
                cum_returns,
                terminal_status=terminal_status,
                terminal_track_day=terminal_day,
            )

            # Current cum_return = last non-None in cum_returns; fallback to 0
            current_cum = next((v for v in reversed(cum_returns) if v is not None), 0.0)
            track_day = max((t["track_day"] for t in tracks), default=0)

            recs_list.append({
                "rec_date": r["rec_date"],
                "ts_code": r["ts_code"],
                "strategy": r["strategy"],
                "rec_price": float(r["rec_price"]),
                "sparkline_svg": spark,
                "current_cum_return": float(current_cum),
                "peak_cum_return": float(r["peak_cum_return"]) if r["peak_cum_return"] is not None else 0.0,
                "track_day": int(track_day),
                "outcome_status": r["outcome_status"],
            })

            summary["total"] += 1
            summary[r["outcome_status"]] = summary.get(r["outcome_status"], 0) + 1

        return {
            "scoring_mode": mode,
            "label": label,
            "recs": recs_list,
            "summary": summary,
        }

    panes = [
        make_pane("heuristic",        "启发式  (传统对照)"),
        make_pane("ml_aggressive",    "ML 激进  (高收益)"),
        make_pane("ml_conservative",  "ML 稳健  (高 Sharpe)"),
    ]
    return {
        "key": "ningbo.s4_tracking",
        "title": "近 15 交易日推荐复盘追踪",
        "order": 4,
        "type": "ningbo_tracking",
        "content_json": {"panes": panes},
    }


# ── Dual-track scoring helper ────────────────────────────────────────────────

def _score_dual_track(
    *, engine, candidates_by_strategy: dict[str, pd.DataFrame],
    report_date: dt.date, on_log: Callable[[str], None],
) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None]:
    """Score all candidates 3 ways → top-N per track + consensus matrix.

    Returns (top_heuristic, top_ml_aggressive, top_ml_conservative, consensus_df).
    Any of the ML returns can be None if the corresponding slot has no active model.
    """
    # 1. Heuristic: existing pipeline with resonance boost
    heur_scorer = HeuristicScorer(version="v1.0")
    top_h = select_top_n(
        candidates_by_strategy, heur_scorer,
        top_n=TOP_N, per_strategy_cap=PER_STRATEGY_CAP,
    )
    on_log(f"  heuristic top-{TOP_N}: {len(top_h)} picks")

    # 2. Build unified per-strategy candidate DataFrame for ML scoring
    rows: list[dict] = []
    close_lookup = _close_lookup_from_strategies(candidates_by_strategy, engine, report_date)
    for strat_name, df in candidates_by_strategy.items():
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            ts = r["ts_code"]
            rows.append({
                "ts_code": ts,
                "strategy": strat_name,
                "confidence_score": float(r["confidence_score"]),
                "rec_signal_meta": r.get("signal_meta", {}) or {},
                "rec_price": close_lookup.get(ts, 0.0),
            })
    candidates_df = pd.DataFrame(rows)
    if candidates_df.empty:
        return top_h, None, None, None

    # 3. Score with both ML slots (loads from registry)
    on_log(f"  scoring with active ML models on {len(candidates_df)} candidates…")
    scores_by_track = score_with_active_models(engine, candidates_df, report_date)

    # 4. Top-N per ML track via existing select_top_n with custom scorer wrapper
    top_a = _picks_from_ml_scores(
        candidates_df, scores_by_track.get("ml_aggressive"),
        scoring_mode="ml_aggressive",
    )
    top_c = _picks_from_ml_scores(
        candidates_df, scores_by_track.get("ml_conservative"),
        scoring_mode="ml_conservative",
    )
    on_log(f"  aggressive top-{TOP_N}: {len(top_a) if top_a is not None else 0} picks  |  "
           f"conservative top-{TOP_N}: {len(top_c) if top_c is not None else 0} picks")

    # 5. Consensus matrix combining 3 tracks
    cm = build_consensus_matrix(
        candidates_df, scores_by_track,
        top_n=REPORT_DISPLAY_TOP_N, per_strategy_cap=PER_STRATEGY_CAP,
    )
    on_log(f"  consensus matrix: {len(cm)} unique stocks across 3 tracks")
    return top_h, top_a, top_c, cm


def _close_lookup_from_strategies(
    candidates_by_strategy: dict[str, pd.DataFrame],
    engine, report_date: dt.date,
) -> dict[str, float]:
    all_codes = set()
    for df in candidates_by_strategy.values():
        if df is not None and not df.empty:
            all_codes.update(df["ts_code"].tolist())
    return _fetch_close(engine, list(all_codes), report_date) if all_codes else {}


def _picks_from_ml_scores(
    candidates_df: pd.DataFrame, scores: Any, *, scoring_mode: str,
) -> pd.DataFrame | None:
    """Apply per-strategy cap top-N selection on ML scores.

    Each (ts_code, strategy) row competes individually. If a stock appears
    multiple times (multi-strategy), the highest-scored row is kept and we
    label its strategy as 'multi'.
    """
    if scores is None or candidates_df.empty:
        return None

    df = candidates_df.copy()
    df["_score"] = scores
    df = df.sort_values("_score", ascending=False)

    picks: list[dict] = []
    seen_ts: dict[str, int] = {}     # ts_code → picks index
    per_strat: dict[str, int] = {}
    for _, r in df.iterrows():
        ts = r["ts_code"]
        if ts in seen_ts:
            continue
        s = r["strategy"]
        if per_strat.get(s, 0) >= PER_STRATEGY_CAP:
            continue
        # Detect multi-strategy: same ts appears under other strategies in candidate pool
        all_strats = candidates_df[candidates_df["ts_code"] == ts]["strategy"].unique().tolist()
        is_multi = len(all_strats) >= 2
        picks.append({
            "ts_code": ts,
            "strategy": "multi" if is_multi else s,
            "strategies_hit": sorted(all_strats),
            "confidence_score": float(r["_score"]),
            "scoring_mode": scoring_mode,
            "param_version": f"{scoring_mode}_v1",
            "rec_signal_meta": {
                "by_strategy": {
                    str(strat): {
                        "raw_score": float(candidates_df[
                            (candidates_df["ts_code"] == ts) &
                            (candidates_df["strategy"] == strat)
                        ]["confidence_score"].iloc[0]),
                        "signal_meta": candidates_df[
                            (candidates_df["ts_code"] == ts) &
                            (candidates_df["strategy"] == strat)
                        ]["rec_signal_meta"].iloc[0],
                    } for strat in all_strats
                },
                "strategies_hit": sorted(all_strats),
                "n_hits": len(all_strats),
                "ml_score": float(r["_score"]),
                "best_individual_score": float(candidates_df[
                    candidates_df["ts_code"] == ts
                ]["confidence_score"].max()),
            },
        })
        seen_ts[ts] = len(picks) - 1
        per_strat[s] = per_strat.get(s, 0) + 1
        if len(picks) >= TOP_N:
            break

    return pd.DataFrame(picks)


def _build_consensus_section(
    consensus_df: pd.DataFrame, names: dict[str, str],
) -> dict[str, Any]:
    """★1-★5 consensus matrix table — main user-facing recommendation."""
    rows = []
    for _, r in consensus_df.iterrows():
        ts = r["ts_code"]
        rows.append({
            "ts_code": ts,
            "name": names.get(ts, ""),
            "strategies": r["strategies"],
            "rank_heuristic":    int(r["rank_heuristic"])    if pd.notna(r["rank_heuristic"])    else None,
            "rank_aggressive":   int(r["rank_aggressive"])   if pd.notna(r["rank_aggressive"])   else None,
            "rank_conservative": int(r["rank_conservative"]) if pd.notna(r["rank_conservative"]) else None,
            "stars": int(r["stars"]),
            "score_total": int(r["score_total"]),
            "rec_price": float(r["rec_price"]),
        })
    return {
        "key": "ningbo.s2c_consensus",
        "title": "今日推荐共识矩阵 (★ 越多越强)",
        "order": 2,
        "type": "ningbo_consensus",
        "content_json": {"rows": rows},
    }


def _build_disclaimer_section() -> dict[str, Any]:
    return {
        "key": "ningbo.s5_disclaimer",
        "title": "风险提示与免责声明",
        "order": 5,
        "type": "disclaimer",
        "content_json": {
            "items": [
                "本报告基于 EOD 数据生成的算法信号，仅供研究参考。",
                "宁波派短线打法风险较高，回撤可观；推荐止损纪律必须严格执行。",
                "持仓周期 5-15 个交易日，单只目标累计 +20% 或跌破 24 日均线立即离场。",
                "本报告不构成投资建议。",
            ],
        },
    }


# ── Main entry ────────────────────────────────────────────────────────────────


def run_ningbo_evening(
    *,
    report_date: dt.date,
    data_cutoff_at: dt.datetime,
    user: str = "default",
    triggered_by: str | None = None,
    scoring_modes: tuple[str, ...] = ("heuristic",),
    on_log: Callable[[str], None] = lambda m: None,
) -> Path:
    """Run the ningbo evening report end-to-end. Returns saved Path."""
    settings = get_settings()
    engine = get_engine(settings)
    llm = LLMClient(settings)

    run = ReportRun(
        report_run_id=uuid.uuid4(),
        market=MARKET, report_family=REPORT_FAMILY, report_type=REPORT_TYPE,
        report_date=report_date, slot=SLOT, timezone_name="Asia/Shanghai",
        data_cutoff_at=data_cutoff_at,
        run_mode=settings.run_mode,
        template_version=TEMPLATE_VERSION,
        prompt_version=PROMPT_VERSION,
        triggered_by=triggered_by or settings.run_mode.value,
    )
    insert_report_run(engine, run)
    on_log(f"[run {str(run.report_run_id)[:8]}] starting Ningbo evening for {report_date}")

    try:
        # ── 1. Load data ──────────────────────────────────────────────────
        on_log("loading universe + weekly bars (lookback 200 calendar days)…")
        t0 = time.monotonic()
        universe = load_universe(engine, report_date, lookback_days=200)
        on_log(f"  universe: {len(universe):,} rows, {universe['ts_code'].nunique():,} stocks "
               f"({time.monotonic()-t0:.1f}s)")

        codes = universe["ts_code"].dropna().unique().tolist()
        weekly = load_weekly_bars(engine, codes, report_date, lookback_weeks=40)
        on_log(f"  weekly: {len(weekly):,} bars ({time.monotonic()-t0:.1f}s total)")

        # ── 2. Run strategies ─────────────────────────────────────────────
        on_log("running strategies…")
        t1 = time.monotonic()
        sniper_df = sniper.detect_signals(universe, report_date)
        on_log(f"  sniper: {len(sniper_df)} signals ({time.monotonic()-t1:.1f}s)")

        t2 = time.monotonic()
        basin_df = treasure_basin.detect_signals(universe, report_date)
        on_log(f"  basin: {len(basin_df)} signals ({time.monotonic()-t2:.1f}s)")

        t3 = time.monotonic()
        hyd_df = half_year_double.detect_signals(universe, weekly, report_date)
        on_log(f"  half_year_double: {len(hyd_df)} signals ({time.monotonic()-t3:.1f}s)")

        # six_step is informational only — count for scan funnel
        six_step_passed = 0
        try:
            six_df = six_step.screen(universe, report_date, min_steps_passed=4)
            six_step_passed = len(six_df)
        except Exception as exc:
            on_log(f"  ⚠️ six_step.screen failed: {exc}")

        # ── 3. Score + select top-5 per scoring mode ──────────────────────
        on_log("scoring + selection…")
        candidates_by_strategy = {
            "sniper": sniper_df, "treasure_basin": basin_df, "half_year_double": hyd_df,
        }

        results_by_mode: dict[str, pd.DataFrame] = {}
        consensus_matrix: pd.DataFrame | None = None
        for mode in scoring_modes:
            if mode == "heuristic":
                scorer = HeuristicScorer(version="v1.0")
                top = select_top_n(
                    candidates_by_strategy, scorer,
                    top_n=TOP_N, per_strategy_cap=PER_STRATEGY_CAP,
                )
                results_by_mode[mode] = top
                on_log(f"  heuristic top-{TOP_N}: {len(top)} picks")
            elif mode == "dual":
                # Build top-N for all 3 tracks (heuristic + 2 ML) + consensus matrix
                top_h, top_a, top_c, cm = _score_dual_track(
                    engine=engine, candidates_by_strategy=candidates_by_strategy,
                    report_date=report_date, on_log=on_log,
                )
                results_by_mode["heuristic"]       = top_h
                if top_a is not None:
                    results_by_mode["ml_aggressive"]   = top_a
                if top_c is not None:
                    results_by_mode["ml_conservative"] = top_c
                consensus_matrix = cm
            elif mode == "ml":
                on_log("  ⚠️ legacy 'ml' mode — use 'dual' instead; skipping")
                results_by_mode[mode] = pd.DataFrame()

        # ── 4. Names + prices for picked stocks ───────────────────────────
        all_picked_codes = []
        for top in results_by_mode.values():
            if not top.empty:
                all_picked_codes.extend(top["ts_code"].tolist())
        names = _load_names(engine, all_picked_codes)
        prices = _fetch_close(engine, all_picked_codes, report_date)

        # ── 5. LLM narrative for each pick (sequential, per spec) ─────────
        market_ctx = {"index_pct_chg": _index_pct_chg(engine, report_date)}
        from ifa.families.ningbo.llm.narrative import generate_narrative
        for mode, top in results_by_mode.items():
            if top.empty:
                continue
            narratives = []
            for _, r in top.iterrows():
                rec_for_llm = r.to_dict()
                rec_for_llm["rec_price"] = prices.get(r["ts_code"], 0.0)
                rec_for_llm["name"] = names.get(r["ts_code"], "")
                t_n = time.monotonic()
                narr = generate_narrative(rec_for_llm, llm_client=llm, market_context=market_ctx)
                on_log(f"  narrative {r['ts_code']} ({mode}): {len(narr)} chars in {time.monotonic()-t_n:.1f}s")
                narratives.append(narr)
            top["llm_narrative"] = narratives
            results_by_mode[mode] = top

        # ── 6. Insert recommendations + initialize outcomes ───────────────
        for mode, top in results_by_mode.items():
            if top.empty:
                continue
            n = insert_recommendations(
                engine, top, report_date,
                scoring_mode=mode,
                param_version=f"{mode}_v1.0" if mode == "heuristic" else f"{mode}_v_phase3",
            )
            on_log(f"  inserted {n} {mode} recommendations into DB")

        # ── 7. Tracking batch (updates in-progress recs from past dates) ──
        on_log("running tracking batch…")
        track_summary = run_tracking_batch(engine, report_date)
        on_log(f"  tracked {track_summary.n_tracking_rows_inserted} rows; "
               f"newly_sl={track_summary.newly_stop_loss}, "
               f"newly_tp={track_summary.newly_take_profit}, "
               f"newly_exp={track_summary.newly_expired}")

        # ── 8. Today's alerts ─────────────────────────────────────────────
        alerts = detect_today_alerts(engine, report_date)
        on_log(f"  alerts: stop_loss={len(alerts['stop_loss'])}, "
               f"take_profit={len(alerts['take_profit'])}")

        # Enrich names: include alert ts_codes + historical tracking ts_codes
        # so every code displayed has a Chinese name resolved.
        extra_codes: set[str] = set()
        for col in ("stop_loss", "take_profit"):
            if not alerts[col].empty:
                extra_codes.update(alerts[col]["ts_code"].tolist())
        from sqlalchemy import text as _text
        with engine.connect() as _c:
            _hist_codes = _c.execute(_text("""
                SELECT DISTINCT ts_code FROM ningbo.recommendations_daily
                WHERE rec_date >= :earliest AND rec_date <= :on_date
            """), {"earliest": report_date - dt.timedelta(days=30), "on_date": report_date}).fetchall()
        extra_codes.update(r[0] for r in _hist_codes)
        # Merge with already-loaded names; load the missing ones in one shot
        missing = [c for c in extra_codes if c not in names]
        if missing:
            extra_names = _load_names(engine, missing)
            names.update(extra_names)

        # ── 9. Assemble sections ──────────────────────────────────────────
        sections: list[dict[str, Any]] = []
        sections.append(_build_market_brief(
            engine, report_date, universe["ts_code"].nunique(),
            six_step_passed, len(sniper_df), len(basin_df), len(hyd_df),
        ))
        # If dual mode produced a consensus matrix, render it as the primary
        # recommendation section. Otherwise fall back to legacy single/heuristic.
        if consensus_matrix is not None and not consensus_matrix.empty:
            sections.append(_build_consensus_section(consensus_matrix, names))
        else:
            sections.append(_build_today_recs_section(
                results_by_mode.get("heuristic", pd.DataFrame()),
                results_by_mode.get("ml", pd.DataFrame()),
                report_date, names, prices,
            ))
        sections.append(_build_alerts_section(alerts, names))
        # New consensus-based tracking (replaces legacy 3-pane);
        # only top-5 per past date, click-to-expand by date.
        sections.append(_build_tracking_consensus_section(engine, report_date, names))
        sections.append(_build_disclaimer_section())

        # Persist sections
        for sec in sections:
            insert_section(
                engine, report_run_id=run.report_run_id,
                section_key=sec["key"], section_title=sec["title"],
                section_order=sec["order"], content_json=sec["content_json"],
                prompt_name="ningbo_v1",
                prompt_version=PROMPT_VERSION,
            )

        # ── 10. Render ────────────────────────────────────────────────────
        out_path = _render_and_save(run, sections, settings)
        finalize_report_run(engine, run, status="succeeded", output_html_path=out_path)
        on_log(f"saved → {out_path}")
        return out_path

    except Exception as exc:
        finalize_report_run(
            engine, run, status="failed",
            error_summary=f"{type(exc).__name__}: {exc}",
        )
        raise


def _render_and_save(run: ReportRun, sections: list[dict], settings) -> Path:
    renderer = HtmlRenderer()
    cutoff_bjt_str = fmt_bjt(run.data_cutoff_at)
    generated_bjt_str = fmt_bjt(utc_now(), "%Y-%m-%d %H:%M")
    report = {
        "title": f"中国 A 股宁波派短线策略报告 · {run.report_date.strftime('%Y年%m月%d日')}",
        "subtitle_en": "China A-Share Ningbo Short-Term Strategy Report — Lindenwood Management LLC",
        "report_date_bjt": run.report_date.strftime("%Y-%m-%d"),
        "data_cutoff_bjt": cutoff_bjt_str,
        "generated_at_bjt": generated_bjt_str,
        "template_version": TEMPLATE_VERSION,
        "run_mode": settings.report_badge,
        "report_run_id_short": str(run.report_run_id)[:8],
        "sections": sections,
    }
    html = renderer.render(report=report)
    out_root = output_dir_for_run(settings, run)
    bjt_now = to_bjt(utc_now())
    fname = f"CN_ningbo_evening_{run.report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.html"
    out_path = out_root / fname
    out_path.write_text(html, encoding="utf-8")
    return out_path
