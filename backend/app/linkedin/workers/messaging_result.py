from __future__ import annotations

from dataclasses import dataclass

from app.linkedin.playwright.profile_types import LinkedInProfileConnectionState
from app.linkedin.workers.messaging_types import LinkedInMessagingWorkerStatus


@dataclass(frozen=True)
class LinkedInMessagingResult:
    status: LinkedInMessagingWorkerStatus
    connection_state: LinkedInProfileConnectionState
    profile_url: str
    timestamp: str
    duration_ms: int
    screenshot_path: str = ""
    html_path: str = ""
    json_path: str = ""
    error_message: str = ""
    message_text: str = ""
    compose_selector: str = ""
    send_selector: str = ""
    verification_method: str = ""
