"""rename smartmoney reserved columns

Revision ID: 7c91866fd29f
Revises: 289066f22cc2
Create Date: 2026-05-01 01:05:59.912656

Renames PostgreSQL-reserved column names to escape-free equivalents:
  smartmoney.raw_dc_index.leading        → leading_name
  smartmoney.raw_kpl_concept_cons.desc   → description
"""
from collections.abc import Sequence

from alembic import op

revision: str = "7c91866fd29f"
down_revision: str | Sequence[str] | None = "289066f22cc2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "smartmoney"


def upgrade() -> None:
    op.alter_column("raw_dc_index", "leading",
                    new_column_name="leading_name", schema=SCHEMA)
    op.alter_column("raw_kpl_concept_cons", "desc",
                    new_column_name="description", schema=SCHEMA)


def downgrade() -> None:
    op.alter_column("raw_kpl_concept_cons", "description",
                    new_column_name="desc", schema=SCHEMA)
    op.alter_column("raw_dc_index", "leading_name",
                    new_column_name="leading", schema=SCHEMA)
