"""add candidate_feedback company_id

Revision ID: 1a2b3c4d5e6f
Revises: d5e6f7a8b9c1
Create Date: 2026-06-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "1a2b3c4d5e6f"
down_revision = "d5e6f7a8b9c1"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "candidate_feedback") and not _column_exists(inspector, "candidate_feedback", "company_id"):
        op.add_column(
            "candidate_feedback",
            sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "candidate_feedback") and _column_exists(inspector, "candidate_feedback", "company_id"):
        op.drop_column("candidate_feedback", "company_id")
