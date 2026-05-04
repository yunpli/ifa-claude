"""ta.setup_metrics_daily — add combined_score_60d (T+15 weighted objective).

Per ta_v2.3.yaml.backtest_objective.weights:
    combined = 0.7 × T+15 wr × ret + 0.2 × T+5 wr × ret + 0.1 × T+10 wr × ret

Used by walk-forward parameter search as the primary optimization target.

Revision ID: g5h6i7j8k9l0
Revises: f4g5h6i7j8k9
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "g5h6i7j8k9l0"
down_revision = "f4g5h6i7j8k9"
branch_labels = None
depends_on = None

SCHEMA = "ta"


def upgrade() -> None:
    op.add_column(
        "setup_metrics_daily",
        sa.Column("combined_score_60d", sa.Numeric(8, 4)),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("setup_metrics_daily", "combined_score_60d", schema=SCHEMA)
