"""full schema fix"""

from typing import Sequence, Union

from alembic import op

revision: str = '5c21b1e83871'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Schema already applied directly — this migration is a no-op.
    pass


def downgrade() -> None:
    pass