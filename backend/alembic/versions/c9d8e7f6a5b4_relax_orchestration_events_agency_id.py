"""relax orchestration events agency id

Revision ID: c9d8e7f6a5b4
Revises: b8a7c6d5e4f3
Create Date: 2026-05-25 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, Sequence[str], None] = "b8a7c6d5e4f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_nullable(inspector: sa.Inspector, table_name: str, column_name: str) -> bool | None:
    if not _table_exists(inspector, table_name):
        return None
    for column in inspector.get_columns(table_name):
        if column["name"] == column_name:
            return bool(column.get("nullable", True))
    return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _table_exists(inspector, "orchestration_events"):
        return

    if bind.dialect.name != "postgresql":
        return

    nullable = _column_nullable(inspector, "orchestration_events", "agency_id")
    if nullable is False:
        op.alter_column(
            "orchestration_events",
            "agency_id",
            nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _table_exists(inspector, "orchestration_events"):
        return

    if bind.dialect.name != "postgresql":
        return

    nullable = _column_nullable(inspector, "orchestration_events", "agency_id")
    if nullable is True:
        op.alter_column(
            "orchestration_events",
            "agency_id",
            nullable=False,
        )

