"""repair slack schema drift

Revision ID: d5e6f7a8b9c1
Revises: 7e6d5c4b3a21
Create Date: 2026-06-01 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "d5e6f7a8b9c1"
down_revision: Union[str, Sequence[str], None] = "7e6d5c4b3a21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _fk_exists(inspector: sa.Inspector, table_name: str, fk_name: str) -> bool:
    return any(constraint["name"] == fk_name for constraint in inspector.get_foreign_keys(table_name))


def _create_index_if_missing(inspector: sa.Inspector, table_name: str, index_name: str, columns: list[str]) -> None:
    if _table_exists(inspector, table_name) and not _index_exists(inspector, table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=False)


def _create_fk_if_missing(
    inspector: sa.Inspector,
    table_name: str,
    fk_name: str,
    referred_table: str,
    local_columns: list[str],
    remote_columns: list[str],
) -> None:
    if _table_exists(inspector, table_name) and not _fk_exists(inspector, table_name, fk_name):
        op.create_foreign_key(fk_name, table_name, referred_table, local_columns, remote_columns)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _table_exists(inspector, "slack_installations"):
        op.create_table(
            "slack_installations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column("team_id", sa.String(length=64), nullable=False),
            sa.Column("team_name", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("enterprise_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("bot_user_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("bot_access_token", sa.Text(), nullable=False, server_default=""),
            sa.Column("scope_list", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("installed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("team_id", name="uq_slack_installations_team_id"),
        )
        inspector = inspect(bind)

    _create_index_if_missing(inspector, "slack_installations", "ix_slack_installations_company_id", ["company_id"])
    _create_index_if_missing(inspector, "slack_installations", "ix_slack_installations_team_id", ["team_id"])
    _create_index_if_missing(inspector, "slack_installations", "ix_slack_installations_is_active", ["is_active"])
    _create_index_if_missing(inspector, "slack_installations", "ix_slack_installations_company_active", ["company_id", "is_active"])
    _create_index_if_missing(inspector, "slack_installations", "ix_slack_installations_installed_by_user_id", ["installed_by_user_id"])

    if not _table_exists(inspector, "slack_users"):
        op.create_table(
            "slack_users",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("company_id", sa.String(length=36), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column("slack_installation_id", sa.String(length=36), sa.ForeignKey("slack_installations.id"), nullable=False),
            sa.Column("slack_user_id", sa.String(length=64), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=False, server_default=""),
            sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("internal_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="recruiter"),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("slack_installation_id", "slack_user_id", name="uq_slack_users_installation_user"),
        )
        inspector = inspect(bind)

    _create_index_if_missing(inspector, "slack_users", "ix_slack_users_company_id", ["company_id"])
    _create_index_if_missing(inspector, "slack_users", "ix_slack_users_slack_installation_id", ["slack_installation_id"])
    _create_index_if_missing(inspector, "slack_users", "ix_slack_users_slack_user_id", ["slack_user_id"])
    _create_index_if_missing(inspector, "slack_users", "ix_slack_users_internal_user_id", ["internal_user_id"])
    _create_index_if_missing(inspector, "slack_users", "ix_slack_users_company_slack_user", ["company_id", "slack_user_id"])

    if _table_exists(inspector, "jobs"):
        if not _column_exists(inspector, "jobs", "slack_installation_id"):
            op.add_column("jobs", sa.Column("slack_installation_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "jobs", "slack_team_id"):
            op.add_column("jobs", sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""))
        if not _column_exists(inspector, "jobs", "slack_user_id"):
            op.add_column("jobs", sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""))
        _create_fk_if_missing(inspector, "jobs", "fk_jobs_slack_installation_id", "slack_installations", ["slack_installation_id"], ["id"])
        _create_index_if_missing(inspector, "jobs", "ix_jobs_slack_installation_id", ["slack_installation_id"])

    if _table_exists(inspector, "outreach_events"):
        if not _column_exists(inspector, "outreach_events", "slack_installation_id"):
            op.add_column("outreach_events", sa.Column("slack_installation_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "outreach_events", "slack_team_id"):
            op.add_column("outreach_events", sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""))
        if not _column_exists(inspector, "outreach_events", "slack_user_id"):
            op.add_column("outreach_events", sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""))
        _create_fk_if_missing(inspector, "outreach_events", "fk_outreach_events_slack_installation_id", "slack_installations", ["slack_installation_id"], ["id"])
        _create_index_if_missing(inspector, "outreach_events", "ix_outreach_events_slack_installation_id", ["slack_installation_id"])

    if _table_exists(inspector, "candidate_feedback"):
        if not _column_exists(inspector, "candidate_feedback", "slack_installation_id"):
            op.add_column("candidate_feedback", sa.Column("slack_installation_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "candidate_feedback", "slack_team_id"):
            op.add_column("candidate_feedback", sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""))
        if not _column_exists(inspector, "candidate_feedback", "slack_user_id"):
            op.add_column("candidate_feedback", sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""))
        _create_fk_if_missing(inspector, "candidate_feedback", "fk_candidate_feedback_slack_installation_id", "slack_installations", ["slack_installation_id"], ["id"])
        _create_index_if_missing(inspector, "candidate_feedback", "ix_candidate_feedback_slack_installation_id", ["slack_installation_id"])

    if _table_exists(inspector, "interview_sessions"):
        if not _column_exists(inspector, "interview_sessions", "slack_installation_id"):
            op.add_column("interview_sessions", sa.Column("slack_installation_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "interview_sessions", "slack_team_id"):
            op.add_column("interview_sessions", sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""))
        if not _column_exists(inspector, "interview_sessions", "slack_user_id"):
            op.add_column("interview_sessions", sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""))
        _create_fk_if_missing(inspector, "interview_sessions", "fk_interview_sessions_slack_installation_id", "slack_installations", ["slack_installation_id"], ["id"])
        _create_index_if_missing(inspector, "interview_sessions", "ix_interview_sessions_slack_installation_id", ["slack_installation_id"])


def downgrade() -> None:
    pass
