from __future__ import annotations

import logging
from typing import Any

from app.linkedin.playwright.browser_context import BrowserContextConfig
from app.linkedin.playwright.browser_exceptions import BrowserClosedError, BrowserLaunchError
from app.linkedin.playwright.browser_types import BrowserHealthStatus
from app.linkedin.playwright.playwright_factory import PlaywrightFactory

logger = logging.getLogger(__name__)

class BrowserManager:
    def __init__(self, account_id: str, config: BrowserContextConfig | None = None, profile_path: str | None = None) -> None:
        self.account_id = account_id
        self.profile_path = profile_path
        self.config = config or BrowserContextConfig()
        self.factory = PlaywrightFactory(self.config)
        self._playwright: Any = None
        self._browser_context: Any = None
        self._browser: Any = None

    async def _start_browser(self) -> Any:
        if self._browser_context is not None:
            return self._browser_context
        try:
            self._playwright = await self.factory.start_playwright()
            if self.profile_path:
                launch_config = self.factory.launch_config_for_profile(self.profile_path)
            else:
                from app.linkedin.profile_resolver import resolve_agency_profile_path
                self.profile_path = resolve_agency_profile_path(self.account_id)
                launch_config = self.factory.launch_config_for_profile(self.profile_path)
            self._browser_context = await self._playwright.chromium.launch_persistent_context(
                **launch_config,
                headless=self.config.headless,
            )
            if hasattr(self._browser_context, "set_default_timeout"):
                self._browser_context.set_default_timeout(self.config.default_timeout)
            if hasattr(self._browser_context, "set_default_navigation_timeout"):
                self._browser_context.set_default_navigation_timeout(self.config.default_timeout)
            self._browser = getattr(self._browser_context, "browser", None)
            await self._ensure_visible_page()
            logger.info("linkedin browser started account_id=%s", self.account_id)
            logger.info("linkedin profile loaded account_id=%s path=%s", self.account_id, launch_config["user_data_dir"])
            return self._browser_context
        except Exception as exc:
            logger.exception("linkedin browser startup failure account_id=%s", self.account_id)
            raise BrowserLaunchError("Failed to start LinkedIn browser") from exc

    async def get_browser(self) -> Any:
        return await self._start_browser()

    async def start(self) -> Any:
        return await self.get_browser()

    async def stop(self) -> None:
        try:
            if self._browser_context is not None:
                await self._browser_context.close()
            if self._playwright is not None:
                await self._playwright.stop()
            self._browser_context = None
            self._browser = None
            self._playwright = None
            logger.info("linkedin browser stopped account_id=%s", self.account_id)
        except Exception as exc:
            logger.exception("linkedin browser shutdown failure account_id=%s", self.account_id)
            raise BrowserClosedError("Failed to stop LinkedIn browser cleanly") from exc

    async def restart(self) -> Any:
        await self.stop()
        return await self.start()

    def browser_context(self) -> Any:
        if self._browser_context is None:
            raise BrowserClosedError("Browser context is not running")
        return self._browser_context

    def is_running(self) -> bool:
        return self._browser_context is not None

    def is_connected(self) -> bool:
        browser = self._browser or getattr(self._browser_context, "browser", None)
        return bool(browser and getattr(browser, "is_connected", lambda: False)())

    def is_context_alive(self) -> bool:
        return self._browser_context is not None and not getattr(self._browser_context, "is_closed", lambda: False)()

    async def _ensure_visible_page(self) -> None:
        if self._browser_context is None:
            return
        pages = []
        try:
            pages = list(getattr(self._browser_context, "pages", []) or [])
        except Exception:
            pages = []

        page = pages[0] if pages else None
        if page is None:
            page = await self._browser_context.new_page()
            logger.info("linkedin browser page created account_id=%s", self.account_id)

        if getattr(page, "url", "") in {"", "about:blank"}:
            try:
                await page.goto("https://www.linkedin.com/", wait_until="domcontentloaded", timeout=self.config.default_timeout)
            except Exception:
                logger.exception("linkedin browser page navigation failed account_id=%s", self.account_id)
