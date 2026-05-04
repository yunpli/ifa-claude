"""Build SetupContext for every stock on a given trade_date — batch loader.

Sources (current):
  · smartmoney.raw_daily        — 60-day OHLCV per stock; we compute MA5/10/20/60 inline
  · smartmoney.sw_member_monthly — stock → SW L1/L2 mapping
  · smartmoney.raw_sw_daily      — L1/L2 sector pct_change for the day

Sources (deferred — return None until ETL populates):
  · ta.factor_pro_daily          — MACD/RSI/turnover (T3 etc remain inactive)
  · ta.cyq_perf_daily            — chip distribution (C1/C2 inactive)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.families.ta.regime.classifier import Regime
from ifa.families.ta.setups.base import SetupContext

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 95    # need 60 trade days; 95 calendar accommodates weekends + CN holidays


def _rank_dict(values: dict) -> dict[str, float]:
    """Returns key → percentile rank (0..1) within values dict."""
    if not values:
        return {}
    sorted_vals = sorted(values.values())
    n = len(sorted_vals)
    out: dict[str, float] = {}
    for key, v in values.items():
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_vals[mid] < v:
                lo = mid + 1
            else:
                hi = mid
        out[key] = lo / max(n - 1, 1)
    return out


def _tradeable_universe(engine: Engine, on_date: date) -> set[str]:
    """Return ts_codes meeting universe filter: not suspended + min liquidity.

    M9.7: also applies sector_flow Layer 1 — excludes stocks whose SW L2
    sector is in 退潮 cycle_phase or 退潮/未识别 role (governance toggles).
    """
    from ifa.families.ta.params import load_params
    p = load_params().get("universe", {}) or {}
    sf = load_params().get("sector_flow", {}) or {}
    min_amt_qy = (p.get("min_avg_amount_yi", 0.2) * 1e8) / 1000.0
    min_coverage = p.get("min_coverage_pct", 90) / 100.0

    sql_liquid = text("""
        WITH window20 AS (
            SELECT ts_code,
                   COUNT(*) AS n_rows,
                   AVG(amount) AS avg_amt_qianyuan
            FROM smartmoney.raw_daily
            WHERE trade_date <= :on_date
              AND trade_date > :start
            GROUP BY ts_code
        ),
        max_rows AS (
            SELECT MAX(n_rows) AS expected FROM window20
        )
        SELECT w.ts_code
        FROM window20 w, max_rows m
        WHERE w.avg_amt_qianyuan >= :min_amt
          AND (w.n_rows::float / NULLIF(m.expected, 0)) >= :min_cov
    """)
    from datetime import timedelta as _td
    with engine.connect() as conn:
        rows = conn.execute(sql_liquid, {
            "on_date": on_date,
            "start": on_date - _td(days=30),
            "min_amt": min_amt_qy,
            "min_cov": min_coverage,
        }).fetchall()
    universe = {r[0] for r in rows}

    # Layer 1 — SmartMoney sector flow exclusions
    excluded_phases = {"退潮"} if sf.get("exclude_retreat_phase", True) else set()
    excluded_roles = set()
    if sf.get("exclude_retreat_role", True):
        excluded_roles.add("退潮")
    if sf.get("exclude_unidentified_role", False):
        excluded_roles.add("未识别")

    if excluded_phases or excluded_roles:
        sql_sector_state = text("""
            SELECT m.ts_code
            FROM smartmoney.sw_member_monthly m
            JOIN smartmoney.sector_state_daily s
              ON s.sector_code = m.l2_code
             AND s.sector_source = 'sw_l2'
             AND s.trade_date = :on_date
            WHERE m.snapshot_month = date_trunc('month', CAST(:on_date AS date))
              AND (s.cycle_phase = ANY(:bad_phases) OR s.role = ANY(:bad_roles))
        """)
        with engine.connect() as conn:
            excluded = {r[0] for r in conn.execute(sql_sector_state, {
                "on_date": on_date,
                "bad_phases": list(excluded_phases) or [""],
                "bad_roles": list(excluded_roles) or [""],
            })}
        universe -= excluded
        log.info("sector_flow Layer 1 excluded %d stocks (phases=%s, roles=%s)",
                 len(excluded), excluded_phases, excluded_roles)

    return universe


def build_contexts(
    engine: Engine,
    on_date: date,
    *,
    regime: Regime | None = None,
) -> dict[str, SetupContext]:
    """Returns {ts_code: SetupContext} for every stock in the tradeable universe.

    Tradeable universe = stocks with raw_daily on on_date AND meeting
    universe.min_avg_amount_yi + universe.min_coverage_pct (per ta_v2.2.yaml).

    Stocks lacking on_date row are excluded. SetupContext closes/highs/lows/volumes
    are tuples ascending by date; today's row sits at index -1.
    """
    cutoff = on_date - timedelta(days=LOOKBACK_DAYS)
    universe = _tradeable_universe(engine, on_date)

    # OHLCV — one wide query, partition by ts_code in Python.
    sql_ohlcv = text("""
        SELECT ts_code, trade_date, open, high, low, close, vol, amount, pre_close
        FROM smartmoney.raw_daily
        WHERE trade_date >= :cutoff AND trade_date <= :on_date
        ORDER BY ts_code, trade_date
    """)
    sql_sector_pct = text("""
        SELECT ts_code, pct_change
        FROM smartmoney.raw_sw_daily
        WHERE trade_date = :on_date
    """)
    sql_member = text("""
        SELECT ts_code, l1_code, l2_code
        FROM smartmoney.sw_member_monthly
        WHERE snapshot_month = date_trunc('month', CAST(:on_date AS date))
    """)
    # MACD/RSI/turnover_rate/volume_ratio (already computed by Tushare)
    sql_factor_pro = text("""
        SELECT ts_code, macd_qfq, macd_dea_qfq, macd_dif_qfq,
               rsi_qfq_6, turnover_rate_pct, volume_ratio
        FROM ta.factor_pro_daily
        WHERE trade_date = :on_date
    """)
    # M9.7 — SmartMoney sector flow per L2 code
    sql_sector_flow = text("""
        SELECT s.sector_code, s.role, s.cycle_phase, s.role_confidence, s.phase_confidence,
               mf.net_amount
        FROM smartmoney.sector_state_daily s
        LEFT JOIN smartmoney.sector_moneyflow_sw_daily mf
               ON mf.l2_code = s.sector_code AND mf.trade_date = s.trade_date
        WHERE s.sector_source = 'sw_l2' AND s.trade_date = :on_date
    """)
    # M10 — Order flow (O family)
    # 5d institutional buying days: count distinct trade_dates where any
    # 机构专用 seat had net_buy > 0 (raw_top_inst.exalter = '机构专用').
    sql_lhb_inst = text("""
        SELECT ts_code, COUNT(DISTINCT trade_date) AS n_days
        FROM smartmoney.raw_top_inst
        WHERE trade_date <= :on_date AND trade_date > :start_5d
          AND exalter = '机构专用' AND net_buy > 0
        GROUP BY ts_code
    """)
    # Today's 龙虎榜 净买额 / 流通市值 (%) — both columns are in 元.
    sql_lhb_today = text("""
        SELECT ts_code,
               CASE WHEN float_values > 0
                    THEN net_amount / float_values * 100
               END AS pct_float
        FROM smartmoney.raw_top_list
        WHERE trade_date = :on_date
    """)
    # KPL today: seal strength = limit_order(元) / free_float(元) × 100 = % of float
    # status: 'T' fully sealed; 'broken' if raw_limit_list_d.limit_='Z' or open_times>0
    sql_kpl_today = text("""
        SELECT k.ts_code,
               CASE WHEN k.free_float > 0 AND k.limit_order IS NOT NULL
                    THEN k.limit_order::numeric / k.free_float * 100
               END AS seal_ratio,
               CASE
                   WHEN l.limit_ = 'Z' OR COALESCE(l.open_times, 0) > 0 THEN 'broken'
                   ELSE 'T'
               END AS status
        FROM smartmoney.raw_kpl_list k
        LEFT JOIN smartmoney.raw_limit_list_d l
               ON l.ts_code = k.ts_code AND l.trade_date = k.trade_date
        WHERE k.trade_date = :on_date
    """)
    # 5d cumulative super-large + large net flow / 流通市值 (%)
    # raw_moneyflow buy_elg / sell_elg / buy_lg / sell_lg are in 万元;
    # raw_daily_basic.float_share is in 万股, close from raw_daily.
    sql_super_flow_5d = text("""
        WITH inst_flow AS (
            SELECT m.ts_code,
                   SUM(COALESCE(m.buy_elg_amount, 0) - COALESCE(m.sell_elg_amount, 0)
                       + COALESCE(m.buy_lg_amount, 0) - COALESCE(m.sell_lg_amount, 0)) AS net_5d
            FROM smartmoney.raw_moneyflow m
            WHERE m.trade_date <= :on_date AND m.trade_date > :start_5d
            GROUP BY m.ts_code
        ),
        float_mv AS (
            SELECT b.ts_code, b.float_share * d.close AS float_mv_wan
            FROM smartmoney.raw_daily_basic b
            JOIN smartmoney.raw_daily d
              ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
            WHERE b.trade_date = :on_date
        )
        SELECT i.ts_code,
               CASE WHEN f.float_mv_wan > 0
                    THEN i.net_5d / f.float_mv_wan * 100
               END AS net_flow_pct
        FROM inst_flow i
        JOIN float_mv f ON f.ts_code = i.ts_code
    """)
    # M10 — Event-driven (E family); table populated by event_etl from Tushare.
    # When event_signal_daily has multiple events per stock, prefer the most
    # impactful (forecast > express > disclosure_pre) using lexical priority.
    sql_event = text("""
        SELECT DISTINCT ON (ts_code) ts_code, event_type, polarity, days_to_disclosure
        FROM ta.event_signal_daily
        WHERE trade_date = :on_date
        ORDER BY ts_code,
                 CASE event_type
                      WHEN 'forecast' THEN 1
                      WHEN 'express' THEN 2
                      WHEN 'disclosure_pre' THEN 3 ELSE 9 END
    """)

    # Chip distribution
    sql_chip = text("""
        SELECT ts_code,
               CASE WHEN weight_avg > 0
                    THEN (cost_85pct - cost_15pct) / weight_avg * 100
               END AS concentration_pct,
               winner_rate_pct
        FROM ta.cyq_perf_daily
        WHERE trade_date = :on_date
    """)

    by_stock: dict[str, list] = defaultdict(list)
    with engine.connect() as conn:
        for row in conn.execute(sql_ohlcv, {"cutoff": cutoff, "on_date": on_date}):
            by_stock[row[0]].append(row)
        sector_pct = {r[0]: float(r[1]) if r[1] is not None else None
                      for r in conn.execute(sql_sector_pct, {"on_date": on_date})}
        members = {r[0]: (r[1], r[2])
                   for r in conn.execute(sql_member, {"on_date": on_date})}
        factor_pro = {
            r[0]: {
                "macd_qfq": float(r[1]) if r[1] is not None else None,
                "macd_dea_qfq": float(r[2]) if r[2] is not None else None,
                "macd_dif_qfq": float(r[3]) if r[3] is not None else None,
                "rsi_qfq_6": float(r[4]) if r[4] is not None else None,
                "turnover_rate_pct": float(r[5]) if r[5] is not None else None,
                "volume_ratio_tushare": float(r[6]) if r[6] is not None else None,
            }
            for r in conn.execute(sql_factor_pro, {"on_date": on_date})
        }
        chip = {
            r[0]: {
                "concentration_pct": float(r[1]) if r[1] is not None else None,
                "winner_rate_pct": float(r[2]) if r[2] is not None else None,
            }
            for r in conn.execute(sql_chip, {"on_date": on_date})
        }
        start_5d = on_date - timedelta(days=10)  # 5 trade-days ≈ 10 calendar days
        lhb_inst_days = {r[0]: int(r[1]) for r in conn.execute(
            sql_lhb_inst, {"on_date": on_date, "start_5d": start_5d})}
        lhb_today = {r[0]: float(r[1]) if r[1] is not None else None
                     for r in conn.execute(sql_lhb_today, {"on_date": on_date})}
        kpl_today: dict[str, dict] = {}
        for r in conn.execute(sql_kpl_today, {"on_date": on_date}):
            kpl_today[r[0]] = {
                "seal_ratio": float(r[1]) if r[1] is not None else None,
                "status": r[2],
            }
        super_flow_5d = {r[0]: float(r[1]) if r[1] is not None else None
                         for r in conn.execute(sql_super_flow_5d,
                                               {"on_date": on_date, "start_5d": start_5d})}
        events_today: dict[str, dict] = {}
        try:
            for r in conn.execute(sql_event, {"on_date": on_date}):
                events_today[r[0]] = {
                    "event_type": r[1], "polarity": r[2],
                    "days_to_disclosure": int(r[3]) if r[3] is not None else None,
                }
        except Exception as e:
            log.debug("ta.event_signal_daily not available: %s", e)
        sector_flow_raw = {
            r[0]: {
                "role": r[1], "cycle_phase": r[2],
                "role_conf": r[3], "phase_conf": r[4],
                "net_amount": float(r[5]) if r[5] is not None else None,
            }
            for r in conn.execute(sql_sector_flow, {"on_date": on_date})
        }

    # Build sector_quality per L2 sector — combines net_amount rank,
    # data-derived phase score, SmartMoney confidence
    from ifa.families.ta.params import load_params
    from ifa.families.ta.sector_phase_metrics import load_phase_scores
    sf_params = load_params().get("sector_flow", {})
    rank_w = sf_params.get("rank_weight", 0.5)
    phase_w = sf_params.get("phase_weight", 0.3)
    conf_w = sf_params.get("confidence_weight", 0.2)
    phase_scores = load_phase_scores(engine, on_date)   # data-derived

    # net_amount cross-sectional rank within today's L2 universe
    flow_amts = {l2: rec["net_amount"] for l2, rec in sector_flow_raw.items()
                 if rec["net_amount"] is not None}
    flow_rank_dict = _rank_dict(flow_amts) if flow_amts else {}

    _CONF = {"high": 1.0, "medium": 0.6, "low": 0.3}
    sector_quality_by_l2: dict[str, float] = {}
    for l2, rec in sector_flow_raw.items():
        rank_score = flow_rank_dict.get(l2, 0.5)
        phase_score = phase_scores.get(rec["cycle_phase"], 0.5) if rec["cycle_phase"] else 0.5
        # Use phase_confidence (not role_confidence) since cycle_phase drives quality
        conf_score = _CONF.get((rec["phase_conf"] or "").lower(), 0.6)
        sector_quality_by_l2[l2] = (
            rank_w * rank_score
            + phase_w * phase_score
            + conf_w * conf_score
        )

    # Build per-L2 peer dict for sector_peers_pct_change
    l2_to_members: dict[str, list[str]] = defaultdict(list)
    for ts_code, (l1, l2) in members.items():
        if l2:
            l2_to_members[l2].append(ts_code)

    # ── Cross-sectional ranks (0-1, 1.0 = highest in today's universe) ──
    # Build rank for volume_ratio (Tushare-provided where available, else our proxy)
    # and today's stock return — both used by setups for "relatively strong" tests.
    vol_ratio_today: dict[str, float] = {}
    for ts_code, fp in factor_pro.items():
        v = fp.get("volume_ratio_tushare")
        if v is not None:
            vol_ratio_today[ts_code] = v

    vol_ratio_rank_dict = _rank_dict(vol_ratio_today)

    # Compute today's stock pct_change from raw_daily for peer dicts
    stock_pct_today: dict[str, float] = {}
    for ts_code, rows in by_stock.items():
        if not rows or rows[-1][1] != on_date:
            continue
        row = rows[-1]
        pre_close = float(row[8]) if row[8] else None
        close = float(row[5]) if row[5] else None
        if pre_close and close:
            stock_pct_today[ts_code] = (close / pre_close - 1.0) * 100

    # Cross-sectional rank of today's stock return
    pct_rank_dict = _rank_dict(stock_pct_today)

    contexts: dict[str, SetupContext] = {}
    for ts_code, rows in by_stock.items():
        if not rows or rows[-1][1] != on_date:
            continue
        if ts_code not in universe:    # M9: tradeable-universe filter
            continue
        # Need at least 21 rows for the cheapest setups (T1, R3 etc); cull below
        if len(rows) < 21:
            continue

        closes = tuple(float(r[5]) for r in rows)
        highs = tuple(float(r[3]) for r in rows)
        lows = tuple(float(r[4]) for r in rows)
        volumes = tuple(float(r[6]) for r in rows)

        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None

        # volume_ratio = today's vol / 20-day avg
        avg_vol_20 = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else None
        vol_ratio = volumes[-1] / avg_vol_20 if avg_vol_20 and avg_vol_20 > 0 else None

        # ATR proxy = 20d std-dev of intraday range / close, in % units (volatility)
        atr_pct_20d = None
        if len(closes) >= 20 and len(highs) >= 20 and len(lows) >= 20:
            window_20 = list(zip(highs[-20:], lows[-20:], closes[-20:]))
            ranges_pct = [
                (h - l) / c * 100 if c else None
                for (h, l, c) in window_20
            ]
            ranges_clean = [r for r in ranges_pct if r is not None]
            if len(ranges_clean) >= 10:
                atr_pct_20d = sum(ranges_clean) / len(ranges_clean)

        l1_code, l2_code = members.get(ts_code, (None, None))
        l1_pct = sector_pct.get(l1_code) if l1_code else None
        l2_pct = sector_pct.get(l2_code) if l2_code else None

        peers = None
        if l2_code and l2_code in l2_to_members:
            peers = {peer: stock_pct_today[peer]
                     for peer in l2_to_members[l2_code]
                     if peer != ts_code and peer in stock_pct_today}
            if not peers:
                peers = None

        fp = factor_pro.get(ts_code, {})
        cp = chip.get(ts_code, {})
        # Prefer Tushare's volume_ratio when present; fall back to our own.
        vol_ratio_final = fp.get("volume_ratio_tushare") or vol_ratio

        contexts[ts_code] = SetupContext(
            ts_code=ts_code,
            trade_date=on_date,
            closes=closes,
            highs=highs,
            lows=lows,
            volumes=volumes,
            close_today=closes[-1],
            ma_qfq_5=ma5,
            ma_qfq_10=ma10,
            ma_qfq_20=ma20,
            ma_qfq_60=ma60,
            macd_qfq=fp.get("macd_qfq"),
            macd_dea_qfq=fp.get("macd_dea_qfq"),
            macd_dif_qfq=fp.get("macd_dif_qfq"),
            rsi_qfq_6=fp.get("rsi_qfq_6"),
            turnover_rate_pct=fp.get("turnover_rate_pct"),
            volume_ratio=vol_ratio_final,
            atr_pct_20d=atr_pct_20d,
            volume_ratio_rank=vol_ratio_rank_dict.get(ts_code),
            today_pct_chg_rank=pct_rank_dict.get(ts_code),
            regime=regime,
            sw_l1_code=l1_code,
            sw_l2_code=l2_code,
            sw_l1_pct_change=l1_pct,
            sw_l2_pct_change=l2_pct,
            sector_peers_pct_change=peers,
            sector_quality=sector_quality_by_l2.get(l2_code) if l2_code else None,
            sector_role=sector_flow_raw.get(l2_code, {}).get("role") if l2_code else None,
            sector_cycle_phase=sector_flow_raw.get(l2_code, {}).get("cycle_phase") if l2_code else None,
            chip_concentration_pct=cp.get("concentration_pct"),
            chip_winner_rate_pct=cp.get("winner_rate_pct"),
            today_pct_chg=stock_pct_today.get(ts_code),
            lhb_inst_buy_days_5d=lhb_inst_days.get(ts_code),
            lhb_net_buy_pct_float_today=lhb_today.get(ts_code),
            kpl_seal_ratio_today=kpl_today.get(ts_code, {}).get("seal_ratio"),
            kpl_status_today=kpl_today.get(ts_code, {}).get("status"),
            super_large_net_buy_5d_pct=super_flow_5d.get(ts_code),
            event_type_today=events_today.get(ts_code, {}).get("event_type"),
            event_polarity=events_today.get(ts_code, {}).get("polarity"),
            days_to_disclosure=events_today.get(ts_code, {}).get("days_to_disclosure"),
        )

    log.info("built %d setup contexts for %s", len(contexts), on_date)
    return contexts
