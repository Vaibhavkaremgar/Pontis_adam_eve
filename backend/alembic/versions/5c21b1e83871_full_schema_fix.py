"""full schema fix"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '5c21b1e83871'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_type(conn, table: str, column: str) -> str:
    row = conn.execute(sa.text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c LIMIT 1"
    ), {"t": table, "c": column}).fetchone()
    return (row[0] or "").lower() if row else ""


def _safe_alter_to_uuid(conn, table: str, column: str, nullable: bool = True) -> None:
    """Only alter if the column exists and is not already uuid."""
    current = _column_type(conn, table, column)
    if not current or current == "uuid":
        return
    null_clause = "DROP NOT NULL" if nullable else "SET NOT NULL"
    conn.execute(sa.text(
        f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
        f'TYPE UUID USING "{column}"::uuid, '
        f'ALTER COLUMN "{column}" {null_clause}'
    ))


def upgrade() -> None:
    conn = op.get_bind()

    _safe_alter_to_uuid(conn, "candidate_feedback", "recruiter_id", nullable=True)
    _safe_alter_to_uuid(conn, "candidate_feedback", "session_id", nullable=True)
    _safe_alter_to_uuid(conn, "ranking_explanations", "id", nullable=False)
    _safe_alter_to_uuid(conn, "ranking_explanations", "job_id", nullable=False)
    _safe_alter_to_uuid(conn, "ranking_runs", "id", nullable=False)
    _safe_alter_to_uuid(conn, "ranking_runs", "job_id", nullable=False)
    _safe_alter_to_uuid(conn, "ranking_runs", "recruiter_id", nullable=True)
    _safe_alter_to_uuid(conn, "recruiter_role_preferences", "id", nullable=False)
    _safe_alter_to_uuid(conn, "recruiter_role_preferences", "recruiter_id", nullable=False)
    _safe_alter_to_uuid(conn, "recruiter_skill_preferences", "id", nullable=False)


def downgrade() -> None:
    pass