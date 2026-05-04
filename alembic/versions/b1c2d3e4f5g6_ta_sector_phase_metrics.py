"""ta: sector_phase_metrics_daily — data-derived phase scores

Revision ID: b1c2d3e4f5g6
Revises: a8b9c0d1e2f3
Create Date: 2026-05-04

Stores rolling 60-day per-phase historical T+15 forward returns for each
SmartMoney cycle_phase. The TA ranker reads this to compute sector_quality
WITHOUT hardcoded phase→score maps. As market behavior shifts, scores adapt.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5g6"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sector_phase_metrics_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("cycle_phase", sa.String(16), nullable=False),
        sa.Column("n_observations", sa.Integer),     # # of (stock, day) pairs
        sa.Column("avg_t15_return_pct", sa.Numeric(8, 4)),
        sa.Column("win_rate_t15_pct", sa.Numeric(8, 4)),
        sa.Column("derived_score", sa.Numeric(5, 4)),    # 0..1 used by ranker
        sa.PrimaryKeyConstraint("trade_date", "cycle_phase"),
        schema="ta",
    )


def downgrade() -> None:
    op.drop_table("sector_phase_metrics_daily", schema="ta")
