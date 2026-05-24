"""add orchestration sessions

Revision ID: 8f7e6d5c4b3a
Revises: 67925be04abf
Create Date: 2026-05-23 15:45:00.000000

"""

from __future__ import annotations

from uuid import uuid4
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "8f7e6d5c4b3a"
down_revision: Union[str, Sequence[str], None] = "67925be04abf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_default(dialect_name: str, *, is_list: bool = False) -> str:
    if dialect_name == "postgresql":
        return "'[]'::json" if is_list else "'{}'::json"
    return "'[]'" if is_list else "'{}'"


def _generate_unique_token(existing_tokens: set[str]) -> str:
    while True:
        token = str(uuid4())
        if token not in existing_tokens:
            existing_tokens.add(token)
            return token


def _backfill_orchestration_session_tokens(bind) -> None:
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    if "orchestration_sessions" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("orchestration_sessions")}
    if "session_token" not in columns:
        return

    rows = bind.execute(
        sa.text(
            """
            SELECT id, session_token
            FROM orchestration_sessions
            ORDER BY created_at ASC, id ASC
            """
        )
    ).fetchall()

    seen_tokens: set[str] = set()
    pending_updates: list[tuple[str, str]] = []
    for row in rows:
        row_id = str(row.id).strip()
        token = str(getattr(row, "session_token", "") or "").strip()
        if not row_id:
            continue
        if not token or token in seen_tokens:
            token = _generate_unique_token(seen_tokens)
            pending_updates.append((row_id, token))
            continue
        seen_tokens.add(token)

    for row_id, token in pending_updates:
        bind.execute(
            sa.text(
                """
                UPDATE orchestration_sessions
                SET session_token = :session_token
                WHERE id = :id
                """
            ),
            {"id": row_id, "session_token": token},
        )

    null_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM orchestration_sessions WHERE session_token IS NULL OR session_token = ''")
    ).scalar()
    duplicate_count = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM (
                SELECT session_token
                FROM orchestration_sessions
                GROUP BY session_token
                HAVING COUNT(*) > 1
            ) dupes
            """
        )
    ).scalar()
    if int(null_count or 0) > 0 or int(duplicate_count or 0) > 0:
        raise RuntimeError(
            "Unable to backfill unique orchestration session tokens safely "
            f"(null_count={int(null_count or 0)}, duplicate_count={int(duplicate_count or 0)})"
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    dialect = bind.dialect.name
    table_names = set(inspector.get_table_names())

    if "orchestration_sessions" not in table_names:
        op.create_table(
            "orchestration_sessions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("session_token", sa.String(length=255), nullable=False, unique=True),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="slack"),
            sa.Column("current_stage", sa.String(length=32), nullable=False, server_default="initiated"),
            sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("slack_channel_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("slack_thread_ts", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("intake_mode", sa.String(length=32), nullable=False, server_default="slack"),
            sa.Column("selected_path", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("current_question", sa.Text(), nullable=False, server_default=""),
            sa.Column("current_question_key", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("current_question_type", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("current_question_schema", sa.JSON(), nullable=False, server_default=_json_default(dialect)),
            sa.Column("structured_context", sa.JSON(), nullable=False, server_default=_json_default(dialect)),
            sa.Column("raw_conversation", sa.JSON(), nullable=False, server_default=_json_default(dialect, is_list=True)),
            sa.Column("normalized_intake", sa.JSON(), nullable=False, server_default=_json_default(dialect)),
            sa.Column("voice_context", sa.JSON(), nullable=False, server_default=_json_default(dialect)),
            sa.Column("slack_context", sa.JSON(), nullable=False, server_default=_json_default(dialect)),
            sa.Column("voice_handoff_token", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("voice_handoff_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("voice_handoff_consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("voice_token_used", sa.Boolean(), nullable=False, server_default=sa.text("false" if dialect == "postgresql" else "0")),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("company_id", sa.String(length=36), nullable=True),
            sa.Column("job_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()" if dialect == "postgresql" else "CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()" if dialect == "postgresql" else "CURRENT_TIMESTAMP")),
        )
    else:
        orchestration_columns = {column["name"] for column in inspector.get_columns("orchestration_sessions")}
        add_columns = [
            ("session_token", sa.String(length=255), None),
            ("source", sa.String(length=32), sa.text("'slack'")),
            ("current_stage", sa.String(length=32), sa.text("'initiated'")),
            ("slack_team_id", sa.String(length=64), sa.text("''")),
            ("slack_channel_id", sa.String(length=64), sa.text("''")),
            ("slack_thread_ts", sa.String(length=64), sa.text("''")),
            ("slack_user_id", sa.String(length=64), sa.text("''")),
            ("intake_mode", sa.String(length=32), sa.text("'slack'")),
            ("selected_path", sa.String(length=32), sa.text("''")),
            ("current_question", sa.Text(), sa.text("''")),
            ("current_question_key", sa.String(length=128), sa.text("''")),
            ("current_question_type", sa.String(length=64), sa.text("''")),
            ("current_question_schema", sa.JSON(), sa.text(_json_default(dialect))),
            ("structured_context", sa.JSON(), sa.text(_json_default(dialect))),
            ("raw_conversation", sa.JSON(), sa.text(_json_default(dialect, is_list=True))),
            ("normalized_intake", sa.JSON(), sa.text(_json_default(dialect))),
            ("voice_context", sa.JSON(), sa.text(_json_default(dialect))),
            ("slack_context", sa.JSON(), sa.text(_json_default(dialect))),
            ("voice_handoff_token", sa.String(length=255), sa.text("''")),
            ("voice_handoff_expires_at", sa.DateTime(timezone=True), None),
            ("voice_handoff_consumed_at", sa.DateTime(timezone=True), None),
            ("voice_token_used", sa.Boolean(), sa.text("false" if dialect == "postgresql" else "0")),
            ("expires_at", sa.DateTime(timezone=True), None),
            ("completed_at", sa.DateTime(timezone=True), None),
            ("state_version", sa.Integer(), sa.text("0")),
            ("last_processed_message_ts", sa.String(length=64), sa.text("''")),
            ("last_processed_action_hash", sa.String(length=64), sa.text("''")),
            ("last_processed_transcript_hash", sa.String(length=64), sa.text("''")),
            ("intake_version", sa.String(length=32), sa.text("'v1'")),
            ("company_id", sa.String(length=36), None),
            ("job_id", sa.String(length=36), None),
        ]
        for column_name, column_type, server_default in add_columns:
            if column_name in orchestration_columns:
                continue
            op.add_column(
                "orchestration_sessions",
                sa.Column(column_name, column_type, nullable=server_default is None, server_default=server_default),
            )
        if dialect == "postgresql" and "session_token" in orchestration_columns:
            op.alter_column(
                "orchestration_sessions",
                "session_token",
                existing_type=sa.String(length=255),
                nullable=True,
                server_default=None,
            )

        _backfill_orchestration_session_tokens(bind)
        if dialect == "postgresql":
            op.alter_column(
                "orchestration_sessions",
                "session_token",
                existing_type=sa.String(length=255),
                nullable=False,
            )

        index_names = {index["name"] for index in inspector.get_indexes("orchestration_sessions")}
        if "uq_orchestration_sessions_session_token" not in index_names:
            op.create_index(
                "uq_orchestration_sessions_session_token",
                "orchestration_sessions",
                ["session_token"],
                unique=True,
            )

    if "orchestration_events" not in table_names:
        op.create_table(
            "orchestration_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("session_id", sa.String(length=36), sa.ForeignKey("orchestration_sessions.id"), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("event_payload", sa.JSON(), nullable=False, server_default=_json_default(dialect)),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="slack"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()" if dialect == "postgresql" else "CURRENT_TIMESTAMP")),
        )
    else:
        event_columns = {column["name"] for column in inspector.get_columns("orchestration_events")}
        add_columns = [
            ("session_id", sa.String(length=36), sa.text("''")),
            ("event_type", sa.String(length=64), sa.text("''")),
            ("event_payload", sa.JSON(), sa.text(_json_default(dialect))),
            ("source", sa.String(length=32), sa.text("'slack'")),
            ("created_at", sa.DateTime(timezone=True), None),
        ]
        for column_name, column_type, server_default in add_columns:
            if column_name in event_columns:
                continue
            op.add_column(
                "orchestration_events",
                sa.Column(column_name, column_type, nullable=server_default is None, server_default=server_default),
            )


def downgrade() -> None:
    op.drop_table("orchestration_events")
    op.drop_table("orchestration_sessions")
