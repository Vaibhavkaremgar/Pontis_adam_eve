from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LinkedInProfileConnectionState(str, Enum):
    CONNECT_AVAILABLE = "CONNECT_AVAILABLE"
    REQUEST_PENDING = "REQUEST_PENDING"
    ALREADY_CONNECTED = "ALREADY_CONNECTED"
    FOLLOW_ONLY = "FOLLOW_ONLY"
    MESSAGE_AVAILABLE = "MESSAGE_AVAILABLE"
    FOLLOW_AVAILABLE = "FOLLOW_AVAILABLE"
    CONNECTED = "CONNECTED"
    PRIVATE_PROFILE = "PRIVATE_PROFILE"
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    ACCOUNT_RESTRICTED = "ACCOUNT_RESTRICTED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    UNKNOWN = "UNKNOWN"


class LinkedInAvailableAction(str, Enum):
    CONNECT = "CONNECT"
    MESSAGE = "MESSAGE"
    FOLLOW = "FOLLOW"
    MORE = "MORE"
    NONE = "NONE"


@dataclass(frozen=True)
class ProfileCapabilities:
    """Capability model — what actions LinkedIn is actually offering on this profile.

    These are independent boolean flags read directly from the toolbar.
    They are NOT inferred from each other.

    connected=True only when there is explicit evidence:
      - "Remove connection" label
      - "1st" degree badge in body text
      - "Connected" badge in body text
    Never set connected=True merely because can_message=True.
    """
    can_connect: bool = False
    can_message: bool = False
    can_follow: bool = False
    has_more: bool = False
    pending: bool = False
    connected: bool = False
    connection_verified: bool = False   # True only when explicit evidence found
    login_required: bool = False
    session_expired: bool = False
    profile_not_found: bool = False
    profile_private: bool = False
    raw_labels: list[str] = field(default_factory=list)

    def log_summary(self, profile_url: str, logger: object) -> None:  # type: ignore[type-arg]
        import logging
        _log = logger if isinstance(logger, logging.Logger) else logging.getLogger(__name__)
        _log.info(
            "capabilities profile_url=%s "
            "can_connect=%s can_message=%s can_follow=%s has_more=%s "
            "pending=%s connected=%s connection_verified=%s "
            "login_required=%s session_expired=%s "
            "profile_not_found=%s profile_private=%s labels=%s",
            profile_url,
            self.can_connect, self.can_message, self.can_follow, self.has_more,
            self.pending, self.connected, self.connection_verified,
            self.login_required, self.session_expired,
            self.profile_not_found, self.profile_private,
            self.raw_labels,
        )


@dataclass(frozen=True)
class LinkedInProfileInspectionResult:
    profile_url: str
    profile_name: str = ""
    current_title: str = ""
    company: str = ""
    page_loaded: bool = False
    profile_exists: bool = False
    profile_private: bool = False
    login_required: bool = False
    connection_state: LinkedInProfileConnectionState = LinkedInProfileConnectionState.UNKNOWN
    available_actions: list[LinkedInAvailableAction] = field(default_factory=list)
    inspection_timestamp: str = ""
    raw_button_labels: list[str] = field(default_factory=list)
    page_url: str = ""
    error: str = ""
    capabilities: ProfileCapabilities | None = None
