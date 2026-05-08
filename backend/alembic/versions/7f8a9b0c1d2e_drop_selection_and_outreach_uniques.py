"""Drop one-row limits for selection sessions and outreach history.

Revision ID: 7f8a9b0c1d2e
Revises: 5c21b1e83871
Create Date: 2026-05-07 00:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "7f8a9b0c1d2e"
down_revision = "5c21b1e83871"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_candidate_selection_sessions_job", "candidate_selection_sessions", type_="unique")
    op.drop_constraint("uq_outreach_events_job_candidate", "outreach_events", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("uq_outreach_events_job_candidate", "outreach_events", ["job_id", "candidate_id"])
    op.create_unique_constraint("uq_candidate_selection_sessions_job", "candidate_selection_sessions", ["job_id"])
