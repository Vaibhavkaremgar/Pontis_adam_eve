"""add opportunity type to jobs and candidates

Revision ID: b4c5d6e7f8a9
Revises: a3f8e2d1c9b7
"""

import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "a3f8e2d1c9b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_equivalent_opportunity_type_check(inspector: sa.Inspector, table_name: str) -> bool:
    for constraint in inspector.get_check_constraints(table_name):
        sqltext = (constraint.get("sqltext") or "").lower().replace('"', "")
        string_values = set(re.findall(r"'([^']+)'", sqltext))
        uses_in_check = bool(re.search(r"\bin\s*\(", sqltext)) and not re.search(
            r"\bnot\s+in\s*\(", sqltext
        )
        uses_membership_check = uses_in_check or bool(re.search(r"=\s*any\s*\(", sqltext))
        if (
            re.search(r"\bopportunity_type\b", sqltext)
            and uses_membership_check
            and string_values == {"jobs", "intern"}
        ):
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()

    for table_name in ("job_descriptions", "candidates"):
        inspector = sa.inspect(bind)
        column_names = {column["name"] for column in inspector.get_columns(table_name)}
        if "opportunity_type" not in column_names:
            op.add_column(
                table_name,
                sa.Column(
                    "opportunity_type",
                    sa.String(length=16),
                    nullable=False,
                    server_default="jobs",
                ),
            )

        inspector = sa.inspect(bind)
        index_name = f"ix_{table_name}_opportunity_type"
        index_names = {index["name"] for index in inspector.get_indexes(table_name)}
        if index_name not in index_names:
            op.create_index(index_name, table_name, ["opportunity_type"])

        inspector = sa.inspect(bind)
        if not _has_equivalent_opportunity_type_check(inspector, table_name):
            op.create_check_constraint(
                f"ck_{table_name}_opportunity_type",
                table_name,
                "opportunity_type IN ('jobs', 'intern')",
            )


def downgrade() -> None:
    op.drop_constraint("ck_candidates_opportunity_type", "candidates", type_="check")
    op.drop_index("ix_candidates_opportunity_type", table_name="candidates")
    op.drop_column("candidates", "opportunity_type")
    op.drop_constraint("ck_job_descriptions_opportunity_type", "job_descriptions", type_="check")
    op.drop_index("ix_job_descriptions_opportunity_type", table_name="job_descriptions")
    op.drop_column("job_descriptions", "opportunity_type")
