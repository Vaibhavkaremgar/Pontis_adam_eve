from __future__ import annotations

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


class LinkedInJobRepository(BaseRepository):
    model = LinkedInJobEntity


class LinkedInConnectionRepository(BaseRepository):
    model = LinkedInConnectionEntity


class LinkedInConversationRepository(BaseRepository):
    model = LinkedInConversationEntity


class LinkedInMessageRepository(BaseRepository):
    model = LinkedInMessageEntity


class LinkedInAttachmentRepository(BaseRepository):
    model = LinkedInAttachmentEntity
