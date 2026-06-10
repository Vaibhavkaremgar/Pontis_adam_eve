"""Normalize hiring source_app values to slack/ui.

Revision ID: e7f8a9b0c1d2
Revises: c1d2e3f4a5b6
Create Date: 2026-06-09 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _normalize_source_app(table_name: str) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not _table_exists(inspector, table_name) or not _column_exists(inspector, table_name, "source_app"):
        return
    op.execute(
        sa.text(
            f"""
            UPDATE {table_name}
            SET source_app = CASE
                WHEN source_app = 'adam' THEN 'slack'
                WHEN source_app = 'dashboard' THEN 'ui'
                ELSE source_app
            END
            """
        )
    )


def upgrade() -> None:
    for table_name in ("jobs", "interviews", "outreach_events", "notification_workflow_tokens"):
        _normalize_source_app(table_name)


def downgrade() -> None:
    for table_name in ("jobs", "interviews", "outreach_events", "notification_workflow_tokens"):
        bind = op.get_bind()
        inspector = inspect(bind)
        if not _table_exists(inspector, table_name) or not _column_exists(inspector, table_name, "source_app"):
            continue
        op.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET source_app = CASE
                    WHEN source_app = 'slack' THEN 'adam'
                    WHEN source_app = 'ui' THEN 'dashboard'
                    ELSE source_app
                END
                """
            )
        )
