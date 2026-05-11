"""merge multiple heads

Revision ID: fbdc5904bece
Revises: 3b1c2d4e5f60, d372c35b8adf
Create Date: 2026-05-11 12:35:32.015572

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fbdc5904bece'
down_revision: Union[str, Sequence[str], None] = ('3b1c2d4e5f60', 'd372c35b8adf')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
