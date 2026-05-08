"""add_performance_indexes_and_cleanup

Revision ID: a1b2c3d4e5f6
Revises: 5c21b1e83871
Create Date: 2025-01-01 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "5c21b1e83871"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── candidate_profiles: index on job_id + fit_score for ranking queries ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_candidate_profiles_job_fit
        ON candidate_profiles (job_id, fit_score DESC)
    """)

    # ── candidate_profiles: index on last_refreshed_at for stale detection ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_candidate_profiles_last_refreshed
        ON candidate_profiles (last_refreshed_at ASC)
    """)

    # ── candidate_feedback: index on recruiter_id for RLHF queries ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_candidate_feedback_recruiter
        ON candidate_feedback (recruiter_id)
        WHERE recruiter_id IS NOT NULL
    """)

    # ── candidate_feedback: index on updated_at for global sample queries ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_candidate_feedback_updated_at
        ON candidate_feedback (updated_at DESC)
    """)

    # ── outreach_events: index for follow-up due queries ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_outreach_events_followup
        ON outreach_events (status, next_follow_up_at, follow_up_count)
        WHERE status = 'sent'
    """)

    # ── outreach_events: index for learning cycle queries ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_outreach_events_learning
        ON outreach_events (status, learning_applied, responded_at)
        WHERE learning_applied = false
    """)

    # ── interviews: index on job_id + status for shortlisted queries ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_interviews_job_status
        ON interviews (job_id, status)
    """)

    # ── jobs: index on job_status + created_at for scheduler scan ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_jobs_status_created
        ON jobs (job_status, created_at DESC)
    """)

    # ── ranking_runs: index on created_at for cleanup ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ranking_runs_created_at
        ON ranking_runs (created_at DESC)
    """)

    # ── otps: index on email + expires_at for OTP lookup ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_otps_email_expires
        ON otps (email, expires_at)
        WHERE used = false
    """)

    # ── interview_sessions: index on expires_at for cleanup ──
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_interview_sessions_expires
        ON interview_sessions (expires_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_candidate_profiles_job_fit")
    op.execute("DROP INDEX IF EXISTS ix_candidate_profiles_last_refreshed")
    op.execute("DROP INDEX IF EXISTS ix_candidate_feedback_recruiter")
    op.execute("DROP INDEX IF EXISTS ix_candidate_feedback_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_outreach_events_followup")
    op.execute("DROP INDEX IF EXISTS ix_outreach_events_learning")
    op.execute("DROP INDEX IF EXISTS ix_interviews_job_status")
    op.execute("DROP INDEX IF EXISTS ix_jobs_status_created")
    op.execute("DROP INDEX IF EXISTS ix_ranking_runs_created_at")
    op.execute("DROP INDEX IF EXISTS ix_otps_email_expires")
    op.execute("DROP INDEX IF EXISTS ix_interview_sessions_expires")
