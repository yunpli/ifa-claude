"""sme: persist market structure strategy snapshots

Revision ID: m1n2o3p4q5r6
Revises: l0m1n2o3p4q5
Create Date: 2026-05-07
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "m1n2o3p4q5r6"
down_revision = "l0m1n2o3p4q5"
branch_labels = None
depends_on = None

SCHEMA = "sme"


def upgrade() -> None:
    op.create_table(
        "sme_market_structure_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("logic_version", sa.Text, nullable=False),
        sa.Column("capital_state", sa.Text),
        sa.Column("state_tags_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("primary_directions_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("secondary_directions_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("defensive_directions_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("repair_directions_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("avoid_directions_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("crowding_risk_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("snapshot_json", postgresql.JSONB, nullable=False),
        sa.Column("client_conclusion_json", postgresql.JSONB, nullable=False),
        sa.Column("external_summary", sa.Text),
        sa.Column("quality_flag", sa.Text, nullable=False, server_default="ok"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", name="pk_sme_market_structure_daily"),
        schema=SCHEMA,
    )
    op.create_index("idx_sme_market_structure_state", "sme_market_structure_daily", ["capital_state", "trade_date"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("idx_sme_market_structure_state", table_name="sme_market_structure_daily", schema=SCHEMA)
    op.drop_table("sme_market_structure_daily", schema=SCHEMA)
