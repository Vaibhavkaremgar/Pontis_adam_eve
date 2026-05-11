"""add resend inbound email tables

Revision ID: 3b1c2d4e5f60
Revises: 84956441b9c3
Create Date: 2026-05-11 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "3b1c2d4e5f60"
down_revision: Union[str, Sequence[str], None] = "84956441b9c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inbound_email_replies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("svix_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False, server_default="email.received"),
        sa.Column("email_id", sa.String(length=255), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("candidate_id", sa.String(length=128), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outreach_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sender_email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("sender_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("subject", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("webhook_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("match_status", sa.String(length=32), nullable=False, server_default="unmatched"),
        sa.Column("attachment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("processing_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["outreach_event_id"], ["outreach_events.id"]),
        sa.UniqueConstraint("svix_id", name="uq_inbound_email_replies_svix_id"),
        sa.UniqueConstraint("email_id", name="uq_inbound_email_replies_email_id"),
        sa.UniqueConstraint("provider_message_id", name="uq_inbound_email_replies_provider_message_id"),
    )

    op.create_index("ix_inbound_email_replies_svix_id", "inbound_email_replies", ["svix_id"], unique=False)
    op.create_index("ix_inbound_email_replies_email_id", "inbound_email_replies", ["email_id"], unique=False)
    op.create_index("ix_inbound_email_replies_candidate_id", "inbound_email_replies", ["candidate_id"], unique=False)
    op.create_index("ix_inbound_email_replies_job_id", "inbound_email_replies", ["job_id"], unique=False)
    op.create_index("ix_inbound_email_replies_outreach_event_id", "inbound_email_replies", ["outreach_event_id"], unique=False)

    op.create_table(
        "inbound_email_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("reply_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_attachment_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("filename", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("public_url", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["reply_id"], ["inbound_email_replies.id"]),
        sa.UniqueConstraint("reply_id", "provider_attachment_id", name="uq_inbound_email_attachments_reply_provider_attachment"),
    )
    op.create_index("ix_inbound_email_attachments_reply_id", "inbound_email_attachments", ["reply_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_inbound_email_attachments_reply_id", table_name="inbound_email_attachments")
    op.drop_table("inbound_email_attachments")

    op.drop_index("ix_inbound_email_replies_outreach_event_id", table_name="inbound_email_replies")
    op.drop_index("ix_inbound_email_replies_job_id", table_name="inbound_email_replies")
    op.drop_index("ix_inbound_email_replies_candidate_id", table_name="inbound_email_replies")
    op.drop_index("ix_inbound_email_replies_email_id", table_name="inbound_email_replies")
    op.drop_index("ix_inbound_email_replies_svix_id", table_name="inbound_email_replies")
    op.drop_table("inbound_email_replies")
