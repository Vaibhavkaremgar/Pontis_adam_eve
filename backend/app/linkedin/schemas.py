from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LinkedInAccount(BaseModel):
    id: str
    company_id: str
    display_name: str = ""
    linkedin_email: str = ""
    browser_profile_name: str = ""
    status: str = "active"
    daily_connection_limit: int = 0
    daily_message_limit: int = 0
    connections_sent_today: int = 0
    messages_sent_today: int = 0
    cookies_updated_at: datetime | None = None
    last_login_at: datetime | None = None
    health: str = "unknown"
    created_at: datetime
    updated_at: datetime


class LinkedInJob(BaseModel):
    id: str
    candidate_id: str
    account_id: str
    job_type: str = ""
    status: str = "queued"
    priority: int = 0
    retry_count: int = 0
    max_retries: int = 0
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str = ""
    worker_id: str = ""
    created_at: datetime
    updated_at: datetime


class LinkedInConnection(BaseModel):
    id: str
    candidate_id: str
    account_id: str
    linkedin_url: str = ""
    connection_status: str = "unknown"
    request_sent_at: datetime | None = None
    accepted_at: datetime | None = None
    last_checked_at: datetime | None = None
    profile_snapshot_json: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class LinkedInConversation(BaseModel):
    id: str
    candidate_id: str
    account_id: str
    conversation_id: str = ""
    conversation_status: str = "unknown"
    last_message_at: datetime | None = None
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LinkedInMessage(BaseModel):
    id: str
    conversation_id: str
    candidate_id: str
    sender_type: str = "system"
    message_type: str = "text"
    message_text: str = ""
    linkedin_message_id: str = ""
    attachment_count: int = 0
    sent_at: datetime | None = None
    created_at: datetime


class LinkedInAttachment(BaseModel):
    id: str
    message_id: str
    candidate_id: str
    filename: str = ""
    mime_type: str = ""
    storage_path: str = ""
    download_status: str = "pending"
    downloaded_at: datetime | None = None
    created_at: datetime
