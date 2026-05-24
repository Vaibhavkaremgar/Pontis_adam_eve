"""merge_heads

Revision ID: 918950e20800
Revises: 3f3bcdc41f8c, 8f7e6d5c4b3a
Create Date: 2026-05-24 14:34:45.976341

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '918950e20800'
down_revision: Union[str, Sequence[str], None] = ('3f3bcdc41f8c', '8f7e6d5c4b3a')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
