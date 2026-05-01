"""Add 'smartmoney' to report_runs.report_family CHECK constraint.

Revision ID: a1f3d2e90c47
Revises: 289066f22cc2
Create Date: 2026-05-01

The SmartMoney evening report family uses report_family='smartmoney', but
the original baseline constraint only allowed the five legacy families.
This migration drops and recreates the constraint to include 'smartmoney'.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1f3d2e90c47"
down_revision = "289066f22cc2"
branch_labels = None
depends_on = None

_FAMILIES_OLD = ["macro", "asset", "tech", "main", "weekend", "briefing", "adhoc"]
_FAMILIES_NEW = _FAMILIES_OLD + ["smartmoney"]


def _family_check(values: list[str]) -> str:
    vals = ", ".join(f"'{v}'" for v in values)
    return f"report_family = ANY (ARRAY[{vals}])"


def upgrade() -> None:
    op.drop_constraint("ck_report_runs_family", "report_runs", type_="check")
    op.create_check_constraint(
        "ck_report_runs_family",
        "report_runs",
        _family_check(_FAMILIES_NEW),
    )


def downgrade() -> None:
    op.drop_constraint("ck_report_runs_family", "report_runs", type_="check")
    op.create_check_constraint(
        "ck_report_runs_family",
        "report_runs",
        _family_check(_FAMILIES_OLD),
    )
