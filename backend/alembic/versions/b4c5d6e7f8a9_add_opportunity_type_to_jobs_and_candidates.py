"""add opportunity type to jobs and candidates

Revision ID: b4c5d6e7f8a9
Revises: a3f8e2d1c9b7
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "a3f8e2d1c9b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_descriptions",
        sa.Column("opportunity_type", sa.String(length=16), nullable=False, server_default="jobs"),
    )
    op.create_index("ix_job_descriptions_opportunity_type", "job_descriptions", ["opportunity_type"])
    op.create_check_constraint(
        "ck_job_descriptions_opportunity_type",
        "job_descriptions",
        "opportunity_type IN ('jobs', 'intern')",
    )
    op.add_column(
        "candidates",
        sa.Column("opportunity_type", sa.String(length=16), nullable=False, server_default="jobs"),
    )
    op.create_index("ix_candidates_opportunity_type", "candidates", ["opportunity_type"])
    op.create_check_constraint(
        "ck_candidates_opportunity_type",
        "candidates",
        "opportunity_type IN ('jobs', 'intern')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_candidates_opportunity_type", "candidates", type_="check")
    op.drop_index("ix_candidates_opportunity_type", table_name="candidates")
    op.drop_column("candidates", "opportunity_type")
    op.drop_constraint("ck_job_descriptions_opportunity_type", "job_descriptions", type_="check")
    op.drop_index("ix_job_descriptions_opportunity_type", table_name="job_descriptions")
    op.drop_column("job_descriptions", "opportunity_type")
