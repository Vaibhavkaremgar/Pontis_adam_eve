from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.models.entities import Base, GUID


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LinkedInAccountEntity(Base):
    __tablename__ = "linkedin_accounts"
    __table_args__ = (
        Index("ix_linkedin_accounts_agency_id", "agency_id"),
        Index("ix_linkedin_accounts_status", "status"),
        Index("ix_linkedin_accounts_health", "health"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    agency_id: Mapped[str] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=False, index=True)
    company_id: Mapped[str] = synonym("agency_id")
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    linkedin_email: Mapped[str] = mapped_column(String(320), nullable=False, default="", index=True)
    browser_profile_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    daily_connection_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    daily_message_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    connections_sent_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_sent_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cookies_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    health: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    agency = relationship("CompanyEntity")
    jobs = relationship("LinkedInJobEntity", back_populates="account")
    connections = relationship("LinkedInConnectionEntity", back_populates="account")
    conversations = relationship("LinkedInConversationEntity", back_populates="account")


class LinkedInJobEntity(Base):
    __tablename__ = "linkedin_jobs"
    __table_args__ = (
        Index("ix_linkedin_jobs_candidate_id", "candidate_id"),
        Index("ix_linkedin_jobs_account_id", "account_id"),
        Index("ix_linkedin_jobs_status", "status"),
        Index("ix_linkedin_jobs_job_type", "job_type"),
        Index("ix_linkedin_jobs_scheduled_at", "scheduled_at"),
        Index("ix_linkedin_jobs_status_scheduled_at", "status", "scheduled_at"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(GUID(), ForeignKey("linkedin_accounts.id"), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    account = relationship("LinkedInAccountEntity", back_populates="jobs")


class LinkedInConnectionEntity(Base):
    __tablename__ = "linkedin_connections"
    __table_args__ = (
        Index("ix_linkedin_connections_candidate_id", "candidate_id"),
        Index("ix_linkedin_connections_account_id", "account_id"),
        Index("ix_linkedin_connections_connection_status", "connection_status"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(GUID(), ForeignKey("linkedin_accounts.id"), nullable=False, index=True)
    linkedin_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    connection_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", index=True)
    request_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    profile_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    account = relationship("LinkedInAccountEntity", back_populates="connections")


class LinkedInConversationEntity(Base):
    __tablename__ = "linkedin_conversations"
    __table_args__ = (
        Index("ix_linkedin_conversations_candidate_id", "candidate_id"),
        Index("ix_linkedin_conversations_account_id", "account_id"),
        Index("ix_linkedin_conversations_conversation_id", "conversation_id"),
        Index("ix_linkedin_conversations_conversation_status", "conversation_status"),
        Index("ix_linkedin_conversations_last_message_at", "last_message_at"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(GUID(), ForeignKey("linkedin_accounts.id"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    conversation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None, index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    account = relationship("LinkedInAccountEntity", back_populates="conversations")
    messages = relationship("LinkedInMessageEntity", back_populates="conversation")


class LinkedInMessageEntity(Base):
    __tablename__ = "linkedin_messages"
    __table_args__ = (
        Index("ix_linkedin_messages_conversation_id", "conversation_id"),
        Index("ix_linkedin_messages_candidate_id", "candidate_id"),
        Index("ix_linkedin_messages_sender_type", "sender_type"),
        Index("ix_linkedin_messages_sent_at", "sent_at"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(GUID(), ForeignKey("linkedin_conversations.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sender_type: Mapped[str] = mapped_column(String(32), nullable=False, default="system", index=True)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    message_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    linkedin_message_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    conversation = relationship("LinkedInConversationEntity", back_populates="messages")
    attachments = relationship("LinkedInAttachmentEntity", back_populates="message")


class LinkedInAttachmentEntity(Base):
    __tablename__ = "linkedin_attachments"
    __table_args__ = (
        Index("ix_linkedin_attachments_message_id", "message_id"),
        Index("ix_linkedin_attachments_candidate_id", "candidate_id"),
        Index("ix_linkedin_attachments_download_status", "download_status"),
        Index("ix_linkedin_attachments_downloaded_at", "downloaded_at"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    message_id: Mapped[str] = mapped_column(GUID(), ForeignKey("linkedin_messages.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    download_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    message = relationship("LinkedInMessageEntity", back_populates="attachments")
