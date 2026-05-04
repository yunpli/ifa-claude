"""ta.fina_indicator_quarterly — per-stock per-period ROE / EPS / margin.

Backs the fundamental_filter ROE check ("近 4 季 ROE 不全部为负") in
ifa.families.ta.setups.context_loader. Populated by Tushare's
`fina_indicator` endpoint (or `fina_indicator_vip` for full coverage).

Revision ID: i7j8k9l0m1n2
Revises: h6i7j8k9l0m1
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "i7j8k9l0m1n2"
down_revision = "h6i7j8k9l0m1"
branch_labels = None
depends_on = None

SCHEMA = "ta"


def upgrade() -> None:
    op.create_table(
        "fina_indicator_quarterly",
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),     # period end (e.g. 20260331)
        sa.Column("ann_date", sa.Date),                      # announcement date
        sa.Column("roe", sa.Numeric(10, 4)),                 # 净资产收益率 %
        sa.Column("roe_dt", sa.Numeric(10, 4)),              # 摊薄 ROE %
        sa.Column("eps", sa.Numeric(10, 4)),                 # 基本每股收益
        sa.Column("netprofit_margin", sa.Numeric(10, 4)),    # 销售净利率 %
        sa.Column("grossprofit_margin", sa.Numeric(10, 4)),  # 销售毛利率 %
        sa.Column("ar_turn", sa.Numeric(10, 4)),             # 应收账款周转率
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("ts_code", "end_date"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ta_fina_end_date",
        "fina_indicator_quarterly", ["end_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_ta_fina_end_date",
                  table_name="fina_indicator_quarterly", schema=SCHEMA)
    op.drop_table("fina_indicator_quarterly", schema=SCHEMA)
