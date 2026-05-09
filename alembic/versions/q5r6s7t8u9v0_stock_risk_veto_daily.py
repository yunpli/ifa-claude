"""stock: persisted diagnostic risk veto surface

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-05-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "q5r6s7t8u9v0"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None

SCHEMA = "stock"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "risk_veto_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("veto_category", sa.Text, nullable=False),
        sa.Column("hard_veto", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("severity", sa.Text),
        sa.Column("source_table", sa.Text, nullable=False),
        sa.Column("source_date", sa.Date),
        sa.Column("reason", sa.Text),
        sa.Column("logic_version", sa.Text, nullable=False, server_default="risk_veto_v1"),
        sa.Column("evidence_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "veto_category", "source_table", "source_date", "logic_version", name="pk_stock_risk_veto_daily"),
        schema=SCHEMA,
    )
    op.create_index("idx_stock_risk_veto_ts", "risk_veto_daily", ["ts_code", "trade_date"], schema=SCHEMA)
    op.create_index("idx_stock_risk_veto_hard", "risk_veto_daily", ["trade_date", "hard_veto"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("idx_stock_risk_veto_hard", table_name="risk_veto_daily", schema=SCHEMA)
    op.drop_index("idx_stock_risk_veto_ts", table_name="risk_veto_daily", schema=SCHEMA)
    op.drop_table("risk_veto_daily", schema=SCHEMA)
