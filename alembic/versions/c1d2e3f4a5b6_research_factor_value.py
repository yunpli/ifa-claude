"""research.factor_value table

Revision ID: c1d2e3f4a5b6
Revises: a3b4c5d6e7f8
Create Date: 2026-05-03

Stores computed factor results for cross-stock peer comparisons and historical
audit. One row per (ts_code, factor_name, period). Upsertable.

Why a flat table (not JSONB):
  · peer.py needs cheap range scans / GROUP BY by factor_name + SW L2 join
  · status, peer_percentile, value all benefit from native typing
  · history series and notes go to JSONB columns where set semantics matter

Indexes:
  · PK (ts_code, factor_name, period) for upsert lookup
  · (factor_name, period) — peer scan join target
  · (ts_code, computed_at DESC) — "give me everything for this stock now"
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c1d2e3f4a5b6"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS research")
    op.create_table(
        "factor_value",
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("factor_name", sa.String(48), nullable=False),
        sa.Column("period", sa.String(8), nullable=False, server_default=""),
        sa.Column("family", sa.String(24), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=True),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("peer_percentile", sa.Numeric(6, 2), nullable=True),
        sa.Column("peer_rank", sa.Integer, nullable=True),
        sa.Column("peer_total", sa.Integer, nullable=True),
        sa.Column("notes", postgresql.JSONB, nullable=True),
        sa.Column("history", postgresql.JSONB, nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("ts_code", "factor_name", "period"),
        schema="research",
    )
    op.create_index(
        "idx_factor_value_factor_period",
        "factor_value", ["factor_name", "period"],
        schema="research",
    )
    op.create_index(
        "idx_factor_value_ts_computed",
        "factor_value", ["ts_code", "computed_at"],
        schema="research",
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("idx_factor_value_ts_computed", table_name="factor_value", schema="research")
    op.drop_index("idx_factor_value_factor_period", table_name="factor_value", schema="research")
    op.drop_table("factor_value", schema="research")
