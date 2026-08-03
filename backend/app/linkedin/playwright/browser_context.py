from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.linkedin.config import (
    LINKEDIN_DEFAULT_TIMEOUT,
    LINKEDIN_DOWNLOAD_PATH,
    LINKEDIN_DEBUG,
    LINKEDIN_HEADLESS,
    LINKEDIN_PROFILE_ROOT,
    LINKEDIN_PROXY_SERVER,
    LINKEDIN_STEALTH_ENABLED,
    LINKEDIN_USER_AGENT,
    LINKEDIN_VIEWPORT_HEIGHT,
    LINKEDIN_VIEWPORT_WIDTH,
)


@dataclass(frozen=True)
class BrowserContextConfig:
    headless: bool = LINKEDIN_HEADLESS and not LINKEDIN_DEBUG
    profile_root: str = LINKEDIN_PROFILE_ROOT
    default_timeout: int = LINKEDIN_DEFAULT_TIMEOUT
    download_path: str = LINKEDIN_DOWNLOAD_PATH
    viewport_width: int = LINKEDIN_VIEWPORT_WIDTH
    viewport_height: int = LINKEDIN_VIEWPORT_HEIGHT
    user_agent: str = LINKEDIN_USER_AGENT
    proxy_server: str = LINKEDIN_PROXY_SERVER
    stealth_enabled: bool = LINKEDIN_STEALTH_ENABLED

    def resolved_download_path(self) -> Path:
        return Path(self.download_path).expanduser().resolve()
