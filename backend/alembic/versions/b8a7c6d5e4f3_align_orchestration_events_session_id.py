"""align orchestration events session id

Revision ID: b8a7c6d5e4f3
Revises: a7b6c5d4e3f2
Create Date: 2026-05-25 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "b8a7c6d5e4f3"
down_revision: Union[str, Sequence[str], None] = "a7b6c5d4e3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _table_exists(inspector, "orchestration_events"):
        return

    columns = {column["name"] for column in inspector.get_columns("orchestration_events")}

    if "orchestration_session_id" in columns and "session_id" not in columns:
        op.alter_column(
            "orchestration_events",
            "orchestration_session_id",
            new_column_name="session_id",
            existing_type=sa.String(length=36),
        )
        return

    if "orchestration_session_id" in columns and "session_id" in columns:
        op.execute(
            """
            UPDATE orchestration_events
            SET session_id = COALESCE(NULLIF(session_id, ''), orchestration_session_id::text)
            WHERE session_id IS NULL OR session_id = ''
            """
        )
        op.drop_column("orchestration_events", "orchestration_session_id")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _table_exists(inspector, "orchestration_events"):
        return

    columns = {column["name"] for column in inspector.get_columns("orchestration_events")}

    if "session_id" in columns and "orchestration_session_id" not in columns:
        op.alter_column(
            "orchestration_events",
            "session_id",
            new_column_name="orchestration_session_id",
            existing_type=sa.String(length=36),
        )
