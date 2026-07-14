from __future__ import annotations

from dataclasses import dataclass

from app.linkedin.playwright.profile_types import LinkedInProfileConnectionState
from app.linkedin.workers.connection_types import LinkedInConnectionWorkerStatus


@dataclass(frozen=True)
class LinkedInConnectionResult:
    status: LinkedInConnectionWorkerStatus
    previous_state: LinkedInProfileConnectionState
    current_state: LinkedInProfileConnectionState
    profile_url: str
    note_sent: bool
    timestamp: str
    duration_ms: int
    error_message: str = ""
