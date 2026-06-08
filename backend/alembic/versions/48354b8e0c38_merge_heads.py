"""merge heads

Revision ID: 48354b8e0c38
Revises: a0b1c2d3e4f5, d0e1f2a3b4c5
Create Date: 2026-06-05 15:52:34.058418

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '48354b8e0c38'
down_revision: Union[str, Sequence[str], None] = ('a0b1c2d3e4f5', 'd0e1f2a3b4c5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
