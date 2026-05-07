"""SW L2 sector orderflow aggregation."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text


def compute_sector_orderflow(engine, *, trade_date: dt.date, source_mode: str = "prefer_smartmoney", run_id: str | None = None) -> int:
    sql = text("""
        WITH joined AS (
            SELECT
                m.trade_date,
                m.l1_code, m.l1_name, m.l2_code, m.l2_name, m.ts_code, m.name,
                f.ts_code AS flow_ts_code, f.amount_yuan, f.pct_chg, f.sm_net_yuan, f.md_net_yuan,
                f.lg_net_yuan, f.elg_net_yuan, f.main_net_yuan, f.retail_net_yuan,
                f.net_mf_amount_yuan
            FROM sme.sme_sw_member_daily m
            LEFT JOIN sme.sme_stock_orderflow_daily f
              ON f.trade_date = m.trade_date AND f.ts_code = m.ts_code
            WHERE m.trade_date = :d
        ),
        agg AS (
            SELECT
                trade_date, l1_code, l1_name, l2_code, l2_name,
                COUNT(*)::int AS member_count,
                COUNT(flow_ts_code)::int AS matched_stock_count,
                SUM(amount_yuan)::bigint AS sector_amount_yuan,
                AVG(pct_chg)::float AS sector_return_equal_weight,
                CASE WHEN SUM(amount_yuan) > 0 THEN SUM(pct_chg * amount_yuan)::float / SUM(amount_yuan) END AS sector_return_amount_weight,
                SUM(sm_net_yuan)::bigint AS sm_net_yuan,
                SUM(md_net_yuan)::bigint AS md_net_yuan,
                SUM(lg_net_yuan)::bigint AS lg_net_yuan,
                SUM(elg_net_yuan)::bigint AS elg_net_yuan,
                SUM(main_net_yuan)::bigint AS main_net_yuan,
                SUM(retail_net_yuan)::bigint AS retail_net_yuan,
                SUM(net_mf_amount_yuan)::bigint AS net_mf_amount_yuan,
                AVG(CASE WHEN net_mf_amount_yuan IS NULL THEN NULL WHEN net_mf_amount_yuan > 0 THEN 1.0 ELSE 0.0 END)::float AS flow_breadth,
                AVG(CASE WHEN main_net_yuan IS NULL THEN NULL WHEN main_net_yuan > 0 THEN 1.0 ELSE 0.0 END)::float AS main_positive_breadth,
                AVG(CASE WHEN elg_net_yuan IS NULL THEN NULL WHEN elg_net_yuan > 0 THEN 1.0 ELSE 0.0 END)::float AS elg_positive_breadth,
                AVG(CASE WHEN retail_net_yuan IS NULL THEN NULL WHEN retail_net_yuan > 0 THEN 1.0 ELSE 0.0 END)::float AS retail_positive_breadth,
                AVG(CASE WHEN pct_chg IS NULL THEN NULL WHEN pct_chg > 0 THEN 1.0 ELSE 0.0 END)::float AS price_positive_breadth
            FROM joined
            GROUP BY trade_date, l1_code, l1_name, l2_code, l2_name
        ),
        leader AS (
            SELECT DISTINCT ON (l2_code)
                l2_code, ts_code AS leader_ts_code, name AS leader_name, main_net_yuan AS leader_main_net_yuan
            FROM joined
            WHERE main_net_yuan IS NOT NULL
            ORDER BY l2_code, main_net_yuan DESC NULLS LAST
        ),
        top5 AS (
            SELECT l2_code,
                   SUM(main_net_yuan)::float AS top5_main_net_yuan
            FROM (
                SELECT l2_code, main_net_yuan,
                       ROW_NUMBER() OVER (PARTITION BY l2_code ORDER BY main_net_yuan DESC NULLS LAST) AS rn
                FROM joined
                WHERE main_net_yuan > 0
            ) x
            WHERE rn <= 5
            GROUP BY l2_code
        )
        INSERT INTO sme.sme_sector_orderflow_daily (
            trade_date, l1_code, l1_name, l2_code, l2_name,
            member_count, matched_stock_count, coverage_ratio, sector_amount_yuan,
            sector_return_equal_weight, sector_return_amount_weight, sector_return_sw_index,
            sm_net_yuan, md_net_yuan, lg_net_yuan, elg_net_yuan, main_net_yuan,
            retail_net_yuan, net_mf_amount_yuan, main_net_ratio, retail_net_ratio,
            elg_net_ratio, flow_breadth, main_positive_breadth, elg_positive_breadth,
            retail_positive_breadth, price_positive_breadth, top5_main_net_share,
            leader_ts_code, leader_name, leader_main_net_yuan, source_mode,
            source_snapshot_id, quality_flag, computed_at
        )
        SELECT
            a.trade_date, a.l1_code, a.l1_name, a.l2_code, a.l2_name,
            a.member_count, a.matched_stock_count,
            CASE WHEN a.member_count > 0 THEN a.matched_stock_count::float / a.member_count END,
            a.sector_amount_yuan,
            a.sector_return_equal_weight,
            a.sector_return_amount_weight,
            COALESCE(sw.pct_change::float, a.sector_return_amount_weight, a.sector_return_equal_weight),
            a.sm_net_yuan, a.md_net_yuan, a.lg_net_yuan, a.elg_net_yuan, a.main_net_yuan,
            a.retail_net_yuan, a.net_mf_amount_yuan,
            CASE WHEN a.sector_amount_yuan > 0 THEN a.main_net_yuan::float / a.sector_amount_yuan END,
            CASE WHEN a.sector_amount_yuan > 0 THEN a.retail_net_yuan::float / a.sector_amount_yuan END,
            CASE WHEN a.sector_amount_yuan > 0 THEN a.elg_net_yuan::float / a.sector_amount_yuan END,
            a.flow_breadth, a.main_positive_breadth, a.elg_positive_breadth,
            a.retail_positive_breadth, a.price_positive_breadth,
            CASE WHEN a.main_net_yuan > 0 THEN COALESCE(t.top5_main_net_yuan, 0) / a.main_net_yuan END,
            l.leader_ts_code, l.leader_name, l.leader_main_net_yuan,
            :source_mode,
            :run_id,
            CASE
              WHEN a.member_count = 0 THEN 'degraded'
              WHEN a.matched_stock_count::float / NULLIF(a.member_count, 0) < 0.80 THEN 'degraded'
              WHEN COALESCE(sw.pct_change::float, a.sector_return_amount_weight, a.sector_return_equal_weight) IS NULL THEN 'degraded'
              ELSE 'ok'
            END,
            now()
        FROM agg a
        LEFT JOIN leader l ON l.l2_code = a.l2_code
        LEFT JOIN top5 t ON t.l2_code = a.l2_code
        LEFT JOIN smartmoney.raw_sw_daily sw ON sw.trade_date = a.trade_date AND sw.ts_code = a.l2_code
        ON CONFLICT (trade_date, l2_code) DO UPDATE SET
            l1_code = EXCLUDED.l1_code,
            l1_name = EXCLUDED.l1_name,
            l2_name = EXCLUDED.l2_name,
            member_count = EXCLUDED.member_count,
            matched_stock_count = EXCLUDED.matched_stock_count,
            coverage_ratio = EXCLUDED.coverage_ratio,
            sector_amount_yuan = EXCLUDED.sector_amount_yuan,
            sector_return_equal_weight = EXCLUDED.sector_return_equal_weight,
            sector_return_amount_weight = EXCLUDED.sector_return_amount_weight,
            sector_return_sw_index = EXCLUDED.sector_return_sw_index,
            sm_net_yuan = EXCLUDED.sm_net_yuan,
            md_net_yuan = EXCLUDED.md_net_yuan,
            lg_net_yuan = EXCLUDED.lg_net_yuan,
            elg_net_yuan = EXCLUDED.elg_net_yuan,
            main_net_yuan = EXCLUDED.main_net_yuan,
            retail_net_yuan = EXCLUDED.retail_net_yuan,
            net_mf_amount_yuan = EXCLUDED.net_mf_amount_yuan,
            main_net_ratio = EXCLUDED.main_net_ratio,
            retail_net_ratio = EXCLUDED.retail_net_ratio,
            elg_net_ratio = EXCLUDED.elg_net_ratio,
            flow_breadth = EXCLUDED.flow_breadth,
            main_positive_breadth = EXCLUDED.main_positive_breadth,
            elg_positive_breadth = EXCLUDED.elg_positive_breadth,
            retail_positive_breadth = EXCLUDED.retail_positive_breadth,
            price_positive_breadth = EXCLUDED.price_positive_breadth,
            top5_main_net_share = EXCLUDED.top5_main_net_share,
            leader_ts_code = EXCLUDED.leader_ts_code,
            leader_name = EXCLUDED.leader_name,
            leader_main_net_yuan = EXCLUDED.leader_main_net_yuan,
            source_mode = EXCLUDED.source_mode,
            source_snapshot_id = EXCLUDED.source_snapshot_id,
            quality_flag = EXCLUDED.quality_flag,
            computed_at = now()
    """)
    with engine.begin() as conn:
        result = conn.execute(sql, {"d": trade_date, "source_mode": source_mode, "run_id": run_id})
    return int(result.rowcount or 0)
