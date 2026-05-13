"""merge company scoped persistence head

Revision ID: 67925be04abf
Revises: d9e8f7a6b5c4, fbdc5904bece
Create Date: 2026-05-13 16:06:05.503779

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '67925be04abf'
down_revision: Union[str, Sequence[str], None] = ('d9e8f7a6b5c4', 'fbdc5904bece')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
