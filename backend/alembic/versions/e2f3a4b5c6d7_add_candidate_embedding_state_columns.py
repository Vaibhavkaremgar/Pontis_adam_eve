"""add explicit candidate embedding state columns

Revision ID: e2f3a4b5c6d7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e2f3a4b5c6d7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("embedding_status", sa.String(length=20), nullable=True))
    op.add_column("candidates", sa.Column("embedding_version", sa.String(length=100), nullable=True))
    op.add_column("candidates", sa.Column("embedding_text_hash", sa.String(length=64), nullable=True))
    op.add_column("candidates", sa.Column("embedding_indexed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_candidates_embedding_status", "candidates", ["embedding_status"])


def downgrade() -> None:
    op.drop_index("ix_candidates_embedding_status", table_name="candidates")
    op.drop_column("candidates", "embedding_indexed_at")
    op.drop_column("candidates", "embedding_text_hash")
    op.drop_column("candidates", "embedding_version")
    op.drop_column("candidates", "embedding_status")
