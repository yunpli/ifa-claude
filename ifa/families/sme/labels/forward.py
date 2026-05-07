"""Forward labels for SME sector research."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text


def compute_labels(engine, *, start: dt.date, end: dt.date, horizons: tuple[int, ...] = (1, 3, 5, 10, 20)) -> int:
    total = 0
    for h in horizons:
        delete_sql = text("""
            DELETE FROM sme.sme_labels_daily
            WHERE trade_date BETWEEN :start AND :end
              AND horizon = :h
        """)
        sql = text("""
            /*
            Horizon labels are trading-day labels. The ROW_NUMBER ordering below
            is per L2 sector observation, so "next h" means the next h available
            trading observations for that sector. Rows with NULL return labels
            are excluded instead of written as ok labels.
            */
            WITH panel AS (
                SELECT
                    trade_date,
                    l1_code,
                    l2_code,
                    l2_name,
                    COALESCE(sector_return_sw_index, sector_return_amount_weight, sector_return_equal_weight) AS label_return,
                    main_net_ratio,
                    main_positive_breadth,
                    ROW_NUMBER() OVER (PARTITION BY l2_code ORDER BY trade_date) AS rn
                FROM sme.sme_sector_orderflow_daily
                WHERE COALESCE(sector_return_sw_index, sector_return_amount_weight, sector_return_equal_weight) IS NOT NULL
            ),
            fwd AS (
                SELECT
                    p.trade_date,
                    p.l1_code,
                    p.l2_code,
                    p.l2_name,
                    ((EXP(SUM(LN(GREATEST(0.0001, 1 + n.label_return / 100.0)))) - 1) * 100.0)::float AS future_return,
                    MAX(n.label_return)::float AS future_max_runup,
                    MIN(n.label_return)::float AS future_drawdown,
                    AVG(COALESCE(n.main_net_ratio, 0) + COALESCE(n.main_positive_breadth, 0))::float AS future_heat,
                    COALESCE(p.main_net_ratio, 0) + COALESCE(p.main_positive_breadth, 0) AS current_heat
                FROM panel p
                JOIN panel n
                  ON n.l2_code = p.l2_code
                 AND n.rn > p.rn
                 AND n.rn <= p.rn + :h
                WHERE p.trade_date BETWEEN :start AND :end
                GROUP BY p.trade_date, p.l1_code, p.l2_code, p.l2_name, p.main_net_ratio, p.main_positive_breadth
                HAVING COUNT(*) = :h
            ),
            ranked AS (
                SELECT
                    *,
                    PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY future_return) AS future_rank_pct,
                    AVG(future_return) OVER (PARTITION BY trade_date) AS market_avg,
                    AVG(future_return) OVER (PARTITION BY trade_date, l1_code) AS l1_avg
                FROM fwd
            )
            INSERT INTO sme.sme_labels_daily (
                trade_date, l2_code, horizon, future_return,
                future_excess_return_vs_market, future_excess_return_vs_l1,
                future_rank_pct, future_top_quantile_label, future_heat_delta,
                future_heat_up_label, future_drawdown, future_max_runup,
                turnover_adjusted_return, label_quality_flag, computed_at
            )
            SELECT
                trade_date, l2_code, :h, future_return,
                future_return - market_avg,
                future_return - l1_avg,
                future_rank_pct,
                future_rank_pct >= 0.80,
                future_heat - current_heat,
                future_heat - current_heat > 0,
                future_drawdown,
                future_max_runup,
                future_return,
                'ok',
                now()
            FROM ranked
            ON CONFLICT (trade_date, l2_code, horizon) DO UPDATE SET
                future_return = EXCLUDED.future_return,
                future_excess_return_vs_market = EXCLUDED.future_excess_return_vs_market,
                future_excess_return_vs_l1 = EXCLUDED.future_excess_return_vs_l1,
                future_rank_pct = EXCLUDED.future_rank_pct,
                future_top_quantile_label = EXCLUDED.future_top_quantile_label,
                future_heat_delta = EXCLUDED.future_heat_delta,
                future_heat_up_label = EXCLUDED.future_heat_up_label,
                future_drawdown = EXCLUDED.future_drawdown,
                future_max_runup = EXCLUDED.future_max_runup,
                turnover_adjusted_return = EXCLUDED.turnover_adjusted_return,
                label_quality_flag = EXCLUDED.label_quality_flag,
                computed_at = now()
        """)
        with engine.begin() as conn:
            conn.execute(delete_sql, {"start": start, "end": end, "h": h})
            result = conn.execute(sql, {"start": start, "end": end, "h": h})
        total += int(result.rowcount or 0)
    return total
