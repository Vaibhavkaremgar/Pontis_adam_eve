"""merge alembic heads

Revision ID: 3f3bcdc41f8c
Revises: 36dfebc065d4, a9f8e7d6c5b4
Create Date: 2026-05-18 12:22:07.981081

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f3bcdc41f8c'
down_revision: Union[str, Sequence[str], None] = ('36dfebc065d4', 'a9f8e7d6c5b4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
