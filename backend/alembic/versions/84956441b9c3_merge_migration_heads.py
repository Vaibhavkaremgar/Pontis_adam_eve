"""merge migration heads

Revision ID: 84956441b9c3
Revises: 9c7d1e2f3a4b, a1b2c3d4e5f6
Create Date: 2026-05-08 12:32:29.428130

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '84956441b9c3'
down_revision: Union[str, Sequence[str], None] = ('9c7d1e2f3a4b', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
