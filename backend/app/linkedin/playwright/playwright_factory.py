from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.linkedin.playwright.browser_context import BrowserContextConfig
from app.linkedin.playwright.browser_exceptions import ConfigurationError

logger = logging.getLogger(__name__)


class PlaywrightFactory:
    def __init__(self, config: BrowserContextConfig | None = None) -> None:
        self.config = config or BrowserContextConfig()

    def _build_launch_args(self) -> dict[str, Any]:
        downloads_path = self.config.resolved_download_path()
        downloads_path.mkdir(parents=True, exist_ok=True)

        viewport = {"width": self.config.viewport_width, "height": self.config.viewport_height}
        context_kwargs: dict[str, Any] = {
            "accept_downloads": True,
            "viewport": viewport,
            "downloads_path": str(downloads_path),
        }
        if self.config.user_agent:
            context_kwargs["user_agent"] = self.config.user_agent
        if self.config.proxy_server:
            context_kwargs["proxy"] = {"server": self.config.proxy_server}
        return context_kwargs

    async def start_playwright(self):
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - import failure depends on env
            raise ConfigurationError("Playwright is not installed or unavailable") from exc
        return await async_playwright().start()

    def profile_directory(self, account_id: str) -> Path:
        if not self.config.profile_root:
            logger.error("linkedin profile root missing account_id=%s", account_id)
            raise ConfigurationError("LINKEDIN_PROFILE_ROOT is required")
        profile_root = Path(self.config.profile_root).expanduser().resolve()
        if not profile_root.exists():
            profile_root.mkdir(parents=True, exist_ok=True)
            logger.info("Created profile directory path=%s", profile_root)
        elif not profile_root.is_dir():
            logger.error("linkedin profile root is not a directory path=%s", profile_root)
            raise ConfigurationError("LINKEDIN_PROFILE_ROOT must point to a directory")

        profile_dir = profile_root / account_id
        if not profile_dir.exists():
            profile_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created profile directory path=%s", profile_dir)
        elif not profile_dir.is_dir():
            logger.error("linkedin profile directory is not a directory path=%s", profile_dir)
            raise ConfigurationError("LinkedIn account profile path must be a directory")
        logger.info("Loaded profile directory path=%s", profile_dir)
        return profile_dir

    def launch_config(self, account_id: str) -> dict[str, Any]:
        profile_dir = self.profile_directory(account_id)
        cfg = self._build_launch_args()
        cfg["user_data_dir"] = str(profile_dir)
        return cfg
