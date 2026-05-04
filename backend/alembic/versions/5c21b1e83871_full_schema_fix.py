"""full schema fix"""

from sqlalchemy.dialects.postgresql import UUID
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '5c21b1e83871'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- candidate_feedback ---
    op.alter_column(
        'candidate_feedback',
        'recruiter_id',
        existing_type=sa.CHAR(length=36),
        type_=UUID(),
        postgresql_using="recruiter_id::uuid",
        existing_nullable=True
    )

    op.alter_column(
        'candidate_feedback',
        'session_id',
        existing_type=sa.CHAR(length=36),
        type_=UUID(),
        postgresql_using="session_id::uuid",
        existing_nullable=True
    )

    # --- ranking_explanations ---
    op.alter_column(
        'ranking_explanations',
        'id',
        existing_type=sa.CHAR(length=36),
        type_=UUID(),
        postgresql_using="id::uuid",
        existing_nullable=False
    )

    op.alter_column(
        'ranking_explanations',
        'job_id',
        existing_type=sa.CHAR(length=36),
        type_=UUID(),
        postgresql_using="job_id::uuid",
        nullable=False
    )

    # --- ranking_runs ---
    op.alter_column(
        'ranking_runs',
        'id',
        existing_type=sa.CHAR(length=36),
        type_=UUID(),
        postgresql_using="id::uuid",
        existing_nullable=False
    )

    op.alter_column(
        'ranking_runs',
        'job_id',
        existing_type=sa.CHAR(length=36),
        type_=UUID(),
        postgresql_using="job_id::uuid",
        nullable=False
    )

    op.alter_column(
        'ranking_runs',
        'recruiter_id',
        existing_type=sa.CHAR(length=36),
        type_=UUID(),
        postgresql_using="recruiter_id::uuid",
        existing_nullable=True
    )

    # --- recruiter_role_preferences ---
    op.alter_column(
        'recruiter_role_preferences',
        'id',
        existing_type=sa.CHAR(length=36),
        type_=UUID(),
        postgresql_using="id::uuid",
        existing_nullable=False
    )

    op.alter_column(
        'recruiter_role_preferences',
        'recruiter_id',
        existing_type=sa.CHAR(length=36),
        type_=UUID(),
        postgresql_using="recruiter_id::uuid",
        existing_nullable=False
    )

    # --- recruiter_skill_preferences ---
    op.alter_column(
        'recruiter_skill_preferences',
        'id',
        existing_type=sa.CHAR(length=36),
        type_=UUID(),
        postgresql_using="id::uuid",
        existing_nullable=False
    )


def downgrade() -> None:
    pass