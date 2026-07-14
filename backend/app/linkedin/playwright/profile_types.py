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
