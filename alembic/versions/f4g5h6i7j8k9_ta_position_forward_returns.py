"""ta.position_events_daily — add T+5/T+10/T+15 fixed-horizon returns.

These coexist with realized_return_pct (which exits dynamically on
stop/target/time). Fixed-horizon returns use fill_price → close at T+N
regardless of intermediate stops/targets, providing the smoother signal
needed for walk-forward parameter optimization.

Backtest objective (per ta_v2.3.yaml.backtest_objective.weights):
  combined = 0.7 × T+15  +  0.2 × T+5  +  0.1 × T+10

Revision ID: f4g5h6i7j8k9
Revises: e3f4g5h6i7j8
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f4g5h6i7j8k9"
down_revision = "e3f4g5h6i7j8"
branch_labels = None
depends_on = None

SCHEMA = "ta"


def upgrade() -> None:
    for col in ("return_t5_pct", "return_t10_pct", "return_t15_pct"):
        op.add_column(
            "position_events_daily",
            sa.Column(col, sa.Numeric(8, 4)),
            schema=SCHEMA,
        )


def downgrade() -> None:
    for col in ("return_t15_pct", "return_t10_pct", "return_t5_pct"):
        op.drop_column("position_events_daily", col, schema=SCHEMA)
