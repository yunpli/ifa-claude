"""smartmoney: widen raw_kpl_list numeric columns to unconstrained NUMERIC

Revision ID: a9f3c2e17d84
Revises: 2d0c597983b9
Create Date: 2026-05-01

Problem
-------
raw_kpl_list has several NUMERIC(p, s) columns whose precision is too tight for
extreme historical values from 2021 A-share data:
  - bid_turnover   NUMERIC(10, 4) → max 6 digits before decimal
  - pct_chg        NUMERIC(10, 4) → same
  - bid_pct_chg    NUMERIC(10, 4)
  - rt_pct_chg     NUMERIC(10, 4)
  - turnover_rate  NUMERIC(10, 4)

When backfilling 2021 data, some stocks return values that exceed these limits
causing psycopg.errors.NumericValueOutOfRange.

Fix: change all constrained NUMERIC(p,s) columns to unconstrained NUMERIC,
which has no precision/scale limit in PostgreSQL.
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "a9f3c2e17d84"
down_revision = "2d0c597983b9"
branch_labels = None
depends_on = None

SCHEMA = "smartmoney"
TABLE = "raw_kpl_list"

# All numeric columns that previously had precision constraints
WIDEN_COLS = [
    "net_change",
    "bid_amount",
    "bid_change",
    "bid_turnover",
    "lu_bid_vol",
    "pct_chg",
    "bid_pct_chg",
    "rt_pct_chg",
    "limit_order",
    "amount",
    "turnover_rate",
    "free_float",
    "lu_limit_order",
]


def upgrade() -> None:
    for col in WIDEN_COLS:
        op.alter_column(
            TABLE,
            col,
            type_=sa.Numeric(),   # unconstrained — no precision/scale limits
            schema=SCHEMA,
            existing_type=sa.Numeric(),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Restore original precision — data that exceeded old limits will be lost
    # on downgrade; acceptable since this migration only widens constraints.
    precision_map = {
        "net_change":    sa.Numeric(20, 2),
        "bid_amount":    sa.Numeric(20, 2),
        "bid_change":    sa.Numeric(20, 2),
        "bid_turnover":  sa.Numeric(10, 4),
        "lu_bid_vol":    sa.Numeric(20, 2),
        "pct_chg":       sa.Numeric(10, 4),
        "bid_pct_chg":   sa.Numeric(10, 4),
        "rt_pct_chg":    sa.Numeric(10, 4),
        "limit_order":   sa.Numeric(20, 2),
        "amount":        sa.Numeric(20, 2),
        "turnover_rate": sa.Numeric(10, 4),
        "free_float":    sa.Numeric(20, 2),
        "lu_limit_order": sa.Numeric(20, 2),
    }
    for col, typ in precision_map.items():
        op.alter_column(TABLE, col, type_=typ, schema=SCHEMA,
                        existing_type=sa.Numeric(), existing_nullable=True)
