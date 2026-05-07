"""sme: MVP-1 schema for Smart Money Enhanced

Revision ID: l0m1n2o3p4q5
Revises: k9l0m1n2o3p4
Create Date: 2026-05-06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "l0m1n2o3p4q5"
down_revision = "k9l0m1n2o3p4"
branch_labels = None
depends_on = None

SCHEMA = "sme"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "sme_unit_registry",
        sa.Column("source_name", sa.Text, nullable=False),
        sa.Column("source_field", sa.Text, nullable=False),
        sa.Column("source_unit", sa.Text, nullable=False),
        sa.Column("target_field", sa.Text, nullable=False),
        sa.Column("target_unit", sa.Text, nullable=False),
        sa.Column("conversion_factor", sa.Float, nullable=False),
        sa.Column("rounding_policy", sa.Text, nullable=False, server_default="round"),
        sa.Column("example_value", sa.Float),
        sa.Column("last_verified_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("source_name", "source_field", name="pk_sme_unit_registry"),
        schema=SCHEMA,
    )

    op.create_table(
        "sme_data_contracts",
        sa.Column("contract_id", sa.Text, nullable=False),
        sa.Column("table_name", sa.Text, nullable=False),
        sa.Column("schema_version", sa.Text, nullable=False),
        sa.Column("contract_json", postgresql.JSONB, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("contract_id", name="pk_sme_data_contracts"),
        schema=SCHEMA,
    )

    op.create_table(
        "sme_etl_runs",
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("run_mode", sa.Text, nullable=False),
        sa.Column("source_mode", sa.Text, nullable=False),
        sa.Column("as_of_trade_date", sa.Date),
        sa.Column("start_date", sa.Date),
        sa.Column("end_date", sa.Date),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("row_counts_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("quality_summary_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("run_id", name="pk_sme_etl_runs"),
        schema=SCHEMA,
    )

    op.create_table(
        "sme_source_audit_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("source_name", sa.Text, nullable=False),
        sa.Column("source_schema", sa.Text, nullable=False),
        sa.Column("source_table", sa.Text, nullable=False),
        sa.Column("row_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("distinct_stock_count", sa.BigInteger),
        sa.Column("distinct_sector_count", sa.BigInteger),
        sa.Column("coverage_status", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("null_rate_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "source_name", name="pk_sme_source_audit_daily"),
        schema=SCHEMA,
    )

    op.create_table(
        "sme_storage_audit_daily",
        sa.Column("audit_date", sa.Date, nullable=False),
        sa.Column("schema_name", sa.Text, nullable=False),
        sa.Column("table_name", sa.Text, nullable=False),
        sa.Column("row_count", sa.BigInteger),
        sa.Column("total_bytes", sa.BigInteger),
        sa.Column("table_bytes", sa.BigInteger),
        sa.Column("index_bytes", sa.BigInteger),
        sa.Column("storage_status", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("audit_date", "schema_name", "table_name", name="pk_sme_storage_audit_daily"),
        schema=SCHEMA,
    )

    op.create_table(
        "sme_sw_member_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("l1_code", sa.Text),
        sa.Column("l1_name", sa.Text),
        sa.Column("l2_code", sa.Text, nullable=False),
        sa.Column("l2_name", sa.Text),
        sa.Column("l3_code", sa.Text),
        sa.Column("l3_name", sa.Text),
        sa.Column("in_date", sa.Date),
        sa.Column("out_date", sa.Date),
        sa.Column("source_mode", sa.Text, nullable=False),
        sa.Column("source_snapshot_id", sa.Text),
        sa.Column("quality_flag", sa.Text, nullable=False, server_default="ok"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "l2_code", "ts_code", name="pk_sme_sw_member_daily"),
        schema=SCHEMA,
    )
    op.create_index("idx_sme_sw_member_daily_stock", "sme_sw_member_daily", ["ts_code", "trade_date"], schema=SCHEMA)
    op.create_index("idx_sme_sw_member_daily_l2", "sme_sw_member_daily", ["l2_code", "trade_date"], schema=SCHEMA)

    op.create_table(
        "sme_stock_orderflow_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("open_yuan", sa.Float),
        sa.Column("high_yuan", sa.Float),
        sa.Column("low_yuan", sa.Float),
        sa.Column("close_yuan", sa.Float),
        sa.Column("pct_chg", sa.Float),
        sa.Column("amount_yuan", sa.BigInteger),
        sa.Column("turnover_rate", sa.Float),
        sa.Column("volume_ratio", sa.Float),
        sa.Column("total_mv_yuan", sa.BigInteger),
        sa.Column("circ_mv_yuan", sa.BigInteger),
        sa.Column("buy_sm_amount_yuan", sa.BigInteger),
        sa.Column("sell_sm_amount_yuan", sa.BigInteger),
        sa.Column("buy_md_amount_yuan", sa.BigInteger),
        sa.Column("sell_md_amount_yuan", sa.BigInteger),
        sa.Column("buy_lg_amount_yuan", sa.BigInteger),
        sa.Column("sell_lg_amount_yuan", sa.BigInteger),
        sa.Column("buy_elg_amount_yuan", sa.BigInteger),
        sa.Column("sell_elg_amount_yuan", sa.BigInteger),
        sa.Column("sm_net_yuan", sa.BigInteger),
        sa.Column("md_net_yuan", sa.BigInteger),
        sa.Column("lg_net_yuan", sa.BigInteger),
        sa.Column("elg_net_yuan", sa.BigInteger),
        sa.Column("main_net_yuan", sa.BigInteger),
        sa.Column("retail_net_yuan", sa.BigInteger),
        sa.Column("net_mf_amount_yuan", sa.BigInteger),
        sa.Column("net_recomputed_yuan", sa.BigInteger),
        sa.Column("main_net_ratio", sa.Float),
        sa.Column("retail_net_ratio", sa.Float),
        sa.Column("elg_net_ratio", sa.Float),
        sa.Column("behavior_flags_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reconciliation_error_yuan", sa.BigInteger),
        sa.Column("source_mode", sa.Text, nullable=False),
        sa.Column("source_snapshot_id", sa.Text),
        sa.Column("quality_flag", sa.Text, nullable=False, server_default="ok"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name="pk_sme_stock_orderflow_daily"),
        schema=SCHEMA,
    )
    op.create_index("idx_sme_stock_orderflow_stock", "sme_stock_orderflow_daily", ["ts_code", "trade_date"], schema=SCHEMA)

    op.create_table(
        "sme_sector_orderflow_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("l1_code", sa.Text),
        sa.Column("l1_name", sa.Text),
        sa.Column("l2_code", sa.Text, nullable=False),
        sa.Column("l2_name", sa.Text),
        sa.Column("member_count", sa.Integer),
        sa.Column("matched_stock_count", sa.Integer),
        sa.Column("coverage_ratio", sa.Float),
        sa.Column("sector_amount_yuan", sa.BigInteger),
        sa.Column("sector_return_equal_weight", sa.Float),
        sa.Column("sector_return_amount_weight", sa.Float),
        sa.Column("sector_return_sw_index", sa.Float),
        sa.Column("sm_net_yuan", sa.BigInteger),
        sa.Column("md_net_yuan", sa.BigInteger),
        sa.Column("lg_net_yuan", sa.BigInteger),
        sa.Column("elg_net_yuan", sa.BigInteger),
        sa.Column("main_net_yuan", sa.BigInteger),
        sa.Column("retail_net_yuan", sa.BigInteger),
        sa.Column("net_mf_amount_yuan", sa.BigInteger),
        sa.Column("main_net_ratio", sa.Float),
        sa.Column("retail_net_ratio", sa.Float),
        sa.Column("elg_net_ratio", sa.Float),
        sa.Column("flow_breadth", sa.Float),
        sa.Column("main_positive_breadth", sa.Float),
        sa.Column("elg_positive_breadth", sa.Float),
        sa.Column("retail_positive_breadth", sa.Float),
        sa.Column("price_positive_breadth", sa.Float),
        sa.Column("top5_main_net_share", sa.Float),
        sa.Column("leader_ts_code", sa.Text),
        sa.Column("leader_name", sa.Text),
        sa.Column("leader_main_net_yuan", sa.BigInteger),
        sa.Column("source_mode", sa.Text, nullable=False),
        sa.Column("source_snapshot_id", sa.Text),
        sa.Column("quality_flag", sa.Text, nullable=False, server_default="ok"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "l2_code", name="pk_sme_sector_orderflow_daily"),
        schema=SCHEMA,
    )
    op.create_index("idx_sme_sector_orderflow_l2", "sme_sector_orderflow_daily", ["l2_code", "trade_date"], schema=SCHEMA)

    op.create_table(
        "sme_sector_diffusion_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("l2_code", sa.Text, nullable=False),
        sa.Column("l2_name", sa.Text),
        sa.Column("leader_return_1d", sa.Float),
        sa.Column("leader_return_3d", sa.Float),
        sa.Column("leader_return_5d", sa.Float),
        sa.Column("median_member_return_5d", sa.Float),
        sa.Column("tail_member_return_5d", sa.Float),
        sa.Column("leader_to_median_spread", sa.Float),
        sa.Column("flow_breadth_1d", sa.Float),
        sa.Column("flow_breadth_3d", sa.Float),
        sa.Column("flow_breadth_5d", sa.Float),
        sa.Column("flow_breadth_10d", sa.Float),
        sa.Column("diffusion_slope_5_10", sa.Float),
        sa.Column("diffusion_phase", sa.Text),
        sa.Column("diffusion_score", sa.Float),
        sa.Column("top_members_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("quality_flag", sa.Text, nullable=False, server_default="ok"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "l2_code", name="pk_sme_sector_diffusion_daily"),
        schema=SCHEMA,
    )

    op.create_table(
        "sme_sector_state_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("l2_code", sa.Text, nullable=False),
        sa.Column("l2_name", sa.Text),
        sa.Column("current_state", sa.Text, nullable=False),
        sa.Column("state_score", sa.Float),
        sa.Column("state_confidence", sa.Float),
        sa.Column("transition_hint", sa.Text),
        sa.Column("risk_flags_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("quality_flag", sa.Text, nullable=False, server_default="ok"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "l2_code", name="pk_sme_sector_state_daily"),
        schema=SCHEMA,
    )

    op.create_table(
        "sme_labels_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("l2_code", sa.Text, nullable=False),
        sa.Column("horizon", sa.Integer, nullable=False),
        sa.Column("future_return", sa.Float),
        sa.Column("future_excess_return_vs_market", sa.Float),
        sa.Column("future_excess_return_vs_l1", sa.Float),
        sa.Column("future_rank_pct", sa.Float),
        sa.Column("future_top_quantile_label", sa.Boolean),
        sa.Column("future_heat_delta", sa.Float),
        sa.Column("future_heat_up_label", sa.Boolean),
        sa.Column("future_drawdown", sa.Float),
        sa.Column("future_max_runup", sa.Float),
        sa.Column("turnover_adjusted_return", sa.Float),
        sa.Column("label_quality_flag", sa.Text, nullable=False, server_default="ok"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "l2_code", "horizon", name="pk_sme_labels_daily"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in [
        "sme_labels_daily",
        "sme_sector_state_daily",
        "sme_sector_diffusion_daily",
        "sme_sector_orderflow_daily",
        "sme_stock_orderflow_daily",
        "sme_sw_member_daily",
        "sme_storage_audit_daily",
        "sme_source_audit_daily",
        "sme_etl_runs",
        "sme_data_contracts",
        "sme_unit_registry",
    ]:
        op.drop_table(table, schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
