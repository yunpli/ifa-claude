"""stock: weekly theme heat cache for sector-cycle leader research

Revision ID: o3p4q5r6s7t8
Revises: n2o3p4q5r6s7
Create Date: 2026-05-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "o3p4q5r6s7t8"
down_revision = "n2o3p4q5r6s7"
branch_labels = None
depends_on = None

SCHEMA = "stock"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "theme_heat_weekly",
        sa.Column("valid_week", sa.Date, nullable=False),
        sa.Column("theme_rank", sa.Integer, nullable=False),
        sa.Column("theme_label", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("heat_score", sa.Float, nullable=False),
        sa.Column("confidence", sa.Float),
        sa.Column("affected_sectors_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("representative_stocks_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_urls_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model_name", sa.Text),
        sa.Column("prompt_version", sa.Text, nullable=False, server_default="stock_theme_heat_v1"),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("run_mode", sa.Text, nullable=False, server_default="manual"),
        sa.Column("quality_flag", sa.Text, nullable=False, server_default="stub"),
        sa.PrimaryKeyConstraint("valid_week", "theme_rank", name="pk_stock_theme_heat_weekly"),
        sa.CheckConstraint("theme_rank BETWEEN 1 AND 5", name="ck_stock_theme_heat_rank"),
        sa.CheckConstraint("heat_score >= 0 AND heat_score <= 1", name="ck_stock_theme_heat_score"),
        schema=SCHEMA,
    )
    op.create_index("idx_stock_theme_heat_category_week", "theme_heat_weekly", ["category", "valid_week"], schema=SCHEMA)
    op.create_index("idx_stock_theme_heat_label_week", "theme_heat_weekly", ["theme_label", "valid_week"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("idx_stock_theme_heat_label_week", table_name="theme_heat_weekly", schema=SCHEMA)
    op.drop_index("idx_stock_theme_heat_category_week", table_name="theme_heat_weekly", schema=SCHEMA)
    op.drop_table("theme_heat_weekly", schema=SCHEMA)
