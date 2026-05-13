"""add reply processing and resume fields

Revision ID: 20260513_add_reply_processing_resume_fields
Revises: 84956441b9c3
Create Date: 2026-05-13 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260513_add_reply_processing_resume_fields"
down_revision = "84956441b9c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidate_profiles", sa.Column("candidate_status", sa.String(length=64), nullable=False, server_default="new"))
    op.add_column("candidate_profiles", sa.Column("resume_received_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("candidate_profiles", sa.Column("total_experience_years", sa.Float(), nullable=False, server_default="0"))
    op.add_column("candidate_profiles", sa.Column("current_title", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("candidate_profiles", sa.Column("current_company", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("candidate_profiles", sa.Column("phone", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("candidate_profiles", sa.Column("linkedin_url", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("candidate_profiles", sa.Column("github_url", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("candidate_profiles", sa.Column("parsed_resume_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("candidate_profiles", sa.Column("parsed_resume_text", sa.Text(), nullable=False, server_default=""))

    op.add_column("outreach_events", sa.Column("reply_intent", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("inbound_email_replies", sa.Column("intent", sa.String(length=64), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("inbound_email_replies", "intent")
    op.drop_column("outreach_events", "reply_intent")

    op.drop_column("candidate_profiles", "parsed_resume_text")
    op.drop_column("candidate_profiles", "parsed_resume_json")
    op.drop_column("candidate_profiles", "github_url")
    op.drop_column("candidate_profiles", "linkedin_url")
    op.drop_column("candidate_profiles", "phone")
    op.drop_column("candidate_profiles", "current_company")
    op.drop_column("candidate_profiles", "current_title")
    op.drop_column("candidate_profiles", "total_experience_years")
    op.drop_column("candidate_profiles", "resume_received_at")
    op.drop_column("candidate_profiles", "candidate_status")
