"""sme: evaluate persisted strategy buckets against forward labels

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Create Date: 2026-05-07
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "n2o3p4q5r6s7"
down_revision = "m1n2o3p4q5r6"
branch_labels = None
depends_on = None

SCHEMA = "sme"


def upgrade() -> None:
    op.create_table(
        "sme_strategy_eval_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("strategy_name", sa.Text, nullable=False),
        sa.Column("bucket", sa.Text, nullable=False),
        sa.Column("horizon", sa.Integer, nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("logic_version", sa.Text, nullable=False),
        sa.Column("signal_count", sa.Integer, nullable=False),
        sa.Column("avg_future_return", sa.Float),
        sa.Column("avg_future_excess_return_vs_market", sa.Float),
        sa.Column("avg_future_excess_return_vs_l1", sa.Float),
        sa.Column("avg_signal_score", sa.Float),
        sa.Column("success_rate", sa.Float),
        sa.Column("top_quantile_rate", sa.Float),
        sa.Column("heat_up_rate", sa.Float),
        sa.Column("avg_future_drawdown", sa.Float),
        sa.Column("avg_future_max_runup", sa.Float),
        sa.Column("l2_codes_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("quality_flag", sa.Text, nullable=False, server_default="ok"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "strategy_name", "bucket", "horizon", name="pk_sme_strategy_eval_daily"),
        schema=SCHEMA,
    )
    op.create_index("idx_sme_strategy_eval_lookup", "sme_strategy_eval_daily", ["strategy_name", "bucket", "horizon", "trade_date"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("idx_sme_strategy_eval_lookup", table_name="sme_strategy_eval_daily", schema=SCHEMA)
    op.drop_table("sme_strategy_eval_daily", schema=SCHEMA)
