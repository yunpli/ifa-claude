"""Evaluate persisted SME strategy buckets against forward labels."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text

from ifa.families.sme.versions import SME_STRATEGY_EVAL_LOGIC_VERSION


DEFAULT_HORIZONS = (1, 3, 5, 10, 20)


def compute_strategy_eval(
    engine,
    *,
    start: dt.date,
    end: dt.date,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> int:
    """Compute tuning-ready realized outcomes for market-structure buckets.

    Each persisted market-structure snapshot says "focus/watch/avoid" for a set
    of SW L2 sectors. This evaluator joins those buckets to mature forward
    labels on the same trade date. For long buckets, positive market excess is
    good; for avoid/crowding buckets, negative market excess is good. The
    `avg_signal_score` column normalizes that sign so higher is always better.
    """
    total = 0
    sql_delete = text("""
        DELETE FROM sme.sme_strategy_eval_daily
        WHERE trade_date BETWEEN :start AND :end
          AND strategy_name = 'market_structure_v1'
          AND horizon = :h
    """)
    sql_insert = text("""
        WITH buckets AS (
            SELECT trade_date, logic_version, 'primary' AS bucket, 'long' AS direction,
                   value->>'l2_code' AS l2_code, value->>'l2_name' AS l2_name
            FROM sme.sme_market_structure_daily, jsonb_array_elements(primary_directions_json)
            WHERE trade_date BETWEEN :start AND :end
            UNION ALL
            SELECT trade_date, logic_version, 'secondary', 'long',
                   value->>'l2_code', value->>'l2_name'
            FROM sme.sme_market_structure_daily, jsonb_array_elements(secondary_directions_json)
            WHERE trade_date BETWEEN :start AND :end
            UNION ALL
            SELECT trade_date, logic_version, 'defensive', 'long',
                   value->>'l2_code', value->>'l2_name'
            FROM sme.sme_market_structure_daily, jsonb_array_elements(defensive_directions_json)
            WHERE trade_date BETWEEN :start AND :end
            UNION ALL
            SELECT trade_date, logic_version, 'repair', 'long',
                   value->>'l2_code', value->>'l2_name'
            FROM sme.sme_market_structure_daily, jsonb_array_elements(repair_directions_json)
            WHERE trade_date BETWEEN :start AND :end
            UNION ALL
            SELECT trade_date, logic_version, 'avoid', 'avoid',
                   value->>'l2_code', value->>'l2_name'
            FROM sme.sme_market_structure_daily, jsonb_array_elements(avoid_directions_json)
            WHERE trade_date BETWEEN :start AND :end
            UNION ALL
            SELECT trade_date, logic_version, 'crowding_risk', 'avoid',
                   value->>'l2_code', value->>'l2_name'
            FROM sme.sme_market_structure_daily, jsonb_array_elements(crowding_risk_json)
            WHERE trade_date BETWEEN :start AND :end
        ),
        joined AS (
            SELECT
                b.trade_date, b.logic_version, b.bucket, b.direction, b.l2_code, b.l2_name,
                l.future_return,
                l.future_excess_return_vs_market,
                l.future_excess_return_vs_l1,
                CASE
                  WHEN b.direction = 'avoid' THEN -l.future_excess_return_vs_market
                  ELSE l.future_excess_return_vs_market
                END AS signal_score,
                CASE
                  WHEN b.direction = 'avoid' THEN l.future_excess_return_vs_market < 0
                  ELSE l.future_excess_return_vs_market > 0
                END AS success,
                l.future_top_quantile_label,
                l.future_heat_up_label,
                l.future_drawdown,
                l.future_max_runup
            FROM buckets b
            JOIN sme.sme_labels_daily l
              ON l.trade_date = b.trade_date
             AND l.l2_code = b.l2_code
             AND l.horizon = :h
            WHERE b.l2_code IS NOT NULL
        ),
        agg AS (
            SELECT
                trade_date,
                'market_structure_v1' AS strategy_name,
                bucket,
                CAST(:h AS int) AS horizon,
                direction,
                MAX(logic_version) AS source_logic_version,
                COUNT(*)::int AS signal_count,
                AVG(future_return)::float AS avg_future_return,
                AVG(future_excess_return_vs_market)::float AS avg_future_excess_return_vs_market,
                AVG(future_excess_return_vs_l1)::float AS avg_future_excess_return_vs_l1,
                AVG(signal_score)::float AS avg_signal_score,
                AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END)::float AS success_rate,
                AVG(CASE WHEN future_top_quantile_label THEN 1.0 ELSE 0.0 END)::float AS top_quantile_rate,
                AVG(CASE WHEN future_heat_up_label THEN 1.0 ELSE 0.0 END)::float AS heat_up_rate,
                AVG(future_drawdown)::float AS avg_future_drawdown,
                AVG(future_max_runup)::float AS avg_future_max_runup,
                jsonb_agg(jsonb_build_object('l2_code', l2_code, 'l2_name', l2_name) ORDER BY l2_code) AS l2_codes_json
            FROM joined
            GROUP BY trade_date, bucket, direction
        )
        INSERT INTO sme.sme_strategy_eval_daily (
            trade_date, strategy_name, bucket, horizon, direction, logic_version,
            signal_count, avg_future_return, avg_future_excess_return_vs_market,
            avg_future_excess_return_vs_l1, avg_signal_score, success_rate,
            top_quantile_rate, heat_up_rate, avg_future_drawdown,
            avg_future_max_runup, l2_codes_json, quality_flag, computed_at
        )
        SELECT
            trade_date, strategy_name, bucket, horizon, direction,
            :logic_version || '/' || source_logic_version,
            signal_count, avg_future_return, avg_future_excess_return_vs_market,
            avg_future_excess_return_vs_l1, avg_signal_score, success_rate,
            top_quantile_rate, heat_up_rate, avg_future_drawdown,
            avg_future_max_runup, COALESCE(l2_codes_json, '[]'::jsonb),
            CASE WHEN signal_count >= 3 THEN 'ok' ELSE 'degraded' END,
            now()
        FROM agg
        ON CONFLICT (trade_date, strategy_name, bucket, horizon) DO UPDATE SET
            direction = EXCLUDED.direction,
            logic_version = EXCLUDED.logic_version,
            signal_count = EXCLUDED.signal_count,
            avg_future_return = EXCLUDED.avg_future_return,
            avg_future_excess_return_vs_market = EXCLUDED.avg_future_excess_return_vs_market,
            avg_future_excess_return_vs_l1 = EXCLUDED.avg_future_excess_return_vs_l1,
            avg_signal_score = EXCLUDED.avg_signal_score,
            success_rate = EXCLUDED.success_rate,
            top_quantile_rate = EXCLUDED.top_quantile_rate,
            heat_up_rate = EXCLUDED.heat_up_rate,
            avg_future_drawdown = EXCLUDED.avg_future_drawdown,
            avg_future_max_runup = EXCLUDED.avg_future_max_runup,
            l2_codes_json = EXCLUDED.l2_codes_json,
            quality_flag = EXCLUDED.quality_flag,
            computed_at = now()
    """)
    with engine.begin() as conn:
        for h in horizons:
            conn.execute(sql_delete, {"start": start, "end": end, "h": h})
            result = conn.execute(sql_insert, {
                "start": start,
                "end": end,
                "h": h,
                "logic_version": SME_STRATEGY_EVAL_LOGIC_VERSION,
            })
            total += int(result.rowcount or 0)
    return total


def summarize_strategy_eval(engine, *, start: dt.date, end: dt.date) -> list[dict]:
    """Return compact aggregate performance for quick tuning triage."""
    sql = text("""
        SELECT
            strategy_name,
            bucket,
            horizon,
            direction,
            COUNT(*)::int AS sample_days,
            SUM(signal_count)::int AS signal_count,
            AVG(avg_signal_score)::float AS avg_signal_score,
            AVG(success_rate)::float AS avg_success_rate,
            AVG(top_quantile_rate)::float AS avg_top_quantile_rate,
            AVG(avg_future_excess_return_vs_market)::float AS avg_excess_market
        FROM sme.sme_strategy_eval_daily
        WHERE trade_date BETWEEN :start AND :end
        GROUP BY strategy_name, bucket, horizon, direction
        ORDER BY horizon, avg_signal_score DESC NULLS LAST
    """)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(sql, {"start": start, "end": end}).mappings().all()]
