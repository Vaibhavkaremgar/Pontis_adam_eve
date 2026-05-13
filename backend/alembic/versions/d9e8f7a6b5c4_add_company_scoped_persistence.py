"""add company-scoped persistence and job intakes

Revision ID: d9e8f7a6b5c4
Revises: c7d8e9f0a1b2
Create Date: 2026-05-13 00:00:01.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "d9e8f7a6b5c4"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    is_sqlite = bind.dialect.name == "sqlite"
    created_job_intakes = False

    if not _table_exists(inspector, "job_intakes"):
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
        created_job_intakes = True
        inspector = inspect(bind)

    if _table_exists(inspector, "jobs"):
        if not _column_exists(inspector, "jobs", "created_by"):
            op.add_column("jobs", sa.Column("created_by", sa.String(length=36), nullable=is_sqlite, server_default=None if is_sqlite else ""))
        if not _column_exists(inspector, "jobs", "remote_policy"):
            op.add_column("jobs", sa.Column("remote_policy", sa.String(length=64), nullable=is_sqlite, server_default=None if is_sqlite else ""))
        if not _column_exists(inspector, "jobs", "experience_required"):
            op.add_column("jobs", sa.Column("experience_required", sa.String(length=255), nullable=is_sqlite, server_default=None if is_sqlite else ""))
        if not _column_exists(inspector, "jobs", "updated_at"):
            op.add_column(
                "jobs",
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    nullable=is_sqlite,
                    server_default=None if is_sqlite else sa.text("NOW()"),
                ),
            )

    if _table_exists(inspector, "candidate_profiles") and not _column_exists(inspector, "candidate_profiles", "company_id"):
        op.add_column("candidate_profiles", sa.Column("company_id", sa.String(length=36), nullable=is_sqlite, server_default=None if is_sqlite else ""))
    if _table_exists(inspector, "outreach_events"):
        if not _column_exists(inspector, "outreach_events", "company_id"):
            op.add_column("outreach_events", sa.Column("company_id", sa.String(length=36), nullable=is_sqlite, server_default=None if is_sqlite else ""))
        if not _column_exists(inspector, "outreach_events", "sent_at"):
            op.add_column("outreach_events", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    if _table_exists(inspector, "inbound_email_replies") and not _column_exists(inspector, "inbound_email_replies", "company_id"):
        op.add_column("inbound_email_replies", sa.Column("company_id", sa.String(length=36), nullable=True))
    if _table_exists(inspector, "interview_sessions"):
        if not _column_exists(inspector, "interview_sessions", "company_id"):
            op.add_column("interview_sessions", sa.Column("company_id", sa.String(length=36), nullable=is_sqlite, server_default=None if is_sqlite else ""))
        if not _column_exists(inspector, "interview_sessions", "outreach_event_id"):
            op.add_column("interview_sessions", sa.Column("outreach_event_id", sa.String(length=36), nullable=True))
        if not _column_exists(inspector, "interview_sessions", "booking_url"):
            op.add_column("interview_sessions", sa.Column("booking_url", sa.String(length=1024), nullable=is_sqlite, server_default=None if is_sqlite else ""))

    if created_job_intakes or _table_exists(inspector, "job_intakes"):
        if not _index_exists(inspector, "job_intakes", "ix_job_intakes_job_id"):
            op.create_index("ix_job_intakes_job_id", "job_intakes", ["job_id"], unique=True)
        if not _index_exists(inspector, "job_intakes", "ix_job_intakes_company_id"):
            op.create_index("ix_job_intakes_company_id", "job_intakes", ["company_id"], unique=False)
    if _table_exists(inspector, "jobs"):
        if not _index_exists(inspector, "jobs", "ix_jobs_created_by"):
            op.create_index("ix_jobs_created_by", "jobs", ["created_by"], unique=False)
        if not _index_exists(inspector, "jobs", "ix_jobs_remote_policy"):
            op.create_index("ix_jobs_remote_policy", "jobs", ["remote_policy"], unique=False)
        if not _index_exists(inspector, "jobs", "ix_jobs_experience_required"):
            op.create_index("ix_jobs_experience_required", "jobs", ["experience_required"], unique=False)
    if _table_exists(inspector, "candidate_profiles") and not _index_exists(inspector, "candidate_profiles", "ix_candidate_profiles_company_id"):
        op.create_index("ix_candidate_profiles_company_id", "candidate_profiles", ["company_id"], unique=False)
    if _table_exists(inspector, "outreach_events"):
        if not _index_exists(inspector, "outreach_events", "ix_outreach_events_company_id"):
            op.create_index("ix_outreach_events_company_id", "outreach_events", ["company_id"], unique=False)
        if not _index_exists(inspector, "outreach_events", "ix_outreach_events_sent_at"):
            op.create_index("ix_outreach_events_sent_at", "outreach_events", ["sent_at"], unique=False)
    if _table_exists(inspector, "inbound_email_replies") and not _index_exists(inspector, "inbound_email_replies", "ix_inbound_email_replies_company_id"):
        op.create_index("ix_inbound_email_replies_company_id", "inbound_email_replies", ["company_id"], unique=False)
    if _table_exists(inspector, "interview_sessions"):
        if not _index_exists(inspector, "interview_sessions", "ix_interview_sessions_company_id"):
            op.create_index("ix_interview_sessions_company_id", "interview_sessions", ["company_id"], unique=False)
        if not _index_exists(inspector, "interview_sessions", "ix_interview_sessions_outreach_event_id"):
            op.create_index("ix_interview_sessions_outreach_event_id", "interview_sessions", ["outreach_event_id"], unique=False)

    op.execute(
        """
        UPDATE jobs
        SET created_by = COALESCE(
            jobs.created_by,
            companies.user_id,
            '00000000-0000-0000-0000-000000000000'::uuid
        ),
            remote_policy = COALESCE(jobs.remote_policy, 'unspecified'),
            experience_required = COALESCE(jobs.experience_required, 0),
            updated_at = COALESCE(jobs.updated_at, NOW())
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

    if not is_sqlite:
        op.alter_column("jobs", "created_by", nullable=False, server_default=None)
        op.alter_column("candidate_profiles", "company_id", nullable=False, server_default=None)
        op.alter_column("outreach_events", "company_id", nullable=False, server_default=None)
        op.alter_column("interview_sessions", "company_id", nullable=False, server_default=None)
        op.alter_column("jobs", "remote_policy", nullable=False, server_default=None)
        op.alter_column("jobs", "experience_required", nullable=False, server_default=None)
        op.alter_column("jobs", "updated_at", nullable=False, server_default=None)
        op.alter_column("jobs", "created_by", nullable=False, server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "interview_sessions"):
        if _index_exists(inspector, "interview_sessions", "ix_interview_sessions_outreach_event_id"):
            op.drop_index("ix_interview_sessions_outreach_event_id", table_name="interview_sessions")
        if _index_exists(inspector, "interview_sessions", "ix_interview_sessions_company_id"):
            op.drop_index("ix_interview_sessions_company_id", table_name="interview_sessions")
    if _table_exists(inspector, "inbound_email_replies") and _index_exists(inspector, "inbound_email_replies", "ix_inbound_email_replies_company_id"):
        op.drop_index("ix_inbound_email_replies_company_id", table_name="inbound_email_replies")
    if _table_exists(inspector, "outreach_events"):
        if _index_exists(inspector, "outreach_events", "ix_outreach_events_sent_at"):
            op.drop_index("ix_outreach_events_sent_at", table_name="outreach_events")
        if _index_exists(inspector, "outreach_events", "ix_outreach_events_company_id"):
            op.drop_index("ix_outreach_events_company_id", table_name="outreach_events")
    if _table_exists(inspector, "candidate_profiles") and _index_exists(inspector, "candidate_profiles", "ix_candidate_profiles_company_id"):
        op.drop_index("ix_candidate_profiles_company_id", table_name="candidate_profiles")
    if _table_exists(inspector, "jobs"):
        if _index_exists(inspector, "jobs", "ix_jobs_experience_required"):
            op.drop_index("ix_jobs_experience_required", table_name="jobs")
        if _index_exists(inspector, "jobs", "ix_jobs_remote_policy"):
            op.drop_index("ix_jobs_remote_policy", table_name="jobs")
        if _index_exists(inspector, "jobs", "ix_jobs_created_by"):
            op.drop_index("ix_jobs_created_by", table_name="jobs")
    if _table_exists(inspector, "job_intakes"):
        if _index_exists(inspector, "job_intakes", "ix_job_intakes_company_id"):
            op.drop_index("ix_job_intakes_company_id", table_name="job_intakes")
        if _index_exists(inspector, "job_intakes", "ix_job_intakes_job_id"):
            op.drop_index("ix_job_intakes_job_id", table_name="job_intakes")

    if _table_exists(inspector, "interview_sessions"):
        if _column_exists(inspector, "interview_sessions", "booking_url"):
            op.drop_column("interview_sessions", "booking_url")
        if _column_exists(inspector, "interview_sessions", "outreach_event_id"):
            op.drop_column("interview_sessions", "outreach_event_id")
        if _column_exists(inspector, "interview_sessions", "company_id"):
            op.drop_column("interview_sessions", "company_id")
    if _table_exists(inspector, "inbound_email_replies") and _column_exists(inspector, "inbound_email_replies", "company_id"):
        op.drop_column("inbound_email_replies", "company_id")
    if _table_exists(inspector, "outreach_events"):
        if _column_exists(inspector, "outreach_events", "sent_at"):
            op.drop_column("outreach_events", "sent_at")
        if _column_exists(inspector, "outreach_events", "company_id"):
            op.drop_column("outreach_events", "company_id")
    if _table_exists(inspector, "candidate_profiles") and _column_exists(inspector, "candidate_profiles", "company_id"):
        op.drop_column("candidate_profiles", "company_id")
    if _table_exists(inspector, "jobs"):
        for column_name in ("updated_at", "experience_required", "remote_policy", "created_by"):
            if _column_exists(inspector, "jobs", column_name):
                op.drop_column("jobs", column_name)
    if _table_exists(inspector, "job_intakes"):
        op.drop_table("job_intakes")
