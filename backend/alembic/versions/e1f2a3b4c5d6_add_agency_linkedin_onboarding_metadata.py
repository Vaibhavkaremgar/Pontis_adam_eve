"""add agency LinkedIn onboarding metadata

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
"""

from alembic import op
import sqlalchemy as sa


revision = "e1f2a3b4c5d6"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agencies", sa.Column("linkedin_email", sa.String(320), nullable=True))
    op.add_column("agencies", sa.Column("linkedin_connected", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("agencies", sa.Column("linkedin_connected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agencies", sa.Column("linkedin_last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agencies", sa.Column("linkedin_profile_path", sa.Text(), nullable=True))
    op.add_column("agencies", sa.Column("linkedin_connection_status", sa.String(32), nullable=False, server_default="pending"))
    op.alter_column("agencies", "linkedin_connected", server_default=None)
    op.alter_column("agencies", "linkedin_connection_status", server_default=None)


def downgrade() -> None:
    for column in (
        "linkedin_connection_status",
        "linkedin_profile_path",
        "linkedin_last_verified_at",
        "linkedin_connected_at",
        "linkedin_connected",
        "linkedin_email",
    ):
        op.drop_column("agencies", column)
