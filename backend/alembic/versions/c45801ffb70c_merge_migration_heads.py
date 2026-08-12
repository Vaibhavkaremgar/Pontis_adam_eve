"""merge migration heads

Revision ID: c45801ffb70c
Revises: f0e1d2c3b4a5, 1a9b8c7d6e5f
Create Date: 2026-08-12 00:55:59.006876

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c45801ffb70c'
down_revision: Union[str, Sequence[str], None] = ('f0e1d2c3b4a5', '1a9b8c7d6e5f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
