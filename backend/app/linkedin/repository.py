from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.linkedin.models import (
    LinkedInAccountEntity,
    LinkedInAttachmentEntity,
    LinkedInConnectionEntity,
    LinkedInConversationEntity,
    LinkedInJobEntity,
    LinkedInMessageEntity,
)


class BaseRepository:
    model = None

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, obj):
        self.session.add(obj)
        self.session.flush()
        return obj

    def get(self, object_id: str):
        return self.session.get(self.model, object_id)

    def list(self, limit: int = 100, offset: int = 0):
        return self.session.query(self.model).offset(offset).limit(limit).all()

    def delete(self, obj) -> None:
        self.session.delete(obj)
        self.session.flush()


class LinkedInAccountRepository(BaseRepository):
    model = LinkedInAccountEntity

    def mark_unhealthy(self, account_id: str) -> None:
        row = self.session.get(LinkedInAccountEntity, account_id)
        if row is not None:
            row.health = "unhealthy"
            row.updated_at = datetime.now(timezone.utc)
            self.session.flush()


class LinkedInJobRepository(BaseRepository):
    model = LinkedInJobEntity


class LinkedInConnectionRepository(BaseRepository):
    model = LinkedInConnectionEntity

    def list_pending(self) -> list[LinkedInConnectionEntity]:
        return (
            self.session.query(LinkedInConnectionEntity)
            .filter(LinkedInConnectionEntity.connection_status == "requested")
            .all()
        )

    def mark_accepted(self, connection_id: str) -> None:
        row = self.session.get(LinkedInConnectionEntity, connection_id)
        if row is not None:
            now = datetime.now(timezone.utc)
            row.connection_status = "accepted"
            row.accepted_at = now
            row.last_checked_at = now
            row.updated_at = now
            self.session.flush()

    def mark_checked(self, connection_id: str) -> None:
        row = self.session.get(LinkedInConnectionEntity, connection_id)
        if row is not None:
            now = datetime.now(timezone.utc)
            row.last_checked_at = now
            row.updated_at = now
            self.session.flush()


class LinkedInConversationRepository(BaseRepository):
    model = LinkedInConversationEntity

    def list_for_account(self, account_id: str) -> list[LinkedInConversationEntity]:
        return (
            self.session.query(LinkedInConversationEntity)
            .filter(LinkedInConversationEntity.account_id == account_id)
            .all()
        )

    def find_by_linkedin_id(
        self, account_id: str, conversation_id: str
    ) -> LinkedInConversationEntity | None:
        return (
            self.session.query(LinkedInConversationEntity)
            .filter(
                LinkedInConversationEntity.account_id == account_id,
                LinkedInConversationEntity.conversation_id == conversation_id,
            )
            .first()
        )

    def touch_synced(
        self,
        conversation_row: LinkedInConversationEntity,
        *,
        last_message_at: datetime | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        conversation_row.last_synced_at = now
        conversation_row.updated_at = now
        if last_message_at is not None:
            conversation_row.last_message_at = last_message_at
        self.session.flush()


class LinkedInMessageRepository(BaseRepository):
    model = LinkedInMessageEntity

    def exists_by_linkedin_id(
        self, conversation_id: str, linkedin_message_id: str
    ) -> bool:
        """Return True if a message with this linkedin_message_id already exists."""
        if not linkedin_message_id:
            return False
        return (
            self.session.query(LinkedInMessageEntity.id)
            .filter(
                LinkedInMessageEntity.conversation_id == conversation_id,
                LinkedInMessageEntity.linkedin_message_id == linkedin_message_id,
            )
            .first()
        ) is not None

    def exists_by_text_and_time(
        self,
        conversation_id: str,
        message_text: str,
        sent_at: datetime,
        *,
        window_seconds: int = 60,
    ) -> bool:
        """Fallback deduplication when linkedin_message_id is unavailable.

        Returns True if a message with the same text exists within
        window_seconds of sent_at.
        """
        from sqlalchemy import func
        from datetime import timedelta

        lo = sent_at.replace(tzinfo=timezone.utc) - timedelta(seconds=window_seconds)
        hi = sent_at.replace(tzinfo=timezone.utc) + timedelta(seconds=window_seconds)
        return (
            self.session.query(LinkedInMessageEntity.id)
            .filter(
                LinkedInMessageEntity.conversation_id == conversation_id,
                LinkedInMessageEntity.message_text == message_text,
                LinkedInMessageEntity.sent_at >= lo,
                LinkedInMessageEntity.sent_at <= hi,
            )
            .first()
        ) is not None


class LinkedInAttachmentRepository(BaseRepository):
    model = LinkedInAttachmentEntity
