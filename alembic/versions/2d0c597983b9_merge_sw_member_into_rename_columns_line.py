"""merge sw_member into rename_columns line

Revision ID: 2d0c597983b9
Revises: 7c91866fd29f, c2e8f1a40b56
Create Date: 2026-05-01 09:31:24.845336

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d0c597983b9'
down_revision: Union[str, Sequence[str], None] = ('7c91866fd29f', 'c2e8f1a40b56')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
