"""ta — recommended-price columns on candidates_daily + position_events_daily.

M10 P1.1+P1.2:
  · Lift entry/stop/target/rr from evidence_json into top-level columns of
    ta.candidates_daily so SQL JOIN/aggregate is fast (10× faster than JSONB).
  · Create ta.position_events_daily — single row per (candidate_id) tracking
    the full life-cycle: fill detection, stop/target/time exit events.
    Replaces the simple ta.candidate_tracking semantics with an institutional
    position state machine.

Revision ID: e3f4g5h6i7j8
Revises: d2e3f4g5h6i7
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e3f4g5h6i7j8"
down_revision = "d2e3f4g5h6i7"
branch_labels = None
depends_on = None

SCHEMA = "ta"


def upgrade() -> None:
    # ── 1. add recommended-price columns to candidates_daily ──
    op.add_column("candidates_daily",
        sa.Column("entry_price", sa.Numeric(12, 4)), schema=SCHEMA)
    op.add_column("candidates_daily",
        sa.Column("stop_loss", sa.Numeric(12, 4)), schema=SCHEMA)
    op.add_column("candidates_daily",
        sa.Column("target_price", sa.Numeric(12, 4)), schema=SCHEMA)
    op.add_column("candidates_daily",
        sa.Column("rr_ratio", sa.Numeric(6, 2)), schema=SCHEMA)
    op.add_column("candidates_daily",
        sa.Column("price_basis", sa.String(16)), schema=SCHEMA)
    op.create_index(
        "ix_ta_candidates_entry_price",
        "candidates_daily", ["trade_date", "entry_price"],
        schema=SCHEMA,
    )

    # ── 2. position_events_daily ──
    op.create_table(
        "position_events_daily",
        sa.Column("candidate_id", sa.UUID, nullable=False),
        sa.Column("generation_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("setup_name", sa.String(32)),
        # Recommended prices snapshot (so events stand alone if candidate row deleted).
        sa.Column("entry_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("stop_loss", sa.Numeric(12, 4), nullable=False),
        sa.Column("target_price", sa.Numeric(12, 4), nullable=False),
        # Fill leg.
        sa.Column("fill_status", sa.String(16),
                  sa.CheckConstraint("fill_status IN ('filled','unfilled','expired')")),
        sa.Column("fill_date", sa.Date),
        sa.Column("fill_price", sa.Numeric(12, 4)),
        # Exit leg.
        sa.Column("exit_status", sa.String(20),
                  sa.CheckConstraint(
                      "exit_status IN ('stop_hit','target_hit','time_exit','still_holding')"
                  )),
        sa.Column("exit_date", sa.Date),
        sa.Column("exit_price", sa.Numeric(12, 4)),
        # Outcomes.
        sa.Column("realized_return_pct", sa.Numeric(8, 4)),
        sa.Column("max_drawdown_pct", sa.Numeric(8, 4)),
        sa.Column("days_held", sa.Integer),
        sa.Column("evaluated_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("candidate_id"),
        sa.ForeignKeyConstraint(
            ["candidate_id"], [f"{SCHEMA}.candidates_daily.candidate_id"],
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ta_position_events_gendate",
        "position_events_daily", ["generation_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ta_position_events_status",
        "position_events_daily", ["exit_status", "generation_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_ta_position_events_status",
                  table_name="position_events_daily", schema=SCHEMA)
    op.drop_index("ix_ta_position_events_gendate",
                  table_name="position_events_daily", schema=SCHEMA)
    op.drop_table("position_events_daily", schema=SCHEMA)

    op.drop_index("ix_ta_candidates_entry_price",
                  table_name="candidates_daily", schema=SCHEMA)
    for col in ("price_basis", "rr_ratio", "target_price", "stop_loss", "entry_price"):
        op.drop_column("candidates_daily", col, schema=SCHEMA)
