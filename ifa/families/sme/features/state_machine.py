"""Rule-based SME sector state machine."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text


def compute_state_range(engine, *, start: dt.date, end: dt.date) -> int:
    sql = text("""
        WITH x AS (
            SELECT
                so.trade_date,
                so.l2_code,
                so.l2_name,
                so.main_net_ratio,
                so.retail_net_ratio,
                so.main_positive_breadth,
                so.price_positive_breadth,
                so.top5_main_net_share,
                so.sector_return_sw_index,
                d.diffusion_phase,
                d.diffusion_score
            FROM sme.sme_sector_orderflow_daily so
            LEFT JOIN sme.sme_sector_diffusion_daily d
              ON d.trade_date = so.trade_date AND d.l2_code = so.l2_code
            WHERE so.trade_date BETWEEN :start AND :end
        ),
        final AS (
            SELECT *,
                CASE
                  WHEN main_net_ratio < 0 AND COALESCE(sector_return_sw_index, 0) > 0 THEN 'distribution'
                  WHEN main_net_ratio < 0 AND COALESCE(sector_return_sw_index, 0) <= 0 THEN 'cooldown'
                  WHEN COALESCE(main_net_ratio, 0) > 0.03 AND COALESCE(diffusion_score, 0) >= 0.65 AND COALESCE(sector_return_sw_index, 0) > 2 THEN 'acceleration'
                  WHEN COALESCE(main_net_ratio, 0) > 0.01 AND COALESCE(diffusion_score, 0) >= 0.50 THEN 'diffusion'
                  WHEN COALESCE(main_net_ratio, 0) > 0.01 AND COALESCE(sector_return_sw_index, 0) >= 0 THEN 'ignition'
                  WHEN COALESCE(main_net_ratio, 0) > 0 AND COALESCE(sector_return_sw_index, 0) < -1 THEN 'rebound'
                  WHEN COALESCE(main_net_ratio, 0) > 0 AND COALESCE(sector_return_sw_index, 0) < 0 THEN 'dormant'
                  ELSE 'dormant'
                END AS current_state,
                LEAST(1.0, GREATEST(0.0,
                    COALESCE(main_positive_breadth, 0) * 0.30
                    + COALESCE(diffusion_score, 0) * 0.35
                    + CASE WHEN COALESCE(main_net_ratio, 0) > 0 THEN LEAST(0.25, COALESCE(main_net_ratio, 0) * 5.0) ELSE 0 END
                    + CASE WHEN COALESCE(sector_return_sw_index, 0) > 0 THEN 0.10 ELSE 0 END
                )) AS state_score
            FROM x
        )
        INSERT INTO sme.sme_sector_state_daily (
            trade_date, l2_code, l2_name, current_state, state_score, state_confidence,
            transition_hint, risk_flags_json, evidence_json, quality_flag, computed_at
        )
        SELECT
            trade_date, l2_code, l2_name, current_state, state_score,
            CASE WHEN state_score >= 0.70 THEN 0.80 WHEN state_score >= 0.45 THEN 0.65 ELSE 0.50 END,
            CASE
              WHEN current_state IN ('ignition', 'diffusion', 'rebound') THEN 'watch_heat_up'
              WHEN current_state IN ('distribution', 'cooldown') THEN 'watch_risk'
              WHEN current_state = 'acceleration' THEN 'watch_crowding'
              ELSE 'watch'
            END,
            jsonb_build_array(
                CASE WHEN COALESCE(top5_main_net_share, 0) > 0.75 THEN 'flow_concentrated' ELSE NULL END,
                CASE WHEN current_state = 'distribution' THEN 'main_out_price_up' ELSE NULL END,
                CASE WHEN COALESCE(retail_net_ratio, 0) > 0 AND COALESCE(main_net_ratio, 0) <= 0 THEN 'retail_chase' ELSE NULL END,
                CASE WHEN current_state = 'acceleration' AND COALESCE(top5_main_net_share, 0) > 0.60 THEN 'leader_crowded' ELSE NULL END
            ) - 'null',
            jsonb_build_array(
                'main_net_ratio=' || COALESCE(ROUND(main_net_ratio::numeric, 4)::text, 'null'),
                'retail_net_ratio=' || COALESCE(ROUND(retail_net_ratio::numeric, 4)::text, 'null'),
                'breadth=' || COALESCE(ROUND(main_positive_breadth::numeric, 4)::text, 'null'),
                'diffusion=' || COALESCE(diffusion_phase, 'unknown'),
                'top5_share=' || COALESCE(ROUND(top5_main_net_share::numeric, 4)::text, 'null')
            ),
            'ok',
            now()
        FROM final
        ON CONFLICT (trade_date, l2_code) DO UPDATE SET
            l2_name = EXCLUDED.l2_name,
            current_state = EXCLUDED.current_state,
            state_score = EXCLUDED.state_score,
            state_confidence = EXCLUDED.state_confidence,
            transition_hint = EXCLUDED.transition_hint,
            risk_flags_json = EXCLUDED.risk_flags_json,
            evidence_json = EXCLUDED.evidence_json,
            quality_flag = EXCLUDED.quality_flag,
            computed_at = now()
    """)
    with engine.begin() as conn:
        result = conn.execute(sql, {"start": start, "end": end})
    return int(result.rowcount or 0)


def compute_state(engine, *, trade_date: dt.date) -> int:
    return compute_state_range(engine, start=trade_date, end=trade_date)
