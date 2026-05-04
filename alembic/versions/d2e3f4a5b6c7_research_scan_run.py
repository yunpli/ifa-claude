"""research.scan_run audit table

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-05-03

Tracks each peer-universe scan invocation. Lets operators ask:
  · "When was this L2 last fully scanned?"
  · "Which stocks failed in the last run?"
  · "Is the daily scan healthy (last completion < 25h ago)?"

One row per (run_id, l2_code). Aggregate across rows for cross-L2 dashboards.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_run",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("l2_code", sa.String(16), nullable=False),
        sa.Column("l2_name", sa.String(64), nullable=True),
        sa.Column("on_date", sa.Date, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),  # running|succeeded|partial|failed
        sa.Column("members_total", sa.Integer, nullable=False),
        sa.Column("scanned", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_fresh", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failures", postgresql.JSONB, nullable=True),
        sa.Column("config", postgresql.JSONB, nullable=True),
        sa.PrimaryKeyConstraint("run_id", "l2_code"),
        schema="research",
    )
    op.create_index(
        "idx_scan_run_l2_completed",
        "scan_run", ["l2_code", "completed_at"],
        schema="research",
    )
    op.create_index(
        "idx_scan_run_started",
        "scan_run", ["started_at"],
        schema="research",
    )


def downgrade() -> None:
    op.drop_index("idx_scan_run_started", table_name="scan_run", schema="research")
    op.drop_index("idx_scan_run_l2_completed", table_name="scan_run", schema="research")
    op.drop_table("scan_run", schema="research")
