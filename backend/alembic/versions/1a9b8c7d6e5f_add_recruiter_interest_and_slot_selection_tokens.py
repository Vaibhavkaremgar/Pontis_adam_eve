"""add recruiter interest workflow and slot selection token columns

Revision ID: 1a9b8c7d6e5f
Revises: fbdc5904bece
Create Date: 2026-08-11 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "1a9b8c7d6e5f"
down_revision = "fbdc5904bece"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _table_exists(inspector, "recruiter_interest_requests"):
        op.create_table(
            "recruiter_interest_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("candidate_id", sa.String(length=128), nullable=False),
            sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_descriptions.id"), nullable=False),
            sa.Column("agency_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agencies.id"), nullable=False),
            sa.Column("recruiter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("request_status", sa.String(length=32), nullable=False, server_default="interested"),
            sa.Column("candidate_response", sa.String(length=32), nullable=True),
            sa.Column("candidate_response_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("recruiter_requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
            sa.UniqueConstraint(
                "candidate_id",
                "job_id",
                "agency_id",
                "recruiter_id",
                name="uq_recruiter_interest_requests_candidate_job_agency_recruiter",
            ),
        )
        op.create_index("ix_recruiter_interest_requests_candidate_job", "recruiter_interest_requests", ["candidate_id", "job_id"], unique=False)
        op.create_index("ix_recruiter_interest_requests_agency_recruiter", "recruiter_interest_requests", ["agency_id", "recruiter_id"], unique=False)
        op.create_index("ix_recruiter_interest_requests_request_status", "recruiter_interest_requests", ["request_status"], unique=False)

    if _table_exists(inspector, "notification_workflow_tokens"):
        if not _column_exists(inspector, "notification_workflow_tokens", "agency_id"):
            op.add_column("notification_workflow_tokens", sa.Column("agency_id", postgresql.UUID(as_uuid=True), nullable=True))
        if not _column_exists(inspector, "notification_workflow_tokens", "user_id"):
            op.add_column("notification_workflow_tokens", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "notification_workflow_tokens"):
        if _column_exists(inspector, "notification_workflow_tokens", "user_id"):
            op.drop_column("notification_workflow_tokens", "user_id")
        if _column_exists(inspector, "notification_workflow_tokens", "agency_id"):
            op.drop_column("notification_workflow_tokens", "agency_id")

    if _table_exists(inspector, "recruiter_interest_requests"):
        op.drop_index("ix_recruiter_interest_requests_request_status", table_name="recruiter_interest_requests")
        op.drop_index("ix_recruiter_interest_requests_agency_recruiter", table_name="recruiter_interest_requests")
        op.drop_index("ix_recruiter_interest_requests_candidate_job", table_name="recruiter_interest_requests")
        op.drop_table("recruiter_interest_requests")
