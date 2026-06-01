"""add missing audit_events columns

Revision ID: b1c2d3e4f5a6
Revises: 1a2b3c4d5e6f
Create Date: 2026-06-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "b1c2d3e4f5a6"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "audit_events") and not _column_exists(inspector, "audit_events", "company_id"):
        op.add_column(
            "audit_events",
            sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=True),
        )

    if _table_exists(inspector, "audit_events") and not _column_exists(inspector, "audit_events", "user_id"):
        op.add_column(
            "audit_events",
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        )

    if _table_exists(inspector, "audit_events") and not _column_exists(inspector, "audit_events", "slack_user_id"):
        op.add_column(
            "audit_events",
            sa.Column("slack_user_id", sa.String(length=64), nullable=True, server_default=""),
        )

    if _table_exists(inspector, "audit_events") and not _column_exists(inspector, "audit_events", "action_type"):
        op.add_column(
            "audit_events",
            sa.Column("action_type", sa.String(length=128), nullable=True, server_default=""),
        )

    if _table_exists(inspector, "audit_events") and not _column_exists(inspector, "audit_events", "payload"):
        op.add_column(
            "audit_events",
            sa.Column("payload", sa.JSON(), nullable=True, server_default=sa.text("'{}'")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "audit_events") and _column_exists(inspector, "audit_events", "payload"):
        op.drop_column("audit_events", "payload")

    if _table_exists(inspector, "audit_events") and _column_exists(inspector, "audit_events", "action_type"):
        op.drop_column("audit_events", "action_type")

    if _table_exists(inspector, "audit_events") and _column_exists(inspector, "audit_events", "slack_user_id"):
        op.drop_column("audit_events", "slack_user_id")

    if _table_exists(inspector, "audit_events") and _column_exists(inspector, "audit_events", "user_id"):
        op.drop_column("audit_events", "user_id")

    if _table_exists(inspector, "audit_events") and _column_exists(inspector, "audit_events", "company_id"):
        op.drop_column("audit_events", "company_id")
