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
    sections.append(_section_stars(engine, on_date, star_filter=5, title="§03 五星级候选"))
    sections.append(_section_stars(engine, on_date, star_filter=4, title="§04 四星级候选"))
    sections.append(_section_verification(engine, on_date))
    sections.append(_section_metrics(engine, on_date))
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
