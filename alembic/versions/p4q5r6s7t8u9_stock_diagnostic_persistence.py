"""stock: sector-cycle leader diagnostic surface

Revision ID: p4q5r6s7t8u9
Revises: p0q1r2s3t4u5
Create Date: 2026-05-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "p4q5r6s7t8u9"
down_revision = "p0q1r2s3t4u5"
branch_labels = None
depends_on = None

SCHEMA = "stock"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "sector_cycle_leader_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("l1_code", sa.Text),
        sa.Column("l1_name", sa.Text),
        sa.Column("l2_code", sa.Text, nullable=False),
        sa.Column("l2_name", sa.Text),
        sa.Column("rank_in_sector", sa.Integer, nullable=False),
        sa.Column("sector_rank_count", sa.Integer, nullable=False),
        sa.Column("leader_score", sa.Float, nullable=False),
        sa.Column("sector_score", sa.Float),
        sa.Column("stock_score", sa.Float),
        sa.Column("quality_flag", sa.Text, nullable=False, server_default="computed"),
        sa.Column("logic_version", sa.Text, nullable=False, server_default="sector_cycle_leader_v1"),
        sa.Column("evidence_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "logic_version", name="pk_stock_sector_cycle_leader_daily"),
        sa.CheckConstraint("rank_in_sector >= 1", name="ck_stock_sector_cycle_rank_positive"),
        sa.CheckConstraint("sector_rank_count >= rank_in_sector", name="ck_stock_sector_cycle_rank_count"),
        schema=SCHEMA,
    )
    op.create_index("idx_stock_sector_cycle_leader_l2_rank", "sector_cycle_leader_daily", ["trade_date", "l2_code", "rank_in_sector"], schema=SCHEMA)
    op.create_index("idx_stock_sector_cycle_leader_ts", "sector_cycle_leader_daily", ["ts_code", "trade_date"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("idx_stock_sector_cycle_leader_ts", table_name="sector_cycle_leader_daily", schema=SCHEMA)
    op.drop_index("idx_stock_sector_cycle_leader_l2_rank", table_name="sector_cycle_leader_daily", schema=SCHEMA)
    op.drop_table("sector_cycle_leader_daily", schema=SCHEMA)
