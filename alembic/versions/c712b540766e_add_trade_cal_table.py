"""core: add trade_cal table (SSE trading calendar mirror)

Revision ID: c712b540766e
Revises: c9f1a5e20b73
Create Date: 2026-05-02

Purpose
-------
Local mirror of TuShare's trade_cal API for SSE.  Lets us check whether
any given date is a trading day without hitting the TuShare network.

Refresh cadence: monthly or quarterly (holiday schedule is known months
in advance; run `scripts/is_trading_day.py --refresh` to update).

Schema
------
    smartmoney.trade_cal
        cal_date    DATE        -- calendar date
        exchange    VARCHAR(8)  -- always 'SSE' for now
        is_open     BOOLEAN     -- True = trading day
        PRIMARY KEY (cal_date, exchange)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c712b540766e"
down_revision = "c9f1a5e20b73"
branch_labels = None
depends_on = None

SCHEMA = "smartmoney"


def upgrade() -> None:
    op.create_table(
        "trade_cal",
        sa.Column("cal_date", sa.Date(), nullable=False),
        sa.Column("exchange", sa.String(8), nullable=False, server_default="SSE"),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("cal_date", "exchange"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_trade_cal_cal_date",
        "trade_cal",
        ["cal_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_trade_cal_cal_date", table_name="trade_cal", schema=SCHEMA)
    op.drop_table("trade_cal", schema=SCHEMA)
