"""stock.tuning_promotion_log — audit trail for tuning promotions

Revision ID: k9l0m1n2o3p4
Revises: j8k9l0m1n2o3
Create Date: 2026-05-05

T3.4: persistent audit log for every auto_promote_if_passing call. Records:
- which artifact was considered
- which gates passed/failed
- per-horizon promotion decisions
- where the variant YAML went (if written)
- a deterministic git tag string (caller can apply manually)
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "k9l0m1n2o3p4"
down_revision = "j8k9l0m1n2o3"
branch_labels = None
depends_on = None

SCHEMA = "stock"


def upgrade() -> None:
    op.create_table(
        "tuning_promotion_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("artifact_path", sa.Text, nullable=True),
        sa.Column("base_yaml_path", sa.Text, nullable=False),
        sa.Column("variant_yaml_path", sa.Text, nullable=True),
        sa.Column("backup_yaml_path", sa.Text, nullable=True),
        sa.Column("accepted", sa.Boolean, nullable=False),
        sa.Column("horizons_applied", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("horizons_kept_baseline", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("gates_summary", postgresql.JSONB, nullable=False),
        sa.Column("rank_ic_summary", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("bootstrap_ci", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("regime_breakdown", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("git_tag", sa.Text, nullable=True),
        sa.Column("applied_to_baseline", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text, nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tuning_promotion_log_created",
        "tuning_promotion_log",
        ["created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tuning_promotion_log_accepted",
        "tuning_promotion_log",
        ["accepted", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_tuning_promotion_log_accepted", table_name="tuning_promotion_log", schema=SCHEMA)
    op.drop_index("ix_tuning_promotion_log_created", table_name="tuning_promotion_log", schema=SCHEMA)
    op.drop_table("tuning_promotion_log", schema=SCHEMA)
