"""merge interview and orchestration heads

Revision ID: 09f8061a0467
Revises: 3c2b1a0f9d4e, c9d8e7f6a5b4
Create Date: 2026-05-25 19:23:15.961187

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '09f8061a0467'
down_revision: Union[str, Sequence[str], None] = ('3c2b1a0f9d4e', 'c9d8e7f6a5b4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
