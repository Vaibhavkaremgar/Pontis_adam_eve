"""merge final resume uuid migration head

Revision ID: d372c35b8adf
Revises: 1f2e3d4c5b6a, 84956441b9c3
Create Date: 2026-05-08 15:50:04.886260

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd372c35b8adf'
down_revision: Union[str, Sequence[str], None] = ('1f2e3d4c5b6a', '84956441b9c3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
