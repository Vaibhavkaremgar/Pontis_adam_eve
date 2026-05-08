"""add internal candidate resumes

Revision ID: 9c7d1e2f3a4b
Revises: 2a4f6d7e8c90
Create Date: 2026-05-08 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "9c7d1e2f3a4b"
down_revision = "2a4f6d7e8c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "internal_candidate_resumes",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("resume_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("source_filename", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("source_path", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("source_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("full_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("headline", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("years_experience", sa.Float(), nullable=False, server_default="0"),
        sa.Column("skills", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("companies", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("education", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("projects", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("certifications", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("location", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("domain_experience", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("raw_resume_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("parsed_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("embedding_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("vector_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("qdrant_point_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("resume_fingerprint", name="uq_internal_candidate_resumes_fingerprint"),
        sa.UniqueConstraint("candidate_id", name="uq_internal_candidate_resumes_candidate_id"),
    )
    op.create_index("ix_internal_candidate_resumes_candidate_id", "internal_candidate_resumes", ["candidate_id"])
    op.create_index("ix_internal_candidate_resumes_resume_fingerprint", "internal_candidate_resumes", ["resume_fingerprint"])
    op.create_index("ix_internal_candidate_resumes_embedding_version", "internal_candidate_resumes", ["embedding_version"])


def downgrade() -> None:
    op.drop_index("ix_internal_candidate_resumes_embedding_version", table_name="internal_candidate_resumes")
    op.drop_index("ix_internal_candidate_resumes_resume_fingerprint", table_name="internal_candidate_resumes")
    op.drop_index("ix_internal_candidate_resumes_candidate_id", table_name="internal_candidate_resumes")
    op.drop_table("internal_candidate_resumes")
