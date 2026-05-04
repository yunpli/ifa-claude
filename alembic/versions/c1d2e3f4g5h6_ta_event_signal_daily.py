"""ta.event_signal_daily — per-stock per-day earnings/disclosure event signal.

Populated by ifa.families.ta.etl.event_etl from Tushare's
forecast / express / disclosure_date interfaces.

Revision ID: c1d2e3f4g5h6
Revises: b1c2d3e4f5g6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4g5h6"
down_revision = "b1c2d3e4f5g6"
branch_labels = None
depends_on = None

SCHEMA = "ta"


def upgrade() -> None:
    op.create_table(
        "event_signal_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),  # forecast / express / disclosure_pre
        sa.Column("polarity", sa.String(16)),                     # positive / neutral / negative
        sa.Column("days_to_disclosure", sa.Integer),
        sa.Column("ref_value", sa.Numeric(20, 4)),                # forecast pct change midpoint, etc.
        sa.Column("source_ann_date", sa.Date),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "event_type"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ta_event_signal_ts_code",
        "event_signal_daily",
        ["ts_code", "trade_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_ta_event_signal_ts_code", table_name="event_signal_daily", schema=SCHEMA)
    op.drop_table("event_signal_daily", schema=SCHEMA)
