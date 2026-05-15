"""Add source_app isolation to shared Adam/Dashboard tables.

Revision ID: 8e9f0a1b2c3d
Revises: 67925be04abf
Create Date: 2026-05-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "8e9f0a1b2c3d"
down_revision: Union[str, Sequence[str], None] = "67925be04abf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not has_column("jobs", "source_app"):
        op.add_column("jobs", sa.Column("source_app", sa.String(length=32), nullable=False, server_default="dashboard"))
    if not has_column("interviews", "source_app"):
        op.add_column("interviews", sa.Column("source_app", sa.String(length=32), nullable=False, server_default="dashboard"))
    if not has_column("outreach_events", "source_app"):
        op.add_column("outreach_events", sa.Column("source_app", sa.String(length=32), nullable=False, server_default="dashboard"))

    if not has_table("notification_workflow_tokens"):
        op.create_table(
            "notification_workflow_tokens",
            sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
            sa.Column("source_app", sa.String(length=32), nullable=False, server_default="dashboard"),
            sa.Column("job_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("jobs.id"), nullable=False),
            sa.Column("candidate_id", sa.String(length=128), nullable=False),
            sa.Column("workflow_name", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("token", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
            sa.UniqueConstraint("token", name="uq_notification_workflow_tokens_token"),
        )


def downgrade() -> None:
    op.drop_table("notification_workflow_tokens")
    op.drop_column("outreach_events", "source_app")
    op.drop_column("interviews", "source_app")
    op.drop_column("jobs", "source_app")
