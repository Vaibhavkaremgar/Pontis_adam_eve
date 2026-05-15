"""merge source_app migration heads

Revision ID: 36dfebc065d4
Revises: 44bc60c1f819, 8e9f0a1b2c3d
Create Date: 2026-05-15 13:17:38.934991

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36dfebc065d4'
down_revision: Union[str, Sequence[str], None] = ('44bc60c1f819', '8e9f0a1b2c3d')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
