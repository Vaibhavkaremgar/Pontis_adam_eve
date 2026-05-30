"""Drop one-row limits for selection sessions and outreach history.

Revision ID: 7f8a9b0c1d2e
Revises: 5c21b1e83871
Create Date: 2026-05-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "7f8a9b0c1d2e"
down_revision = "5c21b1e83871"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    def _constraint_exists(conn, name: str, table_name: str) -> bool:
        try:
            if conn.dialect.name == "postgresql":
                stmt = sa.text(
                    "SELECT 1 FROM pg_constraint WHERE conname = :name AND conrelid = :rel::regclass LIMIT 1"
                )
                return bool(conn.execute(stmt, {"name": name, "rel": table_name}).scalar())

            inspector = inspect(conn)
            # check unique constraints
            for uc in inspector.get_unique_constraints(table_name):
                if uc.get("name") == name:
                    return True
            # check foreign keys (some constraints are FK type)
            for fk in inspector.get_foreign_keys(table_name):
                if fk.get("name") == name:
                    return True
        except Exception:
            return False

    if _constraint_exists(bind, "uq_candidate_selection_sessions_job", "candidate_selection_sessions"):
        op.drop_constraint("uq_candidate_selection_sessions_job", "candidate_selection_sessions", type_="unique")

    if _constraint_exists(bind, "uq_outreach_events_job_candidate", "outreach_events"):
        op.drop_constraint("uq_outreach_events_job_candidate", "outreach_events", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("uq_outreach_events_job_candidate", "outreach_events", ["job_id", "candidate_id"])
    op.create_unique_constraint("uq_candidate_selection_sessions_job", "candidate_selection_sessions", ["job_id"])
