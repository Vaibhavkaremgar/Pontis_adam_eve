"""convert internal candidate resume id to uuid

Revision ID: 1f2e3d4c5b6a
Revises: 9c7d1e2f3a4b
Create Date: 2026-05-08 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "1f2e3d4c5b6a"
down_revision = "9c7d1e2f3a4b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "internal_candidate_resumes",
        "id",
        existing_type=sa.VARCHAR(length=36),
        type_=postgresql.UUID(as_uuid=True),
        postgresql_using="id::uuid",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "internal_candidate_resumes",
        "id",
        existing_type=postgresql.UUID(as_uuid=True),
        type_=sa.VARCHAR(length=36),
        postgresql_using="id::varchar(36)",
        existing_nullable=False,
    )
