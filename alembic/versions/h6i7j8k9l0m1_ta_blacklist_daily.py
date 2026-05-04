"""ta.blacklist_daily — per-stock per-day adverse-event tags.

M10 P1.6 — populated by ifa.families.ta.etl.blacklist_etl from Tushare's
anns_d (announcements). Each row is one (trade_date, ts_code, reason)
tuple. Used by context_loader to hard-cut/soft-warn stocks pre-Tier.

Reasons (from severity descending):
  · investigation        — 立案调查 (hard)
  · major_restructuring  — 重大资产重组停牌 (hard)
  · severe_forecast_miss — 业绩预告 -50%以上 (soft)
  · insider_selling      — 大股东减持 (soft)

(Suspended + ST/*ST already detected in context_loader from existing tables.)

Revision ID: h6i7j8k9l0m1
Revises: g5h6i7j8k9l0
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h6i7j8k9l0m1"
down_revision = "g5h6i7j8k9l0"
branch_labels = None
depends_on = None

SCHEMA = "ta"


def upgrade() -> None:
    op.create_table(
        "blacklist_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(8),
                  sa.CheckConstraint("severity IN ('hard','soft')")),
        sa.Column("ann_title", sa.Text),
        sa.Column("source_ann_date", sa.Date),
        sa.Column("ref_value", sa.Numeric(8, 2)),    # forecast pct etc.
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "reason"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ta_blacklist_severity",
        "blacklist_daily", ["severity", "trade_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_ta_blacklist_severity",
                  table_name="blacklist_daily", schema=SCHEMA)
    op.drop_table("blacklist_daily", schema=SCHEMA)
