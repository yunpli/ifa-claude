"""ta: setup_metrics_daily + regime_winrates JSONB

Revision ID: a8b9c0d1e2f3
Revises: d2e3f4a5b6c7
Create Date: 2026-05-04

Adds `ta.setup_metrics_daily.regime_winrates JSONB` to enable continuous
regime × setup boost in the ranker. Replaces the old boolean
`suitable_regimes` ARRAY with a per-regime winrate map computed by
metrics.compute_setup_metrics. The ARRAY column is kept for backward
compatibility (M5.3 governance still reads it as a fallback when the
JSONB is null).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a8b9c0d1e2f3"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "setup_metrics_daily",
        sa.Column("regime_winrates", postgresql.JSONB, nullable=True),
        schema="ta",
    )


def downgrade() -> None:
    op.drop_column("setup_metrics_daily", "regime_winrates", schema="ta")
