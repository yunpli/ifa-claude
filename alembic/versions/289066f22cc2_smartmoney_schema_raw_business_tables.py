"""smartmoney schema raw + business tables

Revision ID: 289066f22cc2
Revises: bd55ccfce008
Create Date: 2026-05-01 00:53:44.416245

Creates the dedicated `smartmoney` PostgreSQL schema and all tables for the
Smart Money Flow Intelligence module.

Layout:
  schema `smartmoney` contains:
    raw_*        — TuShare raw data caches (20 tables; never touched by reports
                   directly, only by factor computation)
    factor_daily, market_state_daily, sector_state_daily,
    stock_signals_daily, predictions_daily, param_versions,
    backtest_runs, backtest_metrics  — business / computed tables

Field shapes verified against live TuShare probes on 2026-04-30.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "289066f22cc2"
down_revision: str | Sequence[str] | None = "bd55ccfce008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "smartmoney"


def _ck(name: str, column: str, values: list[str]) -> sa.CheckConstraint:
    rendered = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({rendered})", name=name)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ─── RAW TABLES (TuShare caches; identical to TuShare column shapes) ────

    # 1. raw_daily — A股全市场日线
    op.create_table(
        "raw_daily",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("open", sa.Numeric(14, 4)),
        sa.Column("high", sa.Numeric(14, 4)),
        sa.Column("low", sa.Numeric(14, 4)),
        sa.Column("close", sa.Numeric(14, 4)),
        sa.Column("pre_close", sa.Numeric(14, 4)),
        sa.Column("change_", sa.Numeric(14, 4)),
        sa.Column("pct_chg", sa.Numeric(10, 4)),
        sa.Column("vol", sa.Numeric(20, 2)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_daily"),
        schema=SCHEMA,
    )

    # 2. raw_daily_basic — 日级指标（换手 / 估值 / 市值）
    op.create_table(
        "raw_daily_basic",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("close", sa.Numeric(14, 4)),
        sa.Column("turnover_rate", sa.Numeric(10, 4)),
        sa.Column("turnover_rate_f", sa.Numeric(10, 4)),
        sa.Column("volume_ratio", sa.Numeric(10, 4)),
        sa.Column("pe", sa.Numeric(14, 4)),
        sa.Column("pe_ttm", sa.Numeric(14, 4)),
        sa.Column("pb", sa.Numeric(14, 4)),
        sa.Column("ps", sa.Numeric(14, 4)),
        sa.Column("ps_ttm", sa.Numeric(14, 4)),
        sa.Column("dv_ratio", sa.Numeric(10, 4)),
        sa.Column("dv_ttm", sa.Numeric(10, 4)),
        sa.Column("total_share", sa.Numeric(20, 4)),
        sa.Column("float_share", sa.Numeric(20, 4)),
        sa.Column("free_share", sa.Numeric(20, 4)),
        sa.Column("total_mv", sa.Numeric(20, 4)),
        sa.Column("circ_mv", sa.Numeric(20, 4)),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_daily_basic"),
        schema=SCHEMA,
    )

    # 3. raw_moneyflow — 个股主力资金流（小/中/大/超大单）
    op.create_table(
        "raw_moneyflow",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("buy_sm_vol", sa.Numeric(20, 0)),
        sa.Column("buy_sm_amount", sa.Numeric(20, 4)),
        sa.Column("sell_sm_vol", sa.Numeric(20, 0)),
        sa.Column("sell_sm_amount", sa.Numeric(20, 4)),
        sa.Column("buy_md_vol", sa.Numeric(20, 0)),
        sa.Column("buy_md_amount", sa.Numeric(20, 4)),
        sa.Column("sell_md_vol", sa.Numeric(20, 0)),
        sa.Column("sell_md_amount", sa.Numeric(20, 4)),
        sa.Column("buy_lg_vol", sa.Numeric(20, 0)),
        sa.Column("buy_lg_amount", sa.Numeric(20, 4)),
        sa.Column("sell_lg_vol", sa.Numeric(20, 0)),
        sa.Column("sell_lg_amount", sa.Numeric(20, 4)),
        sa.Column("buy_elg_vol", sa.Numeric(20, 0)),
        sa.Column("buy_elg_amount", sa.Numeric(20, 4)),
        sa.Column("sell_elg_vol", sa.Numeric(20, 0)),
        sa.Column("sell_elg_amount", sa.Numeric(20, 4)),
        sa.Column("net_mf_vol", sa.Numeric(20, 0)),
        sa.Column("net_mf_amount", sa.Numeric(20, 4)),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_moneyflow"),
        schema=SCHEMA,
    )

    # 4. raw_moneyflow_ind_dc — 东财板块级资金流 ⭐
    op.create_table(
        "raw_moneyflow_ind_dc",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("content_type", sa.Text),  # 地域 / 行业 / 概念
        sa.Column("name", sa.Text),
        sa.Column("pct_change", sa.Numeric(10, 4)),
        sa.Column("close", sa.Numeric(20, 4)),
        sa.Column("net_amount", sa.Numeric(20, 2)),
        sa.Column("net_amount_rate", sa.Numeric(10, 4)),
        sa.Column("buy_elg_amount", sa.Numeric(20, 2)),
        sa.Column("buy_elg_amount_rate", sa.Numeric(10, 4)),
        sa.Column("buy_lg_amount", sa.Numeric(20, 2)),
        sa.Column("buy_lg_amount_rate", sa.Numeric(10, 4)),
        sa.Column("buy_md_amount", sa.Numeric(20, 2)),
        sa.Column("buy_md_amount_rate", sa.Numeric(10, 4)),
        sa.Column("buy_sm_amount", sa.Numeric(20, 2)),
        sa.Column("buy_sm_amount_rate", sa.Numeric(10, 4)),
        sa.Column("buy_sm_amount_stock", sa.Text),  # 领涨股名称
        sa.Column("rank", sa.Integer),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_mf_dc"),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_raw_mf_dc_content", "raw_moneyflow_ind_dc",
                    ["content_type", "trade_date"], schema=SCHEMA)

    # 5. raw_moneyflow_ind_ths — 同花顺板块资金流
    op.create_table(
        "raw_moneyflow_ind_ths",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("industry", sa.Text),
        sa.Column("lead_stock", sa.Text),
        sa.Column("close", sa.Numeric(20, 4)),
        sa.Column("pct_change", sa.Numeric(10, 4)),
        sa.Column("company_num", sa.Integer),
        sa.Column("pct_change_stock", sa.Numeric(10, 4)),
        sa.Column("close_price", sa.Numeric(20, 4)),
        sa.Column("net_buy_amount", sa.Numeric(20, 2)),
        sa.Column("net_sell_amount", sa.Numeric(20, 2)),
        sa.Column("net_amount", sa.Numeric(20, 2)),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_mf_ths"),
        schema=SCHEMA,
    )

    # 6. raw_moneyflow_hsgt — 沪深港通资金流
    op.create_table(
        "raw_moneyflow_hsgt",
        sa.Column("trade_date", sa.Date, primary_key=True),
        sa.Column("ggt_ss", sa.Numeric(20, 2)),
        sa.Column("ggt_sz", sa.Numeric(20, 2)),
        sa.Column("hgt", sa.Numeric(20, 2)),
        sa.Column("sgt", sa.Numeric(20, 2)),
        sa.Column("north_money", sa.Numeric(20, 2)),
        sa.Column("south_money", sa.Numeric(20, 2)),
        schema=SCHEMA,
    )

    # 7. raw_margin — 两融
    op.create_table(
        "raw_margin",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("exchange_id", sa.Text, nullable=False),
        sa.Column("rzye", sa.Numeric(20, 2)),
        sa.Column("rzmre", sa.Numeric(20, 2)),
        sa.Column("rzche", sa.Numeric(20, 2)),
        sa.Column("rqye", sa.Numeric(20, 2)),
        sa.Column("rqmcl", sa.Numeric(20, 2)),
        sa.Column("rzrqye", sa.Numeric(20, 2)),
        sa.Column("rqyl", sa.Numeric(20, 2)),
        sa.PrimaryKeyConstraint("trade_date", "exchange_id", name="pk_sm_raw_margin"),
        schema=SCHEMA,
    )

    # 8. raw_limit_list_d — 涨跌停明细
    op.create_table(
        "raw_limit_list_d",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("industry", sa.Text),
        sa.Column("name", sa.Text),
        sa.Column("close", sa.Numeric(14, 4)),
        sa.Column("pct_chg", sa.Numeric(10, 4)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.Column("limit_amount", sa.Numeric(20, 4)),
        sa.Column("fc_ratio", sa.Numeric(10, 4)),
        sa.Column("fl_ratio", sa.Numeric(10, 4)),
        sa.Column("fd_amount", sa.Numeric(20, 4)),
        sa.Column("first_time", sa.Text),
        sa.Column("last_time", sa.Text),
        sa.Column("open_times", sa.Integer),
        sa.Column("up_stat", sa.Text),
        sa.Column("limit_times", sa.Integer),
        sa.Column("limit_", sa.Text),  # U / D / Z
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_limit"),
        schema=SCHEMA,
    )

    # 9. raw_kpl_concept — 开盘啦概念榜（today's hot concepts ranked by 涨停数）⭐
    op.create_table(
        "raw_kpl_concept",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("z_t_num", sa.Integer),
        sa.Column("up_num", sa.Integer),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_kpl_concept"),
        schema=SCHEMA,
    )

    # 10. raw_kpl_concept_cons — 开盘啦概念成分
    op.create_table(
        "raw_kpl_concept_cons",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("con_code", sa.Text, nullable=False),  # concept ts_code
        sa.Column("ts_code", sa.Text, nullable=False),   # stock ts_code
        sa.Column("name", sa.Text),
        sa.Column("con_name", sa.Text),
        sa.Column("desc", sa.Text),
        sa.Column("hot_num", sa.Integer),
        sa.PrimaryKeyConstraint("trade_date", "con_code", "ts_code",
                                name="pk_sm_raw_kpl_cons"),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_raw_kpl_cons_stock", "raw_kpl_concept_cons",
                    ["ts_code", "trade_date"], schema=SCHEMA)

    # 11. raw_kpl_list — 开盘啦榜单（涨停股完整生态）⭐
    op.create_table(
        "raw_kpl_list",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("lu_time", sa.Text),
        sa.Column("ld_time", sa.Text),
        sa.Column("open_time", sa.Text),
        sa.Column("last_time", sa.Text),
        sa.Column("lu_desc", sa.Text),  # "首板" / "2连板" / "3连板" / "4天2板" / 一字板 / 烂板
        sa.Column("tag", sa.Text),
        sa.Column("theme", sa.Text),    # "算力、数字经济" / "机器人概念、流感"
        sa.Column("net_change", sa.Numeric(20, 2)),
        sa.Column("bid_amount", sa.Numeric(20, 2)),
        sa.Column("status", sa.Text),
        sa.Column("bid_change", sa.Numeric(20, 2)),
        sa.Column("bid_turnover", sa.Numeric(10, 4)),
        sa.Column("lu_bid_vol", sa.Numeric(20, 2)),
        sa.Column("pct_chg", sa.Numeric(10, 4)),
        sa.Column("bid_pct_chg", sa.Numeric(10, 4)),
        sa.Column("rt_pct_chg", sa.Numeric(10, 4)),
        sa.Column("limit_order", sa.Numeric(20, 2)),
        sa.Column("amount", sa.Numeric(20, 2)),
        sa.Column("turnover_rate", sa.Numeric(10, 4)),
        sa.Column("free_float", sa.Numeric(20, 2)),
        sa.Column("lu_limit_order", sa.Numeric(20, 2)),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_kpl_list"),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_raw_kpl_list_lu_desc", "raw_kpl_list",
                    ["lu_desc", "trade_date"], schema=SCHEMA)

    # 12. raw_top_list — 龙虎榜个股
    op.create_table(
        "raw_top_list",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("close", sa.Numeric(14, 4)),
        sa.Column("pct_change", sa.Numeric(10, 4)),
        sa.Column("turnover_rate", sa.Numeric(10, 4)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.Column("l_sell", sa.Numeric(20, 4)),
        sa.Column("l_buy", sa.Numeric(20, 4)),
        sa.Column("l_amount", sa.Numeric(20, 4)),
        sa.Column("net_amount", sa.Numeric(20, 4)),
        sa.Column("net_rate", sa.Numeric(10, 4)),
        sa.Column("amount_rate", sa.Numeric(10, 4)),
        sa.Column("float_values", sa.Numeric(20, 4)),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "reason",
                                name="pk_sm_raw_top_list"),
        schema=SCHEMA,
    )

    # 13. raw_top_inst — 龙虎榜机构席位
    op.create_table(
        "raw_top_inst",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("exalter", sa.Text),     # 席位名称
        sa.Column("buy", sa.Numeric(20, 4)),
        sa.Column("buy_rate", sa.Numeric(10, 4)),
        sa.Column("sell", sa.Numeric(20, 4)),
        sa.Column("sell_rate", sa.Numeric(10, 4)),
        sa.Column("net_buy", sa.Numeric(20, 4)),
        sa.Column("side", sa.Text),
        sa.Column("reason", sa.Text),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_raw_top_inst_date_code", "raw_top_inst",
                    ["trade_date", "ts_code"], schema=SCHEMA)

    # 14. raw_ths_hot — 同花顺热榜
    op.create_table(
        "raw_ths_hot",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("data_type", sa.Text),     # 个股 / 概念 / etc.
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("ts_name", sa.Text),
        sa.Column("rank", sa.Integer),
        sa.Column("pct_change", sa.Numeric(10, 4)),
        sa.Column("current_price", sa.Numeric(14, 4)),
        sa.Column("hot", sa.Numeric(20, 2)),
        sa.Column("concept", sa.Text),
        sa.Column("rank_time", sa.Text),
        sa.Column("rank_reason", sa.Text),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_raw_ths_hot_date_type", "raw_ths_hot",
                    ["trade_date", "data_type", "rank"], schema=SCHEMA)

    # 15. raw_dc_hot — 东财热榜
    op.create_table(
        "raw_dc_hot",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("data_type", sa.Text),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("ts_name", sa.Text),
        sa.Column("rank", sa.Integer),
        sa.Column("pct_change", sa.Numeric(10, 4)),
        sa.Column("current_price", sa.Numeric(14, 4)),
        sa.Column("hot", sa.Numeric(20, 2)),
        sa.Column("concept", sa.Text),
        sa.Column("rank_time", sa.Text),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_raw_dc_hot_date_type", "raw_dc_hot",
                    ["trade_date", "data_type", "rank"], schema=SCHEMA)

    # 16. raw_dc_index — 东财概念指数
    op.create_table(
        "raw_dc_index",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("leading", sa.Text),         # 领涨股名
        sa.Column("leading_code", sa.Text),
        sa.Column("pct_change", sa.Numeric(10, 4)),
        sa.Column("leading_pct", sa.Numeric(10, 4)),
        sa.Column("total_mv", sa.Numeric(20, 4)),
        sa.Column("turnover_rate", sa.Numeric(10, 4)),
        sa.Column("up_num", sa.Integer),
        sa.Column("down_num", sa.Integer),
        sa.Column("idx_type", sa.Text),
        sa.Column("level", sa.Text),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_dc_index"),
        schema=SCHEMA,
    )

    # 17. raw_dc_member — 东财概念成分
    op.create_table(
        "raw_dc_member",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.Text, nullable=False),    # concept code
        sa.Column("con_code", sa.Text, nullable=False),   # stock code
        sa.Column("name", sa.Text),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "con_code",
                                name="pk_sm_raw_dc_member"),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_raw_dc_member_stock", "raw_dc_member",
                    ["con_code", "trade_date"], schema=SCHEMA)

    # 18. raw_sw_daily — 申万行业日线
    op.create_table(
        "raw_sw_daily",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("open", sa.Numeric(14, 4)),
        sa.Column("low", sa.Numeric(14, 4)),
        sa.Column("high", sa.Numeric(14, 4)),
        sa.Column("close", sa.Numeric(14, 4)),
        sa.Column("change_", sa.Numeric(14, 4)),
        sa.Column("pct_change", sa.Numeric(10, 4)),
        sa.Column("vol", sa.Numeric(20, 0)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.Column("pe", sa.Numeric(14, 4)),
        sa.Column("pb", sa.Numeric(14, 4)),
        sa.Column("float_mv", sa.Numeric(20, 4)),
        sa.Column("total_mv", sa.Numeric(20, 4)),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_sw_daily"),
        schema=SCHEMA,
    )

    # 19. raw_index_daily — 主要指数（上证 / 深成 / 创业板 / 沪深300 / 科创50 / 北证50）
    op.create_table(
        "raw_index_daily",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("close", sa.Numeric(14, 4)),
        sa.Column("open", sa.Numeric(14, 4)),
        sa.Column("high", sa.Numeric(14, 4)),
        sa.Column("low", sa.Numeric(14, 4)),
        sa.Column("pre_close", sa.Numeric(14, 4)),
        sa.Column("change_", sa.Numeric(14, 4)),
        sa.Column("pct_chg", sa.Numeric(10, 4)),
        sa.Column("vol", sa.Numeric(20, 0)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sm_raw_index_daily"),
        schema=SCHEMA,
    )

    # 20. raw_block_trade — 大宗交易
    op.create_table(
        "raw_block_trade",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("price", sa.Numeric(14, 4)),
        sa.Column("vol", sa.Numeric(20, 0)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.Column("buyer", sa.Text),
        sa.Column("seller", sa.Text),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_raw_block_trade", "raw_block_trade",
                    ["trade_date", "ts_code"], schema=SCHEMA)

    # 21. raw_cyq_chips — 筹码分布（按价格挡位）
    op.create_table(
        "raw_cyq_chips",
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("price", sa.Numeric(14, 4), nullable=False),
        sa.Column("percent", sa.Numeric(10, 6)),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "price",
                                name="pk_sm_raw_cyq_chips"),
        schema=SCHEMA,
    )

    # ─── BUSINESS / COMPUTED TABLES ─────────────────────────────────────────

    # B1. factor_daily — P0 4 因子（按板块 × 日）
    op.create_table(
        "factor_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("sector_code", sa.Text, nullable=False),
        sa.Column("sector_source", sa.Text, nullable=False),  # 'sw' / 'dc' / 'ths' / 'kpl'
        sa.Column("sector_name", sa.Text),
        sa.Column("heat_score", sa.Numeric(10, 4)),           # 资金热度
        sa.Column("trend_score", sa.Numeric(10, 4)),          # 趋势确认
        sa.Column("persistence_score", sa.Numeric(10, 4)),    # 资金持续
        sa.Column("crowding_score", sa.Numeric(10, 4)),       # 拥挤风险
        sa.Column("derived_json", postgresql.JSONB),          # 衍生量（rolling、percentile、breadth 等）
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "sector_code", "sector_source",
                                name="pk_sm_factor_daily"),
        _ck("ck_sm_factor_source", "sector_source",
            ["sw", "dc", "ths", "kpl"]),
        schema=SCHEMA,
    )

    # B2. market_state_daily — 市场水位 + 状态（一行/日）
    op.create_table(
        "market_state_daily",
        sa.Column("trade_date", sa.Date, primary_key=True),
        sa.Column("total_amount", sa.Numeric(20, 2)),
        sa.Column("amount_10d_avg", sa.Numeric(20, 2)),
        sa.Column("amount_percentile_60d", sa.Numeric(8, 4)),
        sa.Column("up_count", sa.Integer),
        sa.Column("down_count", sa.Integer),
        sa.Column("flat_count", sa.Integer),
        sa.Column("limit_up_count", sa.Integer),
        sa.Column("limit_down_count", sa.Integer),
        sa.Column("max_consecutive_limit_up", sa.Integer),    # 最高连板
        sa.Column("blow_up_count", sa.Integer),               # 炸板数
        sa.Column("blow_up_rate", sa.Numeric(8, 4)),          # 炸板率
        sa.Column("market_state", sa.Text),                   # 进攻 / 中性 / 防守 / 退潮
        sa.Column("derived_json", postgresql.JSONB),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_sm_market_state", "market_state",
            ["进攻", "中性", "防守", "退潮"]),
        schema=SCHEMA,
    )

    # B3. sector_state_daily — 板块角色 + 情绪周期
    op.create_table(
        "sector_state_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("sector_code", sa.Text, nullable=False),
        sa.Column("sector_source", sa.Text, nullable=False),
        sa.Column("sector_name", sa.Text),
        sa.Column("role", sa.Text),                           # 主线/中军/轮动/防守/催化/退潮
        sa.Column("cycle_phase", sa.Text),                    # 冷/点火/确认/扩散/高潮/分歧/退潮
        sa.Column("role_confidence", sa.Text),                # high / medium / low
        sa.Column("phase_confidence", sa.Text),
        sa.Column("evidence_json", postgresql.JSONB),         # 触发条件 + 关键证据
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "sector_code", "sector_source",
                                name="pk_sm_sector_state"),
        _ck("ck_sm_sector_role", "role",
            ["主线", "中军", "轮动", "防守", "催化", "退潮", "未识别"]),
        _ck("ck_sm_sector_cycle", "cycle_phase",
            ["冷", "点火", "确认", "扩散", "高潮", "分歧", "退潮", "未识别"]),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_sector_state_role", "sector_state_daily",
                    ["trade_date", "role"], schema=SCHEMA)

    # B4. stock_signals_daily — 个股角色信号（仅当日有信号的）
    op.create_table(
        "stock_signals_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("primary_sector_code", sa.Text),
        sa.Column("primary_sector_source", sa.Text),
        sa.Column("role", sa.Text),                           # 龙头/中军/情绪先锋/补涨/趋势/风险
        sa.Column("score", sa.Numeric(10, 4)),
        sa.Column("theme", sa.Text),
        sa.Column("lu_desc", sa.Text),                        # 来自 kpl_list
        sa.Column("evidence_json", postgresql.JSONB),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "role",
                                name="pk_sm_stock_signals"),
        _ck("ck_sm_stock_role", "role",
            ["龙头", "中军", "情绪先锋", "补涨", "趋势", "风险"]),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_stock_signals_role", "stock_signals_daily",
                    ["trade_date", "role"], schema=SCHEMA)

    # B5. predictions_daily — 模型 / 规则预测
    op.create_table(
        "predictions_daily",
        sa.Column("prediction_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trade_date", sa.Date, nullable=False),     # 预测的对象日（次交易日）
        sa.Column("computed_for_date", sa.Date, nullable=False),  # 基于哪天数据计算
        sa.Column("prediction_type", sa.Text, nullable=False),
        sa.Column("target_code", sa.Text),
        sa.Column("target_name", sa.Text),
        sa.Column("predicted_outcome", sa.Text),
        sa.Column("confidence", sa.Numeric(8, 4)),
        sa.Column("model_name", sa.Text),                     # logistic / random_forest / xgboost / rule / llm_catalyst
        sa.Column("param_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("evidence_json", postgresql.JSONB),
        sa.Column("review_status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("review_result", sa.Text),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_sm_pred_type", "prediction_type",
            ["sector_continuation", "sector_rotation", "stock_candidate",
             "cycle_phase_change", "leader_continuation"]),
        _ck("ck_sm_pred_review", "review_status",
            ["pending", "validated", "partial", "failed", "not_applicable"]),
        schema=SCHEMA,
    )
    op.create_index("ix_sm_pred_date_type", "predictions_daily",
                    ["trade_date", "prediction_type"], schema=SCHEMA)

    # B6. param_versions — 冻结参数版本
    op.create_table(
        "param_versions",
        sa.Column("version_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("version_name", sa.Text, nullable=False, unique=True),  # e.g. v2026_04
        sa.Column("params_json", postgresql.JSONB, nullable=False),
        sa.Column("frozen_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("frozen_from_backtest_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'active'")),
        sa.Column("notes", sa.Text),
        _ck("ck_sm_param_status", "status", ["active", "archived", "draft"]),
        schema=SCHEMA,
    )

    # B7. backtest_runs
    op.create_table(
        "backtest_runs",
        sa.Column("backtest_run_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("params_json", postgresql.JSONB),
        sa.Column("param_version_used", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'running'")),
        sa.Column("notes", sa.Text),
        _ck("ck_sm_backtest_status", "status",
            ["running", "succeeded", "failed", "partial"]),
        schema=SCHEMA,
    )

    # B8. backtest_metrics
    op.create_table(
        "backtest_metrics",
        sa.Column("backtest_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey(f"{SCHEMA}.backtest_runs.backtest_run_id",
                                onupdate="CASCADE", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("factor_name", sa.Text, nullable=False),
        sa.Column("metric_name", sa.Text, nullable=False),    # ic / rank_ic / topn_hit / group_return
        sa.Column("window_days", sa.Integer),
        sa.Column("group_label", sa.Text),                    # for group returns: Q1..Q5
        sa.Column("metric_value", sa.Numeric(14, 6)),
        sa.Column("n_samples", sa.Integer),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("backtest_run_id", "factor_name", "metric_name",
                                "window_days", "group_label",
                                name="pk_sm_backtest_metrics"),
        schema=SCHEMA,
    )

    # ─── Watermark for incremental ETL ──────────────────────────────────────
    op.create_table(
        "etl_watermarks",
        sa.Column("table_name", sa.Text, primary_key=True),   # raw_daily, raw_kpl_concept, etc.
        sa.Column("last_trade_date_loaded", sa.Date),
        sa.Column("last_run_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("last_run_mode", sa.Text, nullable=False),
        sa.Column("rows_loaded_total", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        _ck("ck_sm_etl_run_mode", "last_run_mode", ["test", "manual", "production"]),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Drop in reverse FK order
    op.drop_table("etl_watermarks", schema=SCHEMA)
    op.drop_table("backtest_metrics", schema=SCHEMA)
    op.drop_table("backtest_runs", schema=SCHEMA)
    op.drop_table("param_versions", schema=SCHEMA)
    op.drop_table("predictions_daily", schema=SCHEMA)
    op.drop_table("stock_signals_daily", schema=SCHEMA)
    op.drop_table("sector_state_daily", schema=SCHEMA)
    op.drop_table("market_state_daily", schema=SCHEMA)
    op.drop_table("factor_daily", schema=SCHEMA)
    for tbl in [
        "raw_cyq_chips", "raw_block_trade", "raw_index_daily", "raw_sw_daily",
        "raw_dc_member", "raw_dc_index", "raw_dc_hot", "raw_ths_hot",
        "raw_top_inst", "raw_top_list", "raw_kpl_list", "raw_kpl_concept_cons",
        "raw_kpl_concept", "raw_limit_list_d", "raw_margin", "raw_moneyflow_hsgt",
        "raw_moneyflow_ind_ths", "raw_moneyflow_ind_dc", "raw_moneyflow",
        "raw_daily_basic", "raw_daily",
    ]:
        op.drop_table(tbl, schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
