"""add missing interview columns

Revision ID: 44bc60c1f819
Revises: e3f4d5c6b7a8
Create Date: 2026-05-14 18:44:14.844440

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44bc60c1f819'
down_revision: Union[str, Sequence[str], None] = 'e3f4d5c6b7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
