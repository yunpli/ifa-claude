"""ta.warnings_daily — bearish-pattern detector outputs (D family + future C2).

Separated from ta.candidates_daily so the long pool (Tier A/B) is unaffected
by these warning rows. Populated by ifa.families.ta.setups.scanner.scan
(WARNING_SETUPS branch) on the FULL liquid universe (including retreat-phase
sector stocks excluded from the long pool).

Revision ID: d2e3f4g5h6i7
Revises: c1d2e3f4g5h6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d2e3f4g5h6i7"
down_revision = "c1d2e3f4g5h6"
branch_labels = None
depends_on = None

SCHEMA = "ta"


def upgrade() -> None:
    op.create_table(
        "warnings_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("setup_name", sa.String(48), nullable=False),
        sa.Column("score", sa.Numeric(6, 4), nullable=False),
        sa.Column("triggers", sa.ARRAY(sa.Text)),
        sa.Column("evidence", postgresql.JSONB),
        sa.Column("regime_at_gen", sa.String(48)),
        sa.Column("sector_role", sa.String(24)),
        sa.Column("sector_cycle_phase", sa.String(24)),
        sa.Column("in_long_universe", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "setup_name"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ta_warnings_daily_date",
        "warnings_daily",
        ["trade_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ta_warnings_daily_setup",
        "warnings_daily",
        ["setup_name", "trade_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_ta_warnings_daily_setup", table_name="warnings_daily", schema=SCHEMA)
    op.drop_index("ix_ta_warnings_daily_date", table_name="warnings_daily", schema=SCHEMA)
    op.drop_table("warnings_daily", schema=SCHEMA)
