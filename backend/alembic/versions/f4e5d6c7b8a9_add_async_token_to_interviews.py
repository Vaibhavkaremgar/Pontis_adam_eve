"""add async_token to interviews

Revision ID: f4e5d6c7b8a9
Revises: c2d3e4f5a6b7
Create Date: 2026-06-02 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "f4e5d6c7b8a9"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "interviews"):
        if not _column_exists(inspector, "interviews", "async_token"):
            op.add_column(
                "interviews",
                sa.Column("async_token", sa.String(length=64), nullable=True),
            )
            inspector = inspect(bind)

        if not _index_exists(inspector, "interviews", "uq_interviews_async_token"):
            op.create_index(
                "uq_interviews_async_token",
                "interviews",
                ["async_token"],
                unique=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "interviews") and _index_exists(inspector, "interviews", "uq_interviews_async_token"):
        op.drop_index("uq_interviews_async_token", table_name="interviews")

    inspector = inspect(bind)
    if _table_exists(inspector, "interviews") and _column_exists(inspector, "interviews", "async_token"):
        op.drop_column("interviews", "async_token")
