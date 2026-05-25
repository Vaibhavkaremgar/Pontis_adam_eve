"""relax orchestration session linkage constraints

Revision ID: a7b6c5d4e3f2
Revises: 918950e20800
Create Date: 2026-05-25 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


revision: str = "a7b6c5d4e3f2"
down_revision: Union[str, Sequence[str], None] = "918950e20800"
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

    if not _table_exists(inspector, "orchestration_sessions"):
        return

    if bind.dialect.name != "postgresql":
        return

    for column_name in ("agency_id", "company_id", "job_id"):
        nullable = _column_nullable(inspector, "orchestration_sessions", column_name)
        if nullable is False:
                op.alter_column(
                    "orchestration_sessions",
                    column_name,
                    existing_type=PG_UUID(as_uuid=False),
                    nullable=True,
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _table_exists(inspector, "orchestration_sessions"):
        return

    if bind.dialect.name != "postgresql":
        return

    for column_name in ("company_id", "job_id"):
        nullable = _column_nullable(inspector, "orchestration_sessions", column_name)
        if nullable is True:
            op.alter_column(
                "orchestration_sessions",
                column_name,
                existing_type=PG_UUID(as_uuid=False),
                nullable=False,
            )
