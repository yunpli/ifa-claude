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
from ifa.core.render.sparkline import sparkline_svg
from ifa.core.report.timezones import bjt_now, fmt_bjt

log = logging.getLogger(__name__)


_INDEX_PANEL = [
    ("000001.SH", "上证指数", "核心宽基 / 大盘风险偏好"),
    ("399001.SZ", "深证成指", "成长风格"),
    ("399006.SZ", "创业板指", "高 beta 成长"),
    ("000688.SH", "科创50", "硬科技 / 成长锐度"),
    ("899050.BJ", "北证50", "小市值活跃度"),
    ("000300.SH", "沪深300", "核心资产 / 外资偏好"),
]


def build_evening_report(engine: Engine, on_date: date,
                          *, augmenter=None) -> dict:
    sections: list[dict] = []
    overview = _section_overview(engine, on_date)
    index_panel = _section_index_panel(engine, on_date)
    market_state = _section_market_state(engine, on_date)
    methodology = _section_methodology()
    strategy_spotlight = _section_strategy_spotlight(engine, on_date)
    tier_a = _section_tier(engine, on_date, tier="A", title="§04 重点池 (Tier A)")
    tier_b = _section_tier(engine, on_date, tier="B", title="§05 候选池 (Tier B)")
    tier_c = _section_tier(engine, on_date, tier="C", title="§06 观察池 (Tier C)")
    s5 = _section_stars(engine, on_date, star_filter=5, title="§03 五星级候选")    # legacy fallback
    s4 = _section_stars(engine, on_date, star_filter=4, title="§04 四星级候选")
    fam = _section_candidates_by_family(engine, on_date)
    verify = _section_verification(engine, on_date)
    metrics = _section_metrics(engine, on_date)
    attribution = _section_attribution(engine, on_date)
    risk = _section_risk_scan(engine, on_date)
    hypotheses = _section_hypotheses(engine, on_date)
    disclaimer = _section_disclaimer()

    sections.extend([overview, index_panel, market_state])
    if augmenter is not None:
        narrative = augmenter.regime_explainer(
            regime=overview.get("regime"),
            confidence=overview.get("regime_confidence"),
            transitions=_load_transitions(engine, on_date),
        )
        if narrative:
            sections.append({"type": "narrative", "title": "§02-N 体制解读",
                             "body": narrative})
    sections.append(methodology)
    sections.extend([tier_a, tier_b, tier_c, strategy_spotlight])
    if augmenter is not None:
        narrative = augmenter.candidate_narrator(
            top5=[c for c in tier_a.get("candidates", [])][:5],
            top4=[c for c in tier_b.get("candidates", [])][:5],
        )
        if narrative:
            sections.append({"type": "narrative", "title": "§06-N 重点池解读",
                             "body": narrative})
    sections.extend([fam, verify, metrics, attribution, risk])
    if augmenter is not None:
        narrative = augmenter.strategy_review(
            attribution_rows=attribution.get("rows", []),
            decaying=risk.get("decaying_setups", []),
            chip_loose_count=risk.get("chip_loose_count", 0),
            climax_warning=risk.get("climax_warning"),
        )
        if narrative:
            sections.append({"type": "narrative", "title": "§13-N 策略评论",
                             "body": narrative})
    sections.extend([hypotheses, disclaimer])

    # Banner-level fields (consumed by template header)
    from ifa.config import get_settings
    settings = get_settings()

    return {
        "title": f"中国 A 股技术面晚盘报告 · {on_date.strftime('%Y 年 %m 月 %d 日')}",
        "subtitle_en": f"China A-Share Technical Analysis Evening Briefing · {on_date}",
        "report_date_bjt": fmt_bjt(bjt_now()),
        "trade_date": on_date.isoformat(),
        "template_version": "ta-v2.2",
        "slot": "evening",
        "run_mode": settings.run_mode.value,
        "overview": overview,    # banner consumes this directly (regime hero)
        "sections": sections,
    }


def _load_transitions(engine: Engine, on_date: date) -> dict:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT transitions_json FROM ta.regime_daily WHERE trade_date = :d
        """), {"d": on_date}).fetchone()
    if not row or not row[0]:
        return {}
    return row[0] if isinstance(row[0], dict) else {}


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


def _section_tier(engine: Engine, on_date: date, *, tier: str, title: str) -> dict:
    """Tier-based candidate list (A/B/C). Aggregates per stock."""
    sql = text("""
        SELECT ts_code, setup_name, rank, final_score, star_rating, evidence_json
        FROM ta.candidates_daily
        WHERE trade_date = :d AND evidence_json->>'tier' = :tier
        ORDER BY rank
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"d": on_date, "tier": tier}).fetchall()

    by_stock: dict[str, dict] = {}
    for r in rows:
        ts_code = r[0]
        ev = r[5] if isinstance(r[5], dict) else {}
        rec = by_stock.setdefault(ts_code, {
            "rank": int(r[2]) if r[2] is not None else 999,
            "stock_score": float(r[3]) if r[3] is not None else None,
            "stars": int(r[4]) if r[4] is not None else None,
            "resonance_count": ev.get("resonance_count"),
            "resonance_families": ev.get("resonance_families", []),
            "strategies": [],
        })
        rec["strategies"].append({
            "setup_name": r[1],
            "raw_score": float(ev.get("score", r[3])) if ev else (float(r[3]) if r[3] is not None else None),
        })

    sorted_stocks = sorted(by_stock.items(), key=lambda kv: kv[1]["rank"])
    names = _load_stock_names(engine, [ts for ts, _ in sorted_stocks])

    candidates = []
    for ts_code, rec in sorted_stocks:
        candidates.append({
            "ts_code": ts_code,
            "name": names.get(ts_code, ""),
            "rank": rec["rank"],
            "stock_score": rec["stock_score"],
            "stars": rec["stars"],
            "resonance_count": rec["resonance_count"] or len(rec["strategies"]),
            "resonance_families": rec["resonance_families"],
            "strategies": rec["strategies"],
        })
    return {
        "type": "tier_list",
        "tier": tier,
        "title": title,
        "candidates": candidates,
    }


_STRATEGY_DESCRIPTIONS: dict[str, str] = {
    "T1_BREAKOUT": "突破近 20 日新高 + MA20>MA60 + 收盘站上 20 日线",
    "T2_PULLBACK_RESUME": "上升趋势中回踩 20 日线后收复 5 日线",
    "T3_ACCELERATION": "MA 完美多头 + MACD 金叉 + 5 日涨幅 ≥5%",
    "P1_MA20_PULLBACK": "上升趋势中今日触及 20 日线、收盘守住",
    "P2_GAP_FILL": "近 20 日上涨缺口被回补、守住缺口下沿",
    "P3_TIGHT_CONSOLIDATION": "前 20 日 ≥10% 上涨后 5 日箱体 ≤5%",
    "R1_DOUBLE_BOTTOM": "近 30 日两个等高低点形成双底、突破颈线",
    "R2_HS_BOTTOM": "倒头肩底形态 + 突破颈线",
    "R3_HAMMER": "下跌 ≥8% 后单日锤子线（长下影线 + 阳线）",
    "F1_FLAG": "强劲旗杆后旗面窄幅整理、临近突破",
    "F2_TRIANGLE": "近期区间持续收敛、向上突破",
    "F3_RECTANGLE": "横向整理矩形 + 突破上轨",
    "V1_VOL_PRICE_UP": "5 日涨幅 ≥5% 且量比 ≥1.5",
    "V2_QUIET_COIL": "5 日窄幅 + 量比 <0.7（缩量蓄势）",
    "S1_SECTOR_RESONANCE": "板块 L1 ≥1% + L2 ≥1.5% + 个股 ≥2%",
    "S2_LEADER_FOLLOWTHROUGH": "L2 板块强势中个股位列前 30%",
    "S3_LAGGARD_CATCHUP": "L2 强势但个股 20 日滞涨、今日补涨",
    "C1_CHIP_CONCENTRATED": "成本带 ≤15% + 收于 20 日线上方",
    "C2_CHIP_LOOSE": "成本带 ≥25% + 盈利盘 ≥80%（警示信号）",
}


def _section_strategy_spotlight(engine: Engine, on_date: date) -> dict:
    """§07 — per-strategy top-10 candidates by raw setup score, grouped by family."""
    sql = text("""
        SELECT setup_name, ts_code, final_score, evidence_json
        FROM ta.candidates_daily
        WHERE trade_date = :d
        ORDER BY setup_name, final_score DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"d": on_date}).fetchall()

    # Use raw score from evidence (not aggregated stock_score)
    by_setup: dict[str, list] = {}
    all_codes: set[str] = set()
    for setup_name, ts_code, final_score, ev in rows:
        ev_dict = ev if isinstance(ev, dict) else {}
        raw_score = ev_dict.get("score")
        if raw_score is None:
            raw_score = float(final_score) if final_score is not None else 0.0
        else:
            raw_score = float(raw_score)
        by_setup.setdefault(setup_name, []).append({
            "ts_code": ts_code,
            "score": raw_score,
            "triggers": ev_dict.get("triggers", []),
        })
        all_codes.add(ts_code)

    names = _load_stock_names(engine, list(all_codes))

    # Sort each setup's list by raw score desc, take top 10
    family_zh = {"T": "T 趋势族", "P": "P 回踩族", "R": "R 反转族",
                 "F": "F 形态族", "V": "V 量价族", "S": "S 板块族", "C": "C 筹码族"}
    families: dict[str, list] = {v: [] for v in family_zh.values()}
    for setup_name in sorted(by_setup.keys()):
        items = sorted(by_setup[setup_name], key=lambda x: -x["score"])[:10]
        for item in items:
            item["name"] = names.get(item["ts_code"], "")
        fam_letter = setup_name[0]
        families[family_zh[fam_letter]].append({
            "setup_name": setup_name,
            "description": _STRATEGY_DESCRIPTIONS.get(setup_name, ""),
            "n_total": len(by_setup[setup_name]),
            "top10": items,
        })
    return {
        "type": "strategy_spotlight",
        "title": "§07 单策略聚光灯（按族折叠）",
        "families": families,
    }


def _section_stars(engine: Engine, on_date: date, *, star_filter: int, title: str) -> dict:
    """Per-stock candidate list at a given star filter — aggregates multiple
    strategies hitting the same stock into one row with strategy mix."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ts_code, setup_name, rank, final_score, star_rating,
                       evidence_json, in_top_watchlist
                FROM ta.candidates_daily
                WHERE trade_date = :d AND star_rating = :s
                ORDER BY rank
            """),
            {"d": on_date, "s": star_filter},
        ).fetchall()

    # Group rows by ts_code (rank/score/star are stock-level after M9 aggregation)
    by_stock: dict[str, dict] = {}
    for r in rows:
        ts_code = r[0]
        ev = r[5] if isinstance(r[5], dict) else {}
        rec = by_stock.setdefault(ts_code, {
            "rank": int(r[2]) if r[2] is not None else 999,
            "stock_score": float(r[3]) if r[3] is not None else None,
            "stars": int(r[4]) if r[4] is not None else None,
            "in_top_watchlist": bool(r[6]),
            "resonance_count": ev.get("resonance_count"),
            "resonance_families": ev.get("resonance_families", []),
            "strategies": [],
        })
        rec["strategies"].append({
            "setup_name": r[1],
            "raw_score": float(ev.get("score", r[3])) if ev else (float(r[3]) if r[3] is not None else None),
            "triggers": ev.get("triggers", []),
        })

    sorted_stocks = sorted(by_stock.items(), key=lambda kv: kv[1]["rank"])
    names = _load_stock_names(engine, [ts for ts, _ in sorted_stocks])

    candidates = []
    for ts_code, rec in sorted_stocks:
        candidates.append({
            "ts_code": ts_code,
            "name": names.get(ts_code, ""),
            "rank": rec["rank"],
            "stock_score": rec["stock_score"],
            "stars": rec["stars"],
            "resonance_count": rec["resonance_count"] or len(rec["strategies"]),
            "resonance_families": rec["resonance_families"],
            "strategies": rec["strategies"],
            "in_top_watchlist": rec["in_top_watchlist"],
        })
    return {
        "type": "candidate_list",
        "title": title,
        "stars": star_filter,
        "candidates": candidates,
    }


def _section_verification(engine: Engine, on_date: date) -> dict:
    """§08 历史重点池关注 — past 15 trade days of Tier A picks with T+N returns.

    Mirrors Ningbo's tracking style but observation-only (no stop/target).
    Cold start: when no Tier A history yet, returns empty list with a hint
    to run `scripts/ta_backfill.py --start ... --end ...` first.
    """
    from datetime import timedelta
    from ifa.core.calendar import trading_days_between

    # 15 trade days before today (look-back window for observation tracking)
    cal_start = on_date - timedelta(days=30)
    days = trading_days_between(engine, cal_start, on_date)
    days = [d for d in days if d < on_date]   # exclude today
    days = days[-15:]                          # last 15 trade days (or fewer)

    if not days:
        return {
            "type": "history_watch",
            "title": "§08 历史重点池关注",
            "window_days": 0,
            "rows": [],
            "cold_start_hint": True,
        }

    # Pick the lowest-rank candidate row per (ts_code, trade_date) — that's the
    # "primary" strategy for that pick; its tracking row carries the T+N return
    # which is the same regardless of which strategy fired (it's a stock-level outcome).
    sql = text("""
        WITH primary_picks AS (
            SELECT DISTINCT ON (ts_code, trade_date)
                ts_code, trade_date, candidate_id, final_score,
                evidence_json->>'resonance_count' AS rc,
                evidence_json->>'resonance_families' AS fams
            FROM ta.candidates_daily
            WHERE trade_date = ANY(:days) AND evidence_json->>'tier' = 'A'
            ORDER BY ts_code, trade_date, rank
        )
        SELECT p.trade_date, p.ts_code, p.final_score, p.rc, p.fams,
               t1.return_pct, t3.return_pct, t5.return_pct, t10.return_pct
        FROM primary_picks p
        LEFT JOIN ta.candidate_tracking t1
            ON t1.candidate_id = p.candidate_id AND t1.horizon_days = 1
        LEFT JOIN ta.candidate_tracking t3
            ON t3.candidate_id = p.candidate_id AND t3.horizon_days = 3
        LEFT JOIN ta.candidate_tracking t5
            ON t5.candidate_id = p.candidate_id AND t5.horizon_days = 5
        LEFT JOIN ta.candidate_tracking t10
            ON t10.candidate_id = p.candidate_id AND t10.horizon_days = 10
        ORDER BY p.trade_date DESC, p.final_score DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"days": days}).fetchall()

    if not rows:
        return {
            "type": "history_watch",
            "title": "§08 历史重点池关注",
            "window_days": len(days),
            "rows": [],
            "cold_start_hint": True,
        }

    ts_codes = list({r[1] for r in rows})
    names = _load_stock_names(engine, ts_codes)

    out_rows = []
    for r in rows:
        out_rows.append({
            "trade_date": r[0].isoformat() if r[0] else None,
            "ts_code": r[1],
            "name": names.get(r[1], ""),
            "stock_score": float(r[2]) if r[2] is not None else None,
            "resonance_count": int(r[3]) if r[3] is not None else None,
            "resonance_families": r[4] or "",
            "ret_t1": float(r[5]) if r[5] is not None else None,
            "ret_t3": float(r[6]) if r[6] is not None else None,
            "ret_t5": float(r[7]) if r[7] is not None else None,
            "ret_t10": float(r[8]) if r[8] is not None else None,
        })

    # Summary stats: of all picks where T+10 settled, what's the win rate / avg ret
    settled = [r for r in out_rows if r["ret_t10"] is not None]
    win_count = sum(1 for r in settled if r["ret_t10"] >= 5.0)
    pos_count = sum(1 for r in settled if r["ret_t10"] > 0)
    avg_t10 = sum(r["ret_t10"] for r in settled) / len(settled) if settled else None

    return {
        "type": "history_watch",
        "title": "§08 历史重点池关注",
        "window_days": len(days),
        "rows": out_rows,
        "cold_start_hint": False,
        "summary": {
            "n_total": len(out_rows),
            "n_settled_t10": len(settled),
            "win_count": win_count,           # T+10 ≥ +5%
            "pos_count": pos_count,           # T+10 > 0
            "avg_t10": avg_t10,
        },
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


def _load_stock_names(engine: Engine, ts_codes: list[str]) -> dict[str, str]:
    """Batch lookup ts_code → name from sw_member_monthly (most recent snapshot)."""
    if not ts_codes:
        return {}
    sql = text("""
        SELECT DISTINCT ON (ts_code) ts_code, name
        FROM smartmoney.sw_member_monthly
        WHERE ts_code = ANY(:codes) AND name IS NOT NULL
        ORDER BY ts_code, snapshot_month DESC
    """)
    with engine.connect() as conn:
        return {r[0]: r[1] for r in conn.execute(sql, {"codes": ts_codes})}


def _section_index_panel(engine: Engine, on_date: date) -> dict:
    """§02 — six index panel with 10-day sparkline."""
    rows: list[dict] = []
    with engine.connect() as conn:
        for ts_code, name, role in _INDEX_PANEL:
            today = conn.execute(text("""
                SELECT close, pct_chg, amount FROM smartmoney.raw_index_daily
                WHERE ts_code = :tc AND trade_date = :d
            """), {"tc": ts_code, "d": on_date}).fetchone()
            history = conn.execute(text("""
                SELECT close FROM smartmoney.raw_index_daily
                WHERE ts_code = :tc AND trade_date <= :d
                ORDER BY trade_date DESC LIMIT 10
            """), {"tc": ts_code, "d": on_date}).fetchall()
            history_closes = [float(r[0]) for r in reversed(history)]
            rows.append({
                "ts_code": ts_code,
                "name": name,
                "role": role,
                "close": float(today[0]) if today and today[0] else None,
                "pct_chg": float(today[1]) if today and today[1] is not None else None,
                "amount_yi": float(today[2]) / 1e5 if today and today[2] else None,    # 千元 → 亿
                "spark_svg": sparkline_svg(history_closes, width=110, height=26) if history_closes else "",
            })
    return {"type": "index_panel", "title": "§02 主要指数收盘", "rows": rows}


def _section_methodology() -> dict:
    return {
        "type": "methodology",
        "title": "策略说明 / 评级原则",
    }


def _section_market_state(engine: Engine, on_date: date) -> dict:
    """§02 市场状态盘 — breadth / amount / north flow / regime evidence.

    Amount is re-aggregated from smartmoney.raw_daily (千元) directly; the
    persisted market_state_daily.total_amount column has been observed to
    drift on some dates. raw_daily.amount is canonical TuShare 千元.
    """
    from datetime import timedelta
    with engine.connect() as conn:
        ms = conn.execute(text("""
            SELECT up_count, down_count, flat_count,
                   limit_up_count, limit_down_count, max_consecutive_limit_up,
                   blow_up_count, blow_up_rate, market_state
            FROM smartmoney.market_state_daily WHERE trade_date = :d
        """), {"d": on_date}).fetchone()
        hsgt = conn.execute(text("""
            SELECT north_money FROM smartmoney.raw_moneyflow_hsgt WHERE trade_date = :d
        """), {"d": on_date}).fetchone()
        # Re-aggregate today's full-market amount from raw_daily (千元 → 亿元)
        amt_today = conn.execute(text("""
            SELECT SUM(amount) / 1e5 AS amt_yi
            FROM smartmoney.raw_daily WHERE trade_date = :d
        """), {"d": on_date}).scalar()
        # 60-day percentile of today's amount
        amt_pct_60 = conn.execute(text("""
            WITH daily AS (
                SELECT trade_date, SUM(amount) AS amt
                FROM smartmoney.raw_daily
                WHERE trade_date BETWEEN :start AND :on_date
                GROUP BY trade_date
            ),
            today AS (SELECT amt AS today_amt FROM daily WHERE trade_date = :on_date)
            SELECT 100.0 * COUNT(*) FILTER (WHERE d.amt < t.today_amt) / NULLIF(COUNT(*), 0)
            FROM daily d, today t
        """), {"start": on_date - timedelta(days=90), "on_date": on_date}).scalar()
    return {
        "type": "market_state",
        "title": "§02 市场结构与情绪",
        "amount_yi_yuan": float(amt_today) if amt_today else None,
        "amount_pct_60d": float(amt_pct_60) if amt_pct_60 is not None else None,
        "up_count": int(ms[0]) if ms and ms[0] is not None else None,
        "down_count": int(ms[1]) if ms and ms[1] is not None else None,
        "flat_count": int(ms[2]) if ms and ms[2] is not None else None,
        "limit_up": int(ms[3]) if ms and ms[3] is not None else None,
        "limit_down": int(ms[4]) if ms and ms[4] is not None else None,
        "consecutive_lb_high": int(ms[5]) if ms and ms[5] is not None else None,
        "blow_up_count": int(ms[6]) if ms and ms[6] is not None else None,
        "blow_up_rate": float(ms[7]) if ms and ms[7] is not None else None,
        "market_state": ms[8] if ms else None,
        "north_yi_yuan": float(hsgt[0]) / 1e4 if hsgt and hsgt[0] else None,
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

    names = _load_stock_names(engine, [r[0] for r in cands])
    hypotheses = []
    for ts_code, setup_name, score in cands:
        hypotheses.append({
            "ts_code": ts_code,
            "name": names.get(ts_code, ""),
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
    from ifa.core.report.disclaimer import (
        DISCLAIMER_PARAGRAPHS_ZH, DISCLAIMER_PARAGRAPHS_EN,
    )
    return {
        "type": "disclaimer",
        "title": "§16 免责声明 / Disclaimer",
        "paragraphs_zh": DISCLAIMER_PARAGRAPHS_ZH,
        "paragraphs_en": DISCLAIMER_PARAGRAPHS_EN,
    }
