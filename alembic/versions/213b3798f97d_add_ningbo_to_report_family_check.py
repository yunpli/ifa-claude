"""Add 'ningbo' to report_runs.report_family CHECK constraint.

Revision ID: 213b3798f97d
Revises: 3dd12ef8cb0f
Create Date: 2026-05-02

The ningbo short-term strategy family uses report_family='ningbo'.
Extends the existing CHECK constraint to allow this value.
"""
from alembic import op

revision = "213b3798f97d"
down_revision = "3dd12ef8cb0f"
branch_labels = None
depends_on = None

_FAMILIES_OLD = ["macro", "asset", "tech", "main", "weekend", "briefing", "adhoc", "smartmoney"]
_FAMILIES_NEW = _FAMILIES_OLD + ["ningbo"]


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
