"""add interview booking slots, timezone, and token used_at

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-06-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "interview_sessions"):
        if not _column_exists(inspector, "interview_sessions", "available_slots"):
            op.add_column(
                "interview_sessions",
                sa.Column("available_slots", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            )
        if not _column_exists(inspector, "interview_sessions", "timezone"):
            op.add_column(
                "interview_sessions",
                sa.Column("timezone", sa.String(length=64), nullable=False, server_default=sa.text("'UTC'")),
            )
        if not _column_exists(inspector, "interview_sessions", "booking_status"):
            op.add_column(
                "interview_sessions",
                sa.Column("booking_status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
            )

    if _table_exists(inspector, "notification_workflow_tokens"):
        if not _column_exists(inspector, "notification_workflow_tokens", "used_at"):
            op.add_column(
                "notification_workflow_tokens",
                sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "notification_workflow_tokens") and _column_exists(inspector, "notification_workflow_tokens", "used_at"):
        op.drop_column("notification_workflow_tokens", "used_at")

    if _table_exists(inspector, "interview_sessions"):
        if _column_exists(inspector, "interview_sessions", "booking_status"):
            op.drop_column("interview_sessions", "booking_status")
        if _column_exists(inspector, "interview_sessions", "timezone"):
            op.drop_column("interview_sessions", "timezone")
        if _column_exists(inspector, "interview_sessions", "available_slots"):
            op.drop_column("interview_sessions", "available_slots")
