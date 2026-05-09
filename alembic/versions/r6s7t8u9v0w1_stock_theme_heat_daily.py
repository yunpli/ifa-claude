"""stock: daily theme heat curve cache

Revision ID: r6s7t8u9v0w1
Revises: q5r6s7t8u9v0
Create Date: 2026-05-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "r6s7t8u9v0w1"
down_revision = "q5r6s7t8u9v0"
branch_labels = None
depends_on = None

SCHEMA = "stock"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "theme_heat_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("theme_rank", sa.Integer, nullable=False),
        sa.Column("theme_label", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("l1_code", sa.Text),
        sa.Column("l1_name", sa.Text),
        sa.Column("l2_code", sa.Text, nullable=False, server_default="__UNMAPPED__"),
        sa.Column("l2_name", sa.Text),
        sa.Column("heat_level", sa.Float, nullable=False),
        sa.Column("heat_delta", sa.Float),
        sa.Column("heat_acceleration", sa.Float),
        sa.Column("persistence_days", sa.Integer, nullable=False, server_default="1"),
        sa.Column("theme_breadth", sa.Integer),
        sa.Column("sector_breadth", sa.Integer),
        sa.Column("stock_breadth", sa.Integer),
        sa.Column("main_money_judgement", sa.Text),
        sa.Column("retail_chase_judgement", sa.Text),
        sa.Column("main_retail_alignment", sa.Text),
        sa.Column("crowding_distribution_risk", sa.Text),
        sa.Column("one_day_wonder_risk", sa.Float),
        sa.Column("persistence_score", sa.Float),
        sa.Column("freshness", sa.Text),
        sa.Column("quality_flag", sa.Text, nullable=False, server_default="batch_llm_cache"),
        sa.Column("prompt_version", sa.Text, nullable=False, server_default="stock_theme_heat_llm_daily_v1"),
        sa.Column("model_name", sa.Text),
        sa.Column("run_mode", sa.Text, nullable=False, server_default="manual"),
        sa.Column("evidence_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "theme_rank", "l2_code", name="pk_stock_theme_heat_daily"),
        sa.CheckConstraint("theme_rank BETWEEN 1 AND 20", name="ck_stock_theme_heat_daily_rank"),
        sa.CheckConstraint("heat_level >= 0 AND heat_level <= 1", name="ck_stock_theme_heat_daily_level"),
        schema=SCHEMA,
    )
    op.create_index("idx_stock_theme_heat_daily_l2", "theme_heat_daily", ["trade_date", "l2_code", "theme_rank"], schema=SCHEMA)
    op.create_index("idx_stock_theme_heat_daily_label", "theme_heat_daily", ["theme_label", "trade_date"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("idx_stock_theme_heat_daily_label", table_name="theme_heat_daily", schema=SCHEMA)
    op.drop_index("idx_stock_theme_heat_daily_l2", table_name="theme_heat_daily", schema=SCHEMA)
    op.drop_table("theme_heat_daily", schema=SCHEMA)
