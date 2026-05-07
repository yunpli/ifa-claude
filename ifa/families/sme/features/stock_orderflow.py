"""Stock-level orderflow features."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text


def compute_stock_orderflow(engine, *, trade_date: dt.date, source_mode: str = "prefer_smartmoney", run_id: str | None = None) -> int:
    sql = text("""
        /*
        Tushare moneyflow has two different concepts that must not be
        reconciled against each other:
        - net_mf_amount is Tushare's official net inflow amount.
        - buy/sell amounts by order size are turnover buckets. Empirically the
          four bucket buy-sell nets usually sum close to zero because every
          trade has both sides; they are useful for size-class pressure, not as
          a recomputation of net_mf_amount.

        Therefore net_recomputed_yuan is kept as an order-bucket balance check
        and reconciliation_error_yuan stores that balance error. It is not
        official net inflow minus recomputed net inflow.
        */
        WITH src AS (
            SELECT
                mf.trade_date,
                mf.ts_code,
                d.open::float AS open_yuan,
                d.high::float AS high_yuan,
                d.low::float AS low_yuan,
                d.close::float AS close_yuan,
                d.pct_chg::float AS pct_chg,
                ROUND(d.amount * 1000)::bigint AS amount_yuan,
                db.turnover_rate::float AS turnover_rate,
                db.volume_ratio::float AS volume_ratio,
                ROUND(db.total_mv * 10000)::bigint AS total_mv_yuan,
                ROUND(db.circ_mv * 10000)::bigint AS circ_mv_yuan,
                ROUND(COALESCE(mf.buy_sm_amount, 0) * 10000)::bigint AS buy_sm_amount_yuan,
                ROUND(COALESCE(mf.sell_sm_amount, 0) * 10000)::bigint AS sell_sm_amount_yuan,
                ROUND(COALESCE(mf.buy_md_amount, 0) * 10000)::bigint AS buy_md_amount_yuan,
                ROUND(COALESCE(mf.sell_md_amount, 0) * 10000)::bigint AS sell_md_amount_yuan,
                ROUND(COALESCE(mf.buy_lg_amount, 0) * 10000)::bigint AS buy_lg_amount_yuan,
                ROUND(COALESCE(mf.sell_lg_amount, 0) * 10000)::bigint AS sell_lg_amount_yuan,
                ROUND(COALESCE(mf.buy_elg_amount, 0) * 10000)::bigint AS buy_elg_amount_yuan,
                ROUND(COALESCE(mf.sell_elg_amount, 0) * 10000)::bigint AS sell_elg_amount_yuan,
                ROUND(COALESCE(mf.net_mf_amount, 0) * 10000)::bigint AS net_mf_amount_yuan
            FROM smartmoney.raw_moneyflow mf
            LEFT JOIN smartmoney.raw_daily d
              ON d.trade_date = mf.trade_date AND d.ts_code = mf.ts_code
            LEFT JOIN smartmoney.raw_daily_basic db
              ON db.trade_date = mf.trade_date AND db.ts_code = mf.ts_code
            WHERE mf.trade_date = :d
        ),
        calc AS (
            SELECT
                *,
                buy_sm_amount_yuan - sell_sm_amount_yuan AS sm_net_yuan,
                buy_md_amount_yuan - sell_md_amount_yuan AS md_net_yuan,
                buy_lg_amount_yuan - sell_lg_amount_yuan AS lg_net_yuan,
                buy_elg_amount_yuan - sell_elg_amount_yuan AS elg_net_yuan
            FROM src
        ),
        final AS (
            SELECT
                *,
                lg_net_yuan + elg_net_yuan AS main_net_yuan,
                sm_net_yuan + md_net_yuan AS retail_net_yuan,
                sm_net_yuan + md_net_yuan + lg_net_yuan + elg_net_yuan AS net_recomputed_yuan
            FROM calc
        )
        INSERT INTO sme.sme_stock_orderflow_daily (
            trade_date, ts_code, open_yuan, high_yuan, low_yuan, close_yuan, pct_chg,
            amount_yuan, turnover_rate, volume_ratio, total_mv_yuan, circ_mv_yuan,
            buy_sm_amount_yuan, sell_sm_amount_yuan, buy_md_amount_yuan, sell_md_amount_yuan,
            buy_lg_amount_yuan, sell_lg_amount_yuan, buy_elg_amount_yuan, sell_elg_amount_yuan,
            sm_net_yuan, md_net_yuan, lg_net_yuan, elg_net_yuan, main_net_yuan,
            retail_net_yuan, net_mf_amount_yuan, net_recomputed_yuan,
            main_net_ratio, retail_net_ratio, elg_net_ratio, behavior_flags_json,
            reconciliation_error_yuan, source_mode, source_snapshot_id, quality_flag, computed_at
        )
        SELECT
            trade_date, ts_code, open_yuan, high_yuan, low_yuan, close_yuan, pct_chg,
            amount_yuan, turnover_rate, volume_ratio, total_mv_yuan, circ_mv_yuan,
            buy_sm_amount_yuan, sell_sm_amount_yuan, buy_md_amount_yuan, sell_md_amount_yuan,
            buy_lg_amount_yuan, sell_lg_amount_yuan, buy_elg_amount_yuan, sell_elg_amount_yuan,
            sm_net_yuan, md_net_yuan, lg_net_yuan, elg_net_yuan, main_net_yuan,
            retail_net_yuan, net_mf_amount_yuan, net_recomputed_yuan,
            CASE WHEN amount_yuan > 0 THEN main_net_yuan::float / amount_yuan END,
            CASE WHEN amount_yuan > 0 THEN retail_net_yuan::float / amount_yuan END,
            CASE WHEN amount_yuan > 0 THEN elg_net_yuan::float / amount_yuan END,
            jsonb_build_object(
                'true_accumulation', main_net_yuan > 0 AND retail_net_yuan < 0 AND COALESCE(pct_chg, 0) >= 0,
                'silent_accumulation', main_net_yuan > 0 AND COALESCE(pct_chg, 0) <= 0 AND COALESCE(turnover_rate, 0) < 8,
                'retail_chase', retail_net_yuan > 0 AND main_net_yuan <= 0 AND COALESCE(pct_chg, 0) > 0,
                'distribution', main_net_yuan < 0 AND COALESCE(pct_chg, 0) > 0,
                'panic_absorb', main_net_yuan > 0 AND COALESCE(pct_chg, 0) < -2,
                'fake_inflow', net_mf_amount_yuan > 0 AND main_net_yuan <= 0
            ),
            net_recomputed_yuan,
            :source_mode,
            :run_id,
            CASE
              WHEN amount_yuan IS NULL THEN 'degraded'
              WHEN pct_chg IS NULL THEN 'degraded'
              WHEN amount_yuan < 0 THEN 'degraded'
              ELSE 'ok'
            END,
            now()
        FROM final
        ON CONFLICT (trade_date, ts_code) DO UPDATE SET
            open_yuan = EXCLUDED.open_yuan,
            high_yuan = EXCLUDED.high_yuan,
            low_yuan = EXCLUDED.low_yuan,
            close_yuan = EXCLUDED.close_yuan,
            pct_chg = EXCLUDED.pct_chg,
            amount_yuan = EXCLUDED.amount_yuan,
            turnover_rate = EXCLUDED.turnover_rate,
            volume_ratio = EXCLUDED.volume_ratio,
            total_mv_yuan = EXCLUDED.total_mv_yuan,
            circ_mv_yuan = EXCLUDED.circ_mv_yuan,
            buy_sm_amount_yuan = EXCLUDED.buy_sm_amount_yuan,
            sell_sm_amount_yuan = EXCLUDED.sell_sm_amount_yuan,
            buy_md_amount_yuan = EXCLUDED.buy_md_amount_yuan,
            sell_md_amount_yuan = EXCLUDED.sell_md_amount_yuan,
            buy_lg_amount_yuan = EXCLUDED.buy_lg_amount_yuan,
            sell_lg_amount_yuan = EXCLUDED.sell_lg_amount_yuan,
            buy_elg_amount_yuan = EXCLUDED.buy_elg_amount_yuan,
            sell_elg_amount_yuan = EXCLUDED.sell_elg_amount_yuan,
            sm_net_yuan = EXCLUDED.sm_net_yuan,
            md_net_yuan = EXCLUDED.md_net_yuan,
            lg_net_yuan = EXCLUDED.lg_net_yuan,
            elg_net_yuan = EXCLUDED.elg_net_yuan,
            main_net_yuan = EXCLUDED.main_net_yuan,
            retail_net_yuan = EXCLUDED.retail_net_yuan,
            net_mf_amount_yuan = EXCLUDED.net_mf_amount_yuan,
            net_recomputed_yuan = EXCLUDED.net_recomputed_yuan,
            main_net_ratio = EXCLUDED.main_net_ratio,
            retail_net_ratio = EXCLUDED.retail_net_ratio,
            elg_net_ratio = EXCLUDED.elg_net_ratio,
            behavior_flags_json = EXCLUDED.behavior_flags_json,
            reconciliation_error_yuan = EXCLUDED.reconciliation_error_yuan,
            source_mode = EXCLUDED.source_mode,
            source_snapshot_id = EXCLUDED.source_snapshot_id,
            quality_flag = EXCLUDED.quality_flag,
            computed_at = now()
    """)
    with engine.begin() as conn:
        result = conn.execute(sql, {"d": trade_date, "source_mode": source_mode, "run_id": run_id})
    return int(result.rowcount or 0)
