"""add_linkedin_module_foundation

Revision ID: c8d9e0f1a2b3
Revises: a0b1c2d3e4f5
Create Date: 2026-07-11 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "linkedin_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("company_id", sa.String(length=36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("linkedin_email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("browser_profile_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("daily_connection_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("daily_message_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("connections_sent_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_sent_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cookies_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("health", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_linkedin_accounts_company_id", "linkedin_accounts", ["company_id"])
    op.create_index("ix_linkedin_accounts_status", "linkedin_accounts", ["status"])
    op.create_index("ix_linkedin_accounts_health", "linkedin_accounts", ["health"])
    op.create_index("ix_linkedin_accounts_linkedin_email", "linkedin_accounts", ["linkedin_email"])

    op.create_table(
        "linkedin_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("linkedin_accounts.id"), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("worker_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_linkedin_jobs_candidate_id", "linkedin_jobs", ["candidate_id"])
    op.create_index("ix_linkedin_jobs_account_id", "linkedin_jobs", ["account_id"])
    op.create_index("ix_linkedin_jobs_status", "linkedin_jobs", ["status"])
    op.create_index("ix_linkedin_jobs_job_type", "linkedin_jobs", ["job_type"])
    op.create_index("ix_linkedin_jobs_scheduled_at", "linkedin_jobs", ["scheduled_at"])
    op.create_index("ix_linkedin_jobs_status_scheduled_at", "linkedin_jobs", ["status", "scheduled_at"])

    op.create_table(
        "linkedin_connections",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("linkedin_accounts.id"), nullable=False),
        sa.Column("linkedin_url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("connection_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("request_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profile_snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_linkedin_connections_candidate_id", "linkedin_connections", ["candidate_id"])
    op.create_index("ix_linkedin_connections_account_id", "linkedin_connections", ["account_id"])
    op.create_index("ix_linkedin_connections_connection_status", "linkedin_connections", ["connection_status"])

    op.create_table(
        "linkedin_conversations",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("linkedin_accounts.id"), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("conversation_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_linkedin_conversations_candidate_id", "linkedin_conversations", ["candidate_id"])
    op.create_index("ix_linkedin_conversations_account_id", "linkedin_conversations", ["account_id"])
    op.create_index("ix_linkedin_conversations_conversation_id", "linkedin_conversations", ["conversation_id"])
    op.create_index("ix_linkedin_conversations_conversation_status", "linkedin_conversations", ["conversation_status"])
    op.create_index("ix_linkedin_conversations_last_message_at", "linkedin_conversations", ["last_message_at"])

    op.create_table(
        "linkedin_messages",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("conversation_id", sa.String(length=36), sa.ForeignKey("linkedin_conversations.id"), nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("sender_type", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("message_type", sa.String(length=32), nullable=False, server_default="text"),
        sa.Column("message_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("linkedin_message_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("attachment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_linkedin_messages_conversation_id", "linkedin_messages", ["conversation_id"])
    op.create_index("ix_linkedin_messages_candidate_id", "linkedin_messages", ["candidate_id"])
    op.create_index("ix_linkedin_messages_sender_type", "linkedin_messages", ["sender_type"])
    op.create_index("ix_linkedin_messages_sent_at", "linkedin_messages", ["sent_at"])

    op.create_table(
        "linkedin_attachments",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("message_id", sa.String(length=36), sa.ForeignKey("linkedin_messages.id"), nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("storage_path", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("download_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_linkedin_attachments_message_id", "linkedin_attachments", ["message_id"])
    op.create_index("ix_linkedin_attachments_candidate_id", "linkedin_attachments", ["candidate_id"])
    op.create_index("ix_linkedin_attachments_download_status", "linkedin_attachments", ["download_status"])
    op.create_index("ix_linkedin_attachments_downloaded_at", "linkedin_attachments", ["downloaded_at"])


def downgrade() -> None:
    op.drop_index("ix_linkedin_attachments_downloaded_at", table_name="linkedin_attachments")
    op.drop_index("ix_linkedin_attachments_download_status", table_name="linkedin_attachments")
    op.drop_index("ix_linkedin_attachments_candidate_id", table_name="linkedin_attachments")
    op.drop_index("ix_linkedin_attachments_message_id", table_name="linkedin_attachments")
    op.drop_table("linkedin_attachments")

    op.drop_index("ix_linkedin_messages_sent_at", table_name="linkedin_messages")
    op.drop_index("ix_linkedin_messages_sender_type", table_name="linkedin_messages")
    op.drop_index("ix_linkedin_messages_candidate_id", table_name="linkedin_messages")
    op.drop_index("ix_linkedin_messages_conversation_id", table_name="linkedin_messages")
    op.drop_table("linkedin_messages")

    op.drop_index("ix_linkedin_conversations_last_message_at", table_name="linkedin_conversations")
    op.drop_index("ix_linkedin_conversations_conversation_status", table_name="linkedin_conversations")
    op.drop_index("ix_linkedin_conversations_conversation_id", table_name="linkedin_conversations")
    op.drop_index("ix_linkedin_conversations_account_id", table_name="linkedin_conversations")
    op.drop_index("ix_linkedin_conversations_candidate_id", table_name="linkedin_conversations")
    op.drop_table("linkedin_conversations")

    op.drop_index("ix_linkedin_connections_connection_status", table_name="linkedin_connections")
    op.drop_index("ix_linkedin_connections_account_id", table_name="linkedin_connections")
    op.drop_index("ix_linkedin_connections_candidate_id", table_name="linkedin_connections")
    op.drop_table("linkedin_connections")

    op.drop_index("ix_linkedin_jobs_status_scheduled_at", table_name="linkedin_jobs")
    op.drop_index("ix_linkedin_jobs_scheduled_at", table_name="linkedin_jobs")
    op.drop_index("ix_linkedin_jobs_job_type", table_name="linkedin_jobs")
    op.drop_index("ix_linkedin_jobs_status", table_name="linkedin_jobs")
    op.drop_index("ix_linkedin_jobs_account_id", table_name="linkedin_jobs")
    op.drop_index("ix_linkedin_jobs_candidate_id", table_name="linkedin_jobs")
    op.drop_table("linkedin_jobs")

    op.drop_index("ix_linkedin_accounts_linkedin_email", table_name="linkedin_accounts")
    op.drop_index("ix_linkedin_accounts_health", table_name="linkedin_accounts")
    op.drop_index("ix_linkedin_accounts_status", table_name="linkedin_accounts")
    op.drop_index("ix_linkedin_accounts_company_id", table_name="linkedin_accounts")
    op.drop_table("linkedin_accounts")
