"""smartmoney: add sector_moneyflow_sw_daily table

Revision ID: c9f1a5e20b73
Revises: a9f3c2e17d84
Create Date: 2026-05-01

Purpose
-------
Add sector_moneyflow_sw_daily: SW L2 板块日资金流汇总表.

Aggregates raw_moneyflow (individual stock level) into SW L2 sector buckets
using sw_member_monthly PIT snapshots.  Enables time-correct sector-level
money flow analysis without look-ahead bias.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9f1a5e20b73"
down_revision = "a9f3c2e17d84"
branch_labels = None
depends_on = None

SCHEMA = "smartmoney"


def upgrade() -> None:
    op.create_table(
        "sector_moneyflow_sw_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("l2_code", sa.String(12), nullable=False),
        sa.Column("l2_name", sa.String(64), nullable=True),
        sa.Column("l1_code", sa.String(12), nullable=True),
        sa.Column("l1_name", sa.String(64), nullable=True),
        sa.Column("net_amount", sa.Numeric, nullable=True),      # SUM(net_mf_amount) 万元
        sa.Column("buy_elg_amount", sa.Numeric, nullable=True),  # SUM(buy_elg_amount) 超大单买入
        sa.Column("sell_elg_amount", sa.Numeric, nullable=True),
        sa.Column("buy_lg_amount", sa.Numeric, nullable=True),   # SUM(buy_lg_amount) 大单买入
        sa.Column("sell_lg_amount", sa.Numeric, nullable=True),
        sa.Column("stock_count", sa.Integer, nullable=True),     # COUNT(DISTINCT ts_code)
        sa.PrimaryKeyConstraint("trade_date", "l2_code",
                                name="pk_sm_sector_moneyflow_sw_daily"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sector_moneyflow_sw_daily_trade_date",
        "sector_moneyflow_sw_daily",
        ["trade_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sector_moneyflow_sw_daily_trade_date",
        table_name="sector_moneyflow_sw_daily",
        schema=SCHEMA,
    )
    op.drop_table("sector_moneyflow_sw_daily", schema=SCHEMA)
