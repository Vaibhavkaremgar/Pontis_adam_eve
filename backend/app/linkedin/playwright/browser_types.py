from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BrowserSessionStatus(str, Enum):
    LOGGED_IN = "LOGGED_IN"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BrowserHealthStatus:
    running: bool
    connected: bool
    context_alive: bool

