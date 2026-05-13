"""add company-scoped persistence and job intakes

Revision ID: d9e8f7a6b5c4
Revises: c7d8e9f0a1b2
Create Date: 2026-05-13 00:00:01.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d9e8f7a6b5c4"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_intakes",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("job_id", sa.String(length=36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("company_id", sa.String(length=36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False, server_default=""),
        sa.Column("structured_data_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("intake_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("job_id", name="uq_job_intakes_job"),
    )

    op.add_column("jobs", sa.Column("created_by", sa.String(length=36), nullable=False, server_default=""))
    op.add_column("jobs", sa.Column("remote_policy", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("jobs", sa.Column("experience_required", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("jobs", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")))

    op.add_column("candidate_profiles", sa.Column("company_id", sa.String(length=36), nullable=False, server_default=""))
    op.add_column("outreach_events", sa.Column("company_id", sa.String(length=36), nullable=False, server_default=""))
    op.add_column("outreach_events", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("inbound_email_replies", sa.Column("company_id", sa.String(length=36), nullable=True))
    op.add_column("interview_sessions", sa.Column("company_id", sa.String(length=36), nullable=False, server_default=""))
    op.add_column("interview_sessions", sa.Column("outreach_event_id", sa.String(length=36), nullable=True))
    op.add_column("interview_sessions", sa.Column("booking_url", sa.String(length=1024), nullable=False, server_default=""))

    op.create_index("ix_job_intakes_job_id", "job_intakes", ["job_id"], unique=True)
    op.create_index("ix_job_intakes_company_id", "job_intakes", ["company_id"], unique=False)
    op.create_index("ix_jobs_created_by", "jobs", ["created_by"], unique=False)
    op.create_index("ix_jobs_remote_policy", "jobs", ["remote_policy"], unique=False)
    op.create_index("ix_jobs_experience_required", "jobs", ["experience_required"], unique=False)
    op.create_index("ix_candidate_profiles_company_id", "candidate_profiles", ["company_id"], unique=False)
    op.create_index("ix_outreach_events_company_id", "outreach_events", ["company_id"], unique=False)
    op.create_index("ix_outreach_events_sent_at", "outreach_events", ["sent_at"], unique=False)
    op.create_index("ix_inbound_email_replies_company_id", "inbound_email_replies", ["company_id"], unique=False)
    op.create_index("ix_interview_sessions_company_id", "interview_sessions", ["company_id"], unique=False)
    op.create_index("ix_interview_sessions_outreach_event_id", "interview_sessions", ["outreach_event_id"], unique=False)

    op.execute(
        """
        UPDATE jobs
        SET created_by = COALESCE(companies.user_id, created_by)
        FROM companies
        WHERE jobs.company_id = companies.id
        """
    )
    op.execute(
        """
        UPDATE candidate_profiles
        SET company_id = COALESCE(candidate_profiles.company_id, jobs.company_id)
        FROM jobs
        WHERE candidate_profiles.job_id = jobs.id
        """
    )
    op.execute(
        """
        UPDATE outreach_events
        SET company_id = COALESCE(outreach_events.company_id, jobs.company_id)
        FROM jobs
        WHERE outreach_events.job_id = jobs.id
        """
    )
    op.execute(
        """
        UPDATE inbound_email_replies
        SET company_id = COALESCE(inbound_email_replies.company_id, jobs.company_id)
        FROM jobs
        WHERE inbound_email_replies.job_id = jobs.id
        """
    )
    op.execute(
        """
        UPDATE interview_sessions
        SET company_id = COALESCE(interview_sessions.company_id, jobs.company_id)
        FROM jobs
        WHERE interview_sessions.job_id = jobs.id
        """
    )
    op.execute(
        """
        UPDATE interview_sessions
        SET outreach_event_id = COALESCE(interview_sessions.outreach_event_id, outreach_events.id)
        FROM outreach_events
        WHERE interview_sessions.job_id = outreach_events.job_id
          AND interview_sessions.candidate_id = outreach_events.candidate_id
        """
    )

    op.alter_column("jobs", "created_by", nullable=False, server_default=None)
    op.alter_column("candidate_profiles", "company_id", nullable=False, server_default=None)
    op.alter_column("outreach_events", "company_id", nullable=False, server_default=None)
    op.alter_column("interview_sessions", "company_id", nullable=False, server_default=None)
    op.alter_column("jobs", "remote_policy", nullable=False, server_default=None)
    op.alter_column("jobs", "experience_required", nullable=False, server_default=None)
    op.alter_column("jobs", "updated_at", nullable=False, server_default=None)
    op.alter_column("jobs", "created_by", nullable=False, server_default=None)


def downgrade() -> None:
    op.drop_index("ix_interview_sessions_outreach_event_id", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_company_id", table_name="interview_sessions")
    op.drop_index("ix_inbound_email_replies_company_id", table_name="inbound_email_replies")
    op.drop_index("ix_outreach_events_sent_at", table_name="outreach_events")
    op.drop_index("ix_outreach_events_company_id", table_name="outreach_events")
    op.drop_index("ix_candidate_profiles_company_id", table_name="candidate_profiles")
    op.drop_index("ix_jobs_experience_required", table_name="jobs")
    op.drop_index("ix_jobs_remote_policy", table_name="jobs")
    op.drop_index("ix_jobs_created_by", table_name="jobs")
    op.drop_index("ix_job_intakes_company_id", table_name="job_intakes")
    op.drop_index("ix_job_intakes_job_id", table_name="job_intakes")

    op.drop_column("interview_sessions", "booking_url")
    op.drop_column("interview_sessions", "outreach_event_id")
    op.drop_column("interview_sessions", "company_id")
    op.drop_column("inbound_email_replies", "company_id")
    op.drop_column("outreach_events", "sent_at")
    op.drop_column("outreach_events", "company_id")
    op.drop_column("candidate_profiles", "company_id")
    op.drop_column("jobs", "updated_at")
    op.drop_column("jobs", "experience_required")
    op.drop_column("jobs", "remote_policy")
    op.drop_column("jobs", "created_by")
    op.drop_table("job_intakes")
