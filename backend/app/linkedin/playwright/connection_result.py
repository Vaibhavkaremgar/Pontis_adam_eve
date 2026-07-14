from __future__ import annotations

from dataclasses import dataclass

from app.linkedin.playwright.profile_types import LinkedInProfileConnectionState


@dataclass(frozen=True)
class LinkedInConnectionResult:
    success: bool
    connection_state_before: LinkedInProfileConnectionState
    connection_state_after: LinkedInProfileConnectionState
    request_sent: bool
    request_timestamp: str = ""
    error: str = ""
    execution_time: float = 0.0
    profile_url: str = ""
