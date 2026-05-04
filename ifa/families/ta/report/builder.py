"""Build the TA evening report — pulls regime / candidates / tracking / metrics
into a renderable dict.

Sections produced (skeleton — M7.5 will add LLM narrative + sector clustering):
  · 01 overview        — date, regime, breadth, total candidates
  · 03 top_5stars      — top 5★ rated candidates
  · 04 top_4stars      — top 4★ rated candidates (next tier)
  · 08 verification    — yesterday's candidates, T+1 outcome distribution
  · 10 setup_metrics   — rolling 60d/250d edge per setup
  · 16 disclaimer
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.calendar import prev_trading_day
from ifa.core.report.timezones import bjt_now, fmt_bjt

log = logging.getLogger(__name__)


def build_evening_report(engine: Engine, on_date: date) -> dict:
    sections: list[dict] = []
    sections.append(_section_overview(engine, on_date))
    sections.append(_section_market_state(engine, on_date))
    sections.append(_section_stars(engine, on_date, star_filter=5, title="§03 五星级候选"))
    sections.append(_section_stars(engine, on_date, star_filter=4, title="§04 四星级候选"))
    sections.append(_section_candidates_by_family(engine, on_date))
    sections.append(_section_verification(engine, on_date))
    sections.append(_section_metrics(engine, on_date))
    sections.append(_section_attribution(engine, on_date))
    sections.append(_section_risk_scan(engine, on_date))
    sections.append(_section_hypotheses(engine, on_date))
    sections.append(_section_disclaimer())

    return {
        "title": f"TA 晚报 · {on_date}",
        "report_date_bjt": fmt_bjt(bjt_now()),
        "trade_date": on_date.isoformat(),
        "sections": sections,
    }


def _section_overview(engine: Engine, on_date: date) -> dict:
    with engine.connect() as conn:
        regime_row = conn.execute(
            text("SELECT regime, confidence FROM ta.regime_daily WHERE trade_date = :d"),
            {"d": on_date},
        ).fetchone()
        cand_row = conn.execute(
            text("""SELECT COUNT(*) AS n,
                           COUNT(*) FILTER (WHERE in_top_watchlist) AS top_n,
                           COUNT(DISTINCT setup_name) AS active_setups
                    FROM ta.candidates_daily WHERE trade_date = :d"""),
            {"d": on_date},
        ).fetchone()
    return {
        "type": "overview",
        "trade_date": on_date.isoformat(),
        "regime": regime_row[0] if regime_row else "(未分类)",
        "regime_confidence": float(regime_row[1]) if regime_row and regime_row[1] is not None else None,
        "total_candidates": int(cand_row[0]) if cand_row else 0,
        "top_watchlist_count": int(cand_row[1]) if cand_row else 0,
        "active_setup_count": int(cand_row[2]) if cand_row else 0,
    }


def _section_stars(engine: Engine, on_date: date, *, star_filter: int, title: str) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ts_code, setup_name, rank, final_score, star_rating,
                       evidence_json
                FROM ta.candidates_daily
                WHERE trade_date = :d AND star_rating = :s
                ORDER BY rank
                LIMIT 30
            """),
            {"d": on_date, "s": star_filter},
        ).fetchall()
    return {
        "type": "candidate_list",
        "title": title,
        "stars": star_filter,
        "candidates": [
            {
                "ts_code": r[0],
                "setup_name": r[1],
                "rank": int(r[2]) if r[2] is not None else None,
                "score": float(r[3]) if r[3] is not None else None,
                "stars": int(r[4]) if r[4] is not None else None,
                "triggers": (r[5] or {}).get("triggers", []) if isinstance(r[5], dict) else [],
            }
            for r in rows
        ],
    }


def _section_verification(engine: Engine, on_date: date) -> dict:
    """Yesterday's top watchlist outcome at T+1."""
    try:
        prev = prev_trading_day(engine, on_date)
    except RuntimeError:
        prev = None

    if prev is None:
        return {"type": "verification", "title": "§08 验证回顾", "prev_date": None,
                "candidates": [], "summary": {}}

    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT c.ts_code, c.setup_name, c.rank, c.final_score,
                       t.return_pct, t.validation_status
                FROM ta.candidates_daily c
                LEFT JOIN ta.candidate_tracking t
                  ON t.candidate_id = c.candidate_id AND t.horizon_days = 1
                WHERE c.trade_date = :prev AND c.in_top_watchlist
                ORDER BY c.rank
            """),
            {"prev": prev},
        ).fetchall()
        summary = conn.execute(
            text("""
                SELECT t.validation_status, COUNT(*) AS n
                FROM ta.candidates_daily c
                JOIN ta.candidate_tracking t
                  ON t.candidate_id = c.candidate_id AND t.horizon_days = 1
                WHERE c.trade_date = :prev
                GROUP BY t.validation_status
            """),
            {"prev": prev},
        ).fetchall()
    return {
        "type": "verification",
        "title": "§08 验证回顾 (T+1)",
        "prev_date": prev.isoformat(),
        "candidates": [
            {
                "ts_code": r[0],
                "setup_name": r[1],
                "rank": int(r[2]) if r[2] is not None else None,
                "score": float(r[3]) if r[3] is not None else None,
                "return_pct": float(r[4]) if r[4] is not None else None,
                "status": r[5],
            }
            for r in rows
        ],
        "summary": {row[0]: int(row[1]) for row in summary},
    }


def _section_metrics(engine: Engine, on_date: date) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT setup_name, triggers_count, winrate_60d, avg_return_60d,
                       pl_ratio_60d, winrate_250d, decay_score
                FROM ta.setup_metrics_daily WHERE trade_date = :d
                ORDER BY winrate_60d DESC NULLS LAST
            """),
            {"d": on_date},
        ).fetchall()
    return {
        "type": "metrics_table",
        "title": "§10 Setup 滚动边际",
        "rows": [
            {
                "setup_name": r[0],
                "n": int(r[1]) if r[1] is not None else None,
                "winrate_60d": float(r[2]) if r[2] is not None else None,
                "avg_return_60d": float(r[3]) if r[3] is not None else None,
                "pl_ratio": float(r[4]) if r[4] is not None else None,
                "winrate_250d": float(r[5]) if r[5] is not None else None,
                "decay": float(r[6]) if r[6] is not None else None,
            }
            for r in rows
        ],
    }


def _section_market_state(engine: Engine, on_date: date) -> dict:
    """§02 市场状态盘 — breadth / amount / north flow / regime evidence."""
    with engine.connect() as conn:
        ms = conn.execute(text("""
            SELECT total_amount, amount_10d_avg, amount_percentile_60d,
                   up_count, down_count, flat_count,
                   limit_up_count, limit_down_count, max_consecutive_limit_up,
                   blow_up_count, blow_up_rate, market_state
            FROM smartmoney.market_state_daily WHERE trade_date = :d
        """), {"d": on_date}).fetchone()
        hsgt = conn.execute(text("""
            SELECT north_money FROM smartmoney.raw_moneyflow_hsgt WHERE trade_date = :d
        """), {"d": on_date}).fetchone()
        sse = conn.execute(text("""
            SELECT close, pct_chg FROM smartmoney.raw_index_daily
            WHERE ts_code = '000001.SH' AND trade_date = :d
        """), {"d": on_date}).fetchone()
    return {
        "type": "market_state",
        "title": "§02 市场状态盘",
        "sse_close": float(sse[0]) if sse and sse[0] is not None else None,
        "sse_pct_chg": float(sse[1]) if sse and sse[1] is not None else None,
        # market_state_daily.total_amount stored in 万元; 1 亿元 = 10000 万元
        "amount_yi_yuan": float(ms[0]) / 1e4 if ms and ms[0] else None,
        "amount_pct_60d": float(ms[2]) if ms and ms[2] is not None else None,
        "up_count": int(ms[3]) if ms and ms[3] is not None else None,
        "down_count": int(ms[4]) if ms and ms[4] is not None else None,
        "flat_count": int(ms[5]) if ms and ms[5] is not None else None,
        "limit_up": int(ms[6]) if ms and ms[6] is not None else None,
        "limit_down": int(ms[7]) if ms and ms[7] is not None else None,
        "consecutive_lb_high": int(ms[8]) if ms and ms[8] is not None else None,
        "blow_up_count": int(ms[9]) if ms and ms[9] is not None else None,
        "blow_up_rate": float(ms[10]) if ms and ms[10] is not None else None,
        "market_state": ms[11] if ms else None,
        "north_yi_yuan": float(hsgt[0]) / 1e4 if hsgt and hsgt[0] else None,  # 万元 → 亿元
    }


def _section_candidates_by_family(engine: Engine, on_date: date) -> dict:
    """§07 候选股池 — group by setup family (T/P/R/F/V/S/C)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT setup_name, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE star_rating >= 4) AS top_count,
                   AVG(final_score) AS avg_score
            FROM ta.candidates_daily
            WHERE trade_date = :d
            GROUP BY setup_name ORDER BY n DESC
        """), {"d": on_date}).fetchall()

    families: dict[str, dict] = {
        "T 趋势": {"setups": [], "n": 0, "top": 0},
        "P 回踩": {"setups": [], "n": 0, "top": 0},
        "R 反转": {"setups": [], "n": 0, "top": 0},
        "F 形态": {"setups": [], "n": 0, "top": 0},
        "V 量价": {"setups": [], "n": 0, "top": 0},
        "S 板块": {"setups": [], "n": 0, "top": 0},
        "C 筹码": {"setups": [], "n": 0, "top": 0},
    }
    family_map = {"T": "T 趋势", "P": "P 回踩", "R": "R 反转", "F": "F 形态",
                  "V": "V 量价", "S": "S 板块", "C": "C 筹码"}
    for setup_name, n, top, avg in rows:
        fam = family_map.get(setup_name[0])
        if not fam:
            continue
        families[fam]["setups"].append({
            "name": setup_name,
            "n": int(n),
            "top": int(top) if top is not None else 0,
            "avg_score": float(avg) if avg is not None else None,
        })
        families[fam]["n"] += int(n)
        families[fam]["top"] += int(top) if top is not None else 0
    return {
        "type": "family_grid",
        "title": "§07 候选股池（按 Setup 族）",
        "families": families,
    }


def _section_attribution(engine: Engine, on_date: date) -> dict:
    """§11 表现归因 — last 5 trade days candidate-to-T+1 performance per setup."""
    from ifa.core.calendar import trading_days_between
    from datetime import timedelta
    window_start = on_date - timedelta(days=14)
    days = trading_days_between(engine, window_start, on_date)
    start = days[-min(6, len(days))] if days else on_date

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT c.setup_name,
                   COUNT(*) AS n,
                   AVG(t.return_pct) AS avg_ret,
                   100.0 * COUNT(*) FILTER (WHERE t.validation_status = 'confirmed')
                       / NULLIF(COUNT(*), 0) AS win_rate
            FROM ta.candidates_daily c
            JOIN ta.candidate_tracking t
              ON t.candidate_id = c.candidate_id AND t.horizon_days = 1
            WHERE c.trade_date >= :start AND c.trade_date <= :on_date
            GROUP BY c.setup_name
            HAVING COUNT(*) >= 5
            ORDER BY win_rate DESC NULLS LAST
        """), {"start": start, "on_date": on_date}).fetchall()
    return {
        "type": "attribution",
        "title": f"§11 近 5 日表现归因",
        "window_start": start.isoformat(),
        "rows": [
            {
                "setup_name": r[0],
                "n": int(r[1]),
                "avg_return_pct": float(r[2]) if r[2] is not None else None,
                "win_rate": float(r[3]) if r[3] is not None else None,
            }
            for r in rows
        ],
    }


def _section_risk_scan(engine: Engine, on_date: date) -> dict:
    """§13 风险扫描 — C2 chip-loose + setup with bad decay + climax regime."""
    with engine.connect() as conn:
        c2_count = conn.execute(text("""
            SELECT COUNT(*) FROM ta.candidates_daily
            WHERE trade_date = :d AND setup_name = 'C2_CHIP_LOOSE'
        """), {"d": on_date}).scalar() or 0

        bad_decay = conn.execute(text("""
            SELECT setup_name, decay_score, winrate_60d
            FROM ta.setup_metrics_daily
            WHERE trade_date = :d AND decay_score IS NOT NULL AND decay_score <= -5
            ORDER BY decay_score
        """), {"d": on_date}).fetchall()

        regime_row = conn.execute(text(
            "SELECT regime FROM ta.regime_daily WHERE trade_date = :d"
        ), {"d": on_date}).fetchone()

        climax_warning = None
        if regime_row and regime_row[0] in ("emotional_climax", "distribution_risk"):
            climax_warning = (
                f"今日体制为 {regime_row[0]}，历史上后续 5-10 日多见急跌。"
                "建议减仓、不做新多头开仓。"
            )

    return {
        "type": "risk_scan",
        "title": "§13 风险扫描",
        "chip_loose_count": int(c2_count),
        "decaying_setups": [
            {
                "setup_name": r[0],
                "decay": float(r[1]),
                "winrate_60d": float(r[2]) if r[2] is not None else None,
            }
            for r in bad_decay
        ],
        "climax_warning": climax_warning,
    }


def _section_hypotheses(engine: Engine, on_date: date) -> dict:
    """§14 次日假设清单 — top 5★ candidates → record falsifiable judgments."""
    with engine.connect() as conn:
        cands = conn.execute(text("""
            SELECT ts_code, setup_name, final_score
            FROM ta.candidates_daily
            WHERE trade_date = :d AND star_rating = 5 AND in_top_watchlist
            ORDER BY rank LIMIT 5
        """), {"d": on_date}).fetchall()

    hypotheses = []
    for ts_code, setup_name, score in cands:
        hypotheses.append({
            "ts_code": ts_code,
            "setup_name": setup_name,
            "score": float(score) if score is not None else None,
            "statement": f"{ts_code} 触发 {setup_name}（{score:.2f}）将于 T+1 上涨 ≥ 2%",
            "horizon_days": 1,
            "threshold_pct": 2.0,
        })
    return {
        "type": "hypotheses",
        "title": "§14 次日假设（可证伪）",
        "hypotheses": hypotheses,
    }


def _section_disclaimer() -> dict:
    return {
        "type": "disclaimer",
        "title": "免责声明",
        "body": (
            "本报告由 iFA TA Family 算法生成，所有候选标的及指标均基于历史数据回测，"
            "不构成投资建议。技术分析存在固有局限性，市场变化迅速，过往表现不代表未来收益。"
            "投资者应结合自身情况独立判断、谨慎决策，自行承担投资风险。"
        ),
    }
