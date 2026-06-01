"""add slack installations and attribution columns

Revision ID: 7e6d5c4b3a21
Revises: 09f8061a0467
Create Date: 2026-05-30 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "7e6d5c4b3a21"
down_revision: Union[str, Sequence[str], None] = "09f8061a0467"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _table_exists(inspector, "slack_installations"):
        op.create_table(
            "slack_installations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False, index=True),
            sa.Column("team_id", sa.String(length=64), nullable=False),
            sa.Column("team_name", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("enterprise_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("bot_user_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("bot_access_token", sa.Text(), nullable=False, server_default=""),
            sa.Column("scope_list", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("installed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True, index=True),
            sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("team_id", name="uq_slack_installations_team_id"),
        )
        inspector = inspect(bind)

    if not _table_exists(inspector, "slack_users"):
        op.create_table(
            "slack_users",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("company_id", sa.String(length=36), sa.ForeignKey("companies.id"), nullable=False, index=True),
            sa.Column("slack_installation_id", sa.String(length=36), sa.ForeignKey("slack_installations.id"), nullable=False, index=True),
            sa.Column("slack_user_id", sa.String(length=64), nullable=False, index=True),
            sa.Column("email", sa.String(length=320), nullable=False, server_default=""),
            sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("internal_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True, index=True),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="recruiter"),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("slack_installation_id", "slack_user_id", name="uq_slack_users_installation_user"),
        )
        inspector = inspect(bind)

    if _table_exists(inspector, "jobs"):
        if not _column_exists(inspector, "jobs", "slack_installation_id"):
            op.add_column("jobs", sa.Column("slack_installation_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "jobs", "slack_team_id"):
            op.add_column("jobs", sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""))
        if not _column_exists(inspector, "jobs", "slack_user_id"):
            op.add_column("jobs", sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""))
        if not _index_exists(inspector, "jobs", "ix_jobs_slack_installation_id"):
            op.create_index("ix_jobs_slack_installation_id", "jobs", ["slack_installation_id"], unique=False)

    if _table_exists(inspector, "candidate_feedback"):
        if not _column_exists(inspector, "candidate_feedback", "company_id"):
            op.add_column("candidate_feedback", sa.Column("company_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "candidate_feedback", "slack_installation_id"):
            op.add_column("candidate_feedback", sa.Column("slack_installation_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "candidate_feedback", "slack_team_id"):
            op.add_column("candidate_feedback", sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""))
        if not _column_exists(inspector, "candidate_feedback", "slack_user_id"):
            op.add_column("candidate_feedback", sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""))
        if not _index_exists(inspector, "candidate_feedback", "ix_candidate_feedback_company_id"):
            op.create_index("ix_candidate_feedback_company_id", "candidate_feedback", ["company_id"], unique=False)
        if not _index_exists(inspector, "candidate_feedback", "ix_candidate_feedback_slack_installation_id"):
            op.create_index("ix_candidate_feedback_slack_installation_id", "candidate_feedback", ["slack_installation_id"], unique=False)

    if _table_exists(inspector, "candidate_lifecycle_events"):
        if not _column_exists(inspector, "candidate_lifecycle_events", "slack_installation_id"):
            op.add_column("candidate_lifecycle_events", sa.Column("slack_installation_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "candidate_lifecycle_events", "slack_team_id"):
            op.add_column("candidate_lifecycle_events", sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""))
        if not _column_exists(inspector, "candidate_lifecycle_events", "slack_user_id"):
            op.add_column("candidate_lifecycle_events", sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""))
        if not _index_exists(inspector, "candidate_lifecycle_events", "ix_candidate_lifecycle_events_slack_installation_id"):
            op.create_index("ix_candidate_lifecycle_events_slack_installation_id", "candidate_lifecycle_events", ["slack_installation_id"], unique=False)

    if _table_exists(inspector, "outreach_events"):
        if not _column_exists(inspector, "outreach_events", "slack_installation_id"):
            op.add_column("outreach_events", sa.Column("slack_installation_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "outreach_events", "slack_team_id"):
            op.add_column("outreach_events", sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""))
        if not _column_exists(inspector, "outreach_events", "slack_user_id"):
            op.add_column("outreach_events", sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""))
        if not _index_exists(inspector, "outreach_events", "ix_outreach_events_slack_installation_id"):
            op.create_index("ix_outreach_events_slack_installation_id", "outreach_events", ["slack_installation_id"], unique=False)

    if _table_exists(inspector, "interview_sessions"):
        if not _column_exists(inspector, "interview_sessions", "slack_installation_id"):
            op.add_column("interview_sessions", sa.Column("slack_installation_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "interview_sessions", "slack_team_id"):
            op.add_column("interview_sessions", sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""))
        if not _column_exists(inspector, "interview_sessions", "slack_user_id"):
            op.add_column("interview_sessions", sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""))
        if not _index_exists(inspector, "interview_sessions", "ix_interview_sessions_slack_installation_id"):
            op.create_index("ix_interview_sessions_slack_installation_id", "interview_sessions", ["slack_installation_id"], unique=False)

    if _table_exists(inspector, "audit_events"):
        if not _column_exists(inspector, "audit_events", "company_id"):
            op.add_column("audit_events", sa.Column("company_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "audit_events", "user_id"):
            op.add_column("audit_events", sa.Column("user_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "audit_events", "slack_user_id"):
            op.add_column("audit_events", sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""))
        if not _column_exists(inspector, "audit_events", "action_type"):
            op.add_column("audit_events", sa.Column("action_type", sa.String(length=128), nullable=False, server_default=""))
        if not _column_exists(inspector, "audit_events", "payload"):
            op.add_column("audit_events", sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        if not _index_exists(inspector, "audit_events", "ix_audit_events_company_id"):
            op.create_index("ix_audit_events_company_id", "audit_events", ["company_id"], unique=False)
        if not _index_exists(inspector, "audit_events", "ix_audit_events_user_id"):
            op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"], unique=False)
        if not _index_exists(inspector, "audit_events", "ix_audit_events_slack_user_id"):
            op.create_index("ix_audit_events_slack_user_id", "audit_events", ["slack_user_id"], unique=False)
        if not _index_exists(inspector, "audit_events", "ix_audit_events_action_type"):
            op.create_index("ix_audit_events_action_type", "audit_events", ["action_type"], unique=False)

    op.execute("UPDATE audit_events SET action_type = COALESCE(NULLIF(action_type, ''), action)")
    op.execute("UPDATE audit_events SET payload = COALESCE(payload, metadata)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "audit_events"):
        for index_name in (
            "ix_audit_events_action_type",
            "ix_audit_events_slack_user_id",
            "ix_audit_events_user_id",
            "ix_audit_events_company_id",
        ):
            if _index_exists(inspector, "audit_events", index_name):
                op.drop_index(index_name, table_name="audit_events")
        for column_name in ("payload", "action_type", "slack_user_id", "user_id", "company_id"):
            if _column_exists(inspector, "audit_events", column_name):
                op.drop_column("audit_events", column_name)

    if _table_exists(inspector, "interview_sessions"):
        if _index_exists(inspector, "interview_sessions", "ix_interview_sessions_slack_installation_id"):
            op.drop_index("ix_interview_sessions_slack_installation_id", table_name="interview_sessions")
        for column_name in ("slack_user_id", "slack_team_id", "slack_installation_id"):
            if _column_exists(inspector, "interview_sessions", column_name):
                op.drop_column("interview_sessions", column_name)

    if _table_exists(inspector, "outreach_events"):
        if _index_exists(inspector, "outreach_events", "ix_outreach_events_slack_installation_id"):
            op.drop_index("ix_outreach_events_slack_installation_id", table_name="outreach_events")
        for column_name in ("slack_user_id", "slack_team_id", "slack_installation_id"):
            if _column_exists(inspector, "outreach_events", column_name):
                op.drop_column("outreach_events", column_name)

    if _table_exists(inspector, "candidate_lifecycle_events"):
        if _index_exists(inspector, "candidate_lifecycle_events", "ix_candidate_lifecycle_events_slack_installation_id"):
            op.drop_index("ix_candidate_lifecycle_events_slack_installation_id", table_name="candidate_lifecycle_events")
        for column_name in ("slack_user_id", "slack_team_id", "slack_installation_id"):
            if _column_exists(inspector, "candidate_lifecycle_events", column_name):
                op.drop_column("candidate_lifecycle_events", column_name)

    if _table_exists(inspector, "candidate_feedback"):
        if _index_exists(inspector, "candidate_feedback", "ix_candidate_feedback_slack_installation_id"):
            op.drop_index("ix_candidate_feedback_slack_installation_id", table_name="candidate_feedback")
        if _index_exists(inspector, "candidate_feedback", "ix_candidate_feedback_company_id"):
            op.drop_index("ix_candidate_feedback_company_id", table_name="candidate_feedback")
        for column_name in ("slack_user_id", "slack_team_id", "slack_installation_id", "company_id"):
            if _column_exists(inspector, "candidate_feedback", column_name):
                op.drop_column("candidate_feedback", column_name)

    if _table_exists(inspector, "jobs"):
        if _index_exists(inspector, "jobs", "ix_jobs_slack_installation_id"):
            op.drop_index("ix_jobs_slack_installation_id", table_name="jobs")
        for column_name in ("slack_user_id", "slack_team_id", "slack_installation_id"):
            if _column_exists(inspector, "jobs", column_name):
                op.drop_column("jobs", column_name)

    if _table_exists(inspector, "slack_users"):
        op.drop_table("slack_users")

    if _table_exists(inspector, "slack_installations"):
        op.drop_table("slack_installations")
