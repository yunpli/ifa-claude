"""smartmoney: add raw_sw_member + sw_member_monthly tables for SW board membership

Revision ID: c2e8f1a40b56
Revises: a1f3d2e90c47
Create Date: 2026-05-01

Purpose
-------
Switch SmartMoney's primary sector source from DC (东财概念) to SW (申万 L2).

DC concept member data (`raw_dc_member`) only covers ~18 days in production —
unusable for time-correct historical backtest over 2021-2026 (~1200 days).

SW (申万) provides complete L1/L2/L3 classification with `in_date` / `out_date`
fields, enabling time-correct point-in-time member queries across full history.

Two tables:
  · raw_sw_member         — Source of truth: full SW classification with
                            in_date / out_date for every (sector, stock) pair.
                            Pulled once via TuShare `index_member_all`.
  · sw_member_monthly     — Pre-materialised monthly snapshots derived from
                            raw_sw_member, for fast point-in-time aggregation.
                            One row per (snapshot_month, l2_code, ts_code).

Aggregation queries during compute will use sw_member_monthly for speed:
  WHERE snapshot_month = date_trunc('month', :trade_date)::date
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c2e8f1a40b56"
down_revision = "a1f3d2e90c47"
branch_labels = None
depends_on = None

SCHEMA = "smartmoney"


def upgrade() -> None:
    # ── raw_sw_member ────────────────────────────────────────────────────
    op.create_table(
        "raw_sw_member",
        sa.Column("l1_code", sa.Text, nullable=False),    # e.g. '801010.SI'
        sa.Column("l1_name", sa.Text),                    # e.g. '农林牧渔'
        sa.Column("l2_code", sa.Text),                    # e.g. '801011.SI'
        sa.Column("l2_name", sa.Text),                    # e.g. '林业Ⅱ'
        sa.Column("l3_code", sa.Text),                    # e.g. '850111.SI'
        sa.Column("l3_name", sa.Text),                    # e.g. '林业Ⅲ'
        sa.Column("ts_code", sa.Text, nullable=False),    # e.g. '600519.SH'
        sa.Column("name", sa.Text),                       # stock name at fetch time
        sa.Column("in_date", sa.Date),                    # when stock joined sector
        sa.Column("out_date", sa.Date),                   # when stock left, NULL = still in
        sa.Column("is_new", sa.Text),                     # 'Y' / 'N' (TuShare flag)
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("NOW()")),
        # PK note: a stock may join → leave → rejoin a sector, creating multiple
        # rows. Use (l1_code, ts_code, in_date) as the natural composite PK.
        sa.PrimaryKeyConstraint("l1_code", "ts_code", "in_date",
                                 name="pk_sm_raw_sw_member"),
        schema=SCHEMA,
    )
    op.create_index("idx_sw_member_l2",
                    "raw_sw_member",
                    ["l2_code"],
                    schema=SCHEMA,
                    postgresql_where=sa.text("l2_code IS NOT NULL"))
    op.create_index("idx_sw_member_l3",
                    "raw_sw_member",
                    ["l3_code"],
                    schema=SCHEMA,
                    postgresql_where=sa.text("l3_code IS NOT NULL"))
    op.create_index("idx_sw_member_ts",
                    "raw_sw_member",
                    ["ts_code"],
                    schema=SCHEMA)
    op.create_index("idx_sw_member_in_out",
                    "raw_sw_member",
                    ["in_date", "out_date"],
                    schema=SCHEMA)

    # ── sw_member_monthly ────────────────────────────────────────────────
    # Pre-materialised monthly snapshots for fast aggregation queries.
    # One row per (snapshot_month, l2_code, ts_code) where in_date <= snapshot_month
    # AND (out_date IS NULL OR out_date > snapshot_month).
    op.create_table(
        "sw_member_monthly",
        sa.Column("snapshot_month", sa.Date, nullable=False),  # '2024-01-01' etc.
        sa.Column("l1_code", sa.Text, nullable=False),
        sa.Column("l1_name", sa.Text),
        sa.Column("l2_code", sa.Text, nullable=False),
        sa.Column("l2_name", sa.Text),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("name", sa.Text),
        sa.PrimaryKeyConstraint("snapshot_month", "l2_code", "ts_code",
                                 name="pk_sm_sw_member_monthly"),
        schema=SCHEMA,
    )
    op.create_index("idx_sw_monthly_l2",
                    "sw_member_monthly",
                    ["l2_code", "snapshot_month"],
                    schema=SCHEMA)
    op.create_index("idx_sw_monthly_ts",
                    "sw_member_monthly",
                    ["ts_code", "snapshot_month"],
                    schema=SCHEMA)
    op.create_index("idx_sw_monthly_l1",
                    "sw_member_monthly",
                    ["l1_code", "snapshot_month"],
                    schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("idx_sw_monthly_l1", "sw_member_monthly", schema=SCHEMA)
    op.drop_index("idx_sw_monthly_ts", "sw_member_monthly", schema=SCHEMA)
    op.drop_index("idx_sw_monthly_l2", "sw_member_monthly", schema=SCHEMA)
    op.drop_table("sw_member_monthly", schema=SCHEMA)

    op.drop_index("idx_sw_member_in_out", "raw_sw_member", schema=SCHEMA)
    op.drop_index("idx_sw_member_ts", "raw_sw_member", schema=SCHEMA)
    op.drop_index("idx_sw_member_l3", "raw_sw_member", schema=SCHEMA)
    op.drop_index("idx_sw_member_l2", "raw_sw_member", schema=SCHEMA)
    op.drop_table("raw_sw_member", schema=SCHEMA)
