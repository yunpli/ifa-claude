"""Sector diffusion features."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text


def compute_diffusion_range(engine, *, start: dt.date, end: dt.date) -> int:
    sql = text("""
        WITH dates AS (
            SELECT trade_date,
                   ROW_NUMBER() OVER (ORDER BY trade_date ASC) AS rn
            FROM (
                SELECT DISTINCT trade_date
                FROM sme.sme_stock_orderflow_daily
                WHERE trade_date <= :end
                  AND trade_date >= (
                      SELECT MIN(trade_date)
                      FROM (
                          SELECT trade_date
                          FROM sme.sme_stock_orderflow_daily
                          WHERE trade_date <= :start
                          ORDER BY trade_date DESC
                          LIMIT 10
                      ) lookback
                  )
            ) x
        ),
        current_dates AS (
            SELECT trade_date, rn
            FROM dates
            WHERE trade_date BETWEEN :start AND :end
        ),
        universe AS (
            SELECT
                cd.trade_date,
                cd.rn AS target_rn,
                m.l2_code,
                m.l2_name,
                m.ts_code
            FROM current_dates cd
            JOIN sme.sme_sw_member_daily m
              ON m.trade_date = cd.trade_date
        ),
        stock_roll AS (
            SELECT
                u.trade_date,
                u.l2_code,
                MAX(u.l2_name) AS l2_name,
                u.ts_code,
                SUM(f.main_net_yuan) FILTER (WHERE hd.rn = u.target_rn) AS main_net_1d_yuan,
                SUM(f.main_net_yuan) FILTER (WHERE hd.rn BETWEEN u.target_rn - 2 AND u.target_rn) AS main_net_3d_yuan,
                SUM(f.main_net_yuan) FILTER (WHERE hd.rn BETWEEN u.target_rn - 4 AND u.target_rn) AS main_net_5d_yuan,
                SUM(f.main_net_yuan) FILTER (WHERE hd.rn BETWEEN u.target_rn - 9 AND u.target_rn) AS main_net_10d_yuan,
                MAX(f.pct_chg) FILTER (WHERE hd.rn = u.target_rn) AS ret_1d,
                (EXP(SUM(LN(GREATEST(0.0001, 1 + COALESCE(f.pct_chg, 0) / 100.0))) FILTER (WHERE hd.rn BETWEEN u.target_rn - 2 AND u.target_rn)) - 1) * 100.0 AS ret_3d,
                (EXP(SUM(LN(GREATEST(0.0001, 1 + COALESCE(f.pct_chg, 0) / 100.0))) FILTER (WHERE hd.rn BETWEEN u.target_rn - 4 AND u.target_rn)) - 1) * 100.0 AS ret_5d
            FROM universe u
            JOIN dates hd
              ON hd.rn BETWEEN u.target_rn - 9 AND u.target_rn
            JOIN sme.sme_stock_orderflow_daily f
              ON f.trade_date = hd.trade_date AND f.ts_code = u.ts_code
            GROUP BY u.trade_date, u.l2_code, u.ts_code
        ),
        ranked AS (
            SELECT
                trade_date,
                l2_code,
                l2_name,
                ts_code,
                main_net_1d_yuan,
                main_net_3d_yuan,
                main_net_5d_yuan,
                main_net_10d_yuan,
                ret_1d,
                ret_3d,
                ret_5d,
                ROW_NUMBER() OVER (PARTITION BY trade_date, l2_code ORDER BY main_net_1d_yuan DESC NULLS LAST) AS flow_rank_1d
            FROM stock_roll
        ),
        roll AS (
            SELECT
                r.trade_date,
                r.l2_code,
                MAX(r.l2_name) AS l2_name,
                AVG(CASE WHEN r.main_net_1d_yuan > 0 THEN 1.0 ELSE 0.0 END) AS breadth_1d,
                AVG(CASE WHEN r.main_net_3d_yuan > 0 THEN 1.0 ELSE 0.0 END) AS breadth_3d,
                AVG(CASE WHEN r.main_net_5d_yuan > 0 THEN 1.0 ELSE 0.0 END) AS breadth_5d,
                AVG(CASE WHEN r.main_net_10d_yuan > 0 THEN 1.0 ELSE 0.0 END) AS breadth_10d,
                MAX(r.ret_1d) FILTER (WHERE r.ts_code = cur.leader_ts_code) AS leader_ret_1d,
                MAX(r.ret_3d) FILTER (WHERE r.ts_code = cur.leader_ts_code) AS leader_ret_3d,
                MAX(r.ret_5d) FILTER (WHERE r.ts_code = cur.leader_ts_code) AS leader_ret_5d,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY r.ret_5d) AS median_ret_5d,
                percentile_cont(0.2) WITHIN GROUP (ORDER BY r.ret_5d) AS tail_ret_5d,
                jsonb_agg(
                    jsonb_build_object(
                        'ts_code', r.ts_code,
                        'main_net_1d_yuan', r.main_net_1d_yuan,
                        'main_net_5d_yuan', r.main_net_5d_yuan,
                        'return_1d', r.ret_1d,
                        'return_5d', r.ret_5d
                    )
                    ORDER BY r.main_net_1d_yuan DESC NULLS LAST
                ) FILTER (WHERE r.flow_rank_1d <= 5) AS top_json
            FROM ranked r
            JOIN sme.sme_sector_orderflow_daily cur
              ON cur.trade_date = r.trade_date AND cur.l2_code = r.l2_code
            GROUP BY r.trade_date, r.l2_code, cur.leader_ts_code
        ),
        final AS (
            SELECT
                r.trade_date,
                r.l2_code,
                r.l2_name,
                r.leader_ret_1d AS leader_return_1d,
                r.leader_ret_3d AS leader_return_3d,
                r.leader_ret_5d AS leader_return_5d,
                r.median_ret_5d AS median_member_return_5d,
                r.tail_ret_5d AS tail_member_return_5d,
                r.leader_ret_5d - r.median_ret_5d AS leader_to_median_spread,
                r.breadth_1d AS flow_breadth_1d,
                r.breadth_3d AS flow_breadth_3d,
                r.breadth_5d AS flow_breadth_5d,
                r.breadth_10d AS flow_breadth_10d,
                r.breadth_5d - r.breadth_10d AS diffusion_slope_5_10,
                CASE
                  WHEN r.breadth_1d >= 0.60 AND COALESCE(r.leader_ret_1d, 0) > 0 THEN 'broad_diffusion'
                  WHEN r.breadth_1d >= 0.40 AND COALESCE(r.leader_ret_1d, 0) > 0 THEN 'midcap_following'
                  WHEN COALESCE(r.leader_ret_1d, 0) > 0 THEN 'leader_confirmed'
                  WHEN r.breadth_1d < 0.25 THEN 'diffusion_breakdown'
                  ELSE 'leader_only'
                END AS diffusion_phase,
                LEAST(1.0, GREATEST(0.0, COALESCE(r.breadth_1d, 0) * 0.55 + COALESCE(r.breadth_5d, 0) * 0.30 + CASE WHEN COALESCE(r.leader_ret_1d, 0) > 0 THEN 0.15 ELSE 0 END)) AS diffusion_score,
                COALESCE(r.top_json, '[]'::jsonb) AS top_members_json
            FROM roll r
        )
        INSERT INTO sme.sme_sector_diffusion_daily (
            trade_date, l2_code, l2_name, leader_return_1d, leader_return_3d,
            leader_return_5d, median_member_return_5d, tail_member_return_5d,
            leader_to_median_spread, flow_breadth_1d, flow_breadth_3d,
            flow_breadth_5d, flow_breadth_10d, diffusion_slope_5_10,
            diffusion_phase, diffusion_score, top_members_json, quality_flag, computed_at
        )
        SELECT trade_date, l2_code, l2_name, leader_return_1d, leader_return_3d,
               leader_return_5d, median_member_return_5d, tail_member_return_5d,
               leader_to_median_spread, flow_breadth_1d, flow_breadth_3d,
               flow_breadth_5d, flow_breadth_10d, diffusion_slope_5_10,
               diffusion_phase, diffusion_score, top_members_json, 'ok', now()
        FROM final
        ON CONFLICT (trade_date, l2_code) DO UPDATE SET
            l2_name = EXCLUDED.l2_name,
            leader_return_1d = EXCLUDED.leader_return_1d,
            leader_return_3d = EXCLUDED.leader_return_3d,
            leader_return_5d = EXCLUDED.leader_return_5d,
            median_member_return_5d = EXCLUDED.median_member_return_5d,
            tail_member_return_5d = EXCLUDED.tail_member_return_5d,
            leader_to_median_spread = EXCLUDED.leader_to_median_spread,
            flow_breadth_1d = EXCLUDED.flow_breadth_1d,
            flow_breadth_3d = EXCLUDED.flow_breadth_3d,
            flow_breadth_5d = EXCLUDED.flow_breadth_5d,
            flow_breadth_10d = EXCLUDED.flow_breadth_10d,
            diffusion_slope_5_10 = EXCLUDED.diffusion_slope_5_10,
            diffusion_phase = EXCLUDED.diffusion_phase,
            diffusion_score = EXCLUDED.diffusion_score,
            top_members_json = EXCLUDED.top_members_json,
            quality_flag = EXCLUDED.quality_flag,
            computed_at = now()
    """)
    with engine.begin() as conn:
        result = conn.execute(sql, {"start": start, "end": end})
    return int(result.rowcount or 0)


def compute_diffusion(engine, *, trade_date: dt.date) -> int:
    return compute_diffusion_range(engine, start=trade_date, end=trade_date)
