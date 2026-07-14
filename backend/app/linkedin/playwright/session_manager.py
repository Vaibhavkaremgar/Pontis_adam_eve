from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.linkedin.playwright.browser_types import BrowserSessionStatus

logger = logging.getLogger(__name__)

@dataclass
class SessionManager:
    browser_context: object

    async def detect_session_status(self) -> BrowserSessionStatus:
        page: Any | None = None
        try:
            page = await self.browser_context.new_page()
            try:
                await page.goto("https://www.linkedin.com/", wait_until="domcontentloaded")
            except Exception:
                pass

            current_url = str(getattr(page, "url", "") or "").lower()
            title = await self._safe_title(page)
            body_text = await self._safe_body_text(page)
            locator = page.locator("body")
            has_login_form = await self._has_login_form(page)
            has_authenticated_nav = await self._has_authenticated_nav(page)
            has_feed_container = await self._has_feed_container(page)
            has_search_bar = await self._has_search_bar(page)
            has_profile_avatar = await self._has_profile_avatar(page)
            has_checkpoint = any(token in f"{current_url} {title} {body_text}".lower() for token in ("checkpoint", "challenge"))
            has_login_page = any(
                token in f"{current_url} {title} {body_text}".lower()
                for token in ("linkedin: log in or sign up", "/login", "/uas/login", "sign in", "log in")
            ) or has_login_form

            if has_checkpoint:
                status = BrowserSessionStatus.SESSION_EXPIRED
            elif has_login_page:
                status = BrowserSessionStatus.LOGIN_REQUIRED
            elif has_authenticated_nav or has_feed_container or has_search_bar or has_profile_avatar:
                status = BrowserSessionStatus.LOGGED_IN
            else:
                # Keep the result conservative when the page is ambiguous.
                status = BrowserSessionStatus.UNKNOWN

            logger.info(
                "linkedin session status=%s url=%s has_login_form=%s has_authenticated_nav=%s has_feed_container=%s has_search_bar=%s has_profile_avatar=%s",
                status.value,
                current_url,
                has_login_form,
                has_authenticated_nav,
                has_feed_container,
                has_search_bar,
                has_profile_avatar,
            )
            return status
        except Exception:
            logger.info("linkedin session status=%s", BrowserSessionStatus.UNKNOWN.value)
            return BrowserSessionStatus.UNKNOWN
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _safe_title(self, page: Any) -> str:
        try:
            return str(await page.title() or "").lower()
        except Exception:
            return ""

    async def _safe_body_text(self, page: Any) -> str:
        try:
            return str(await page.locator("body").inner_text(timeout=5000) or "").lower()
        except Exception:
            return ""

    async def _has_login_form(self, page: Any) -> bool:
        selectors = [
            "input[name='session_key']",
            "input[name='session_password']",
            "form[action*='/uas/login']",
            "button[type='submit']",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _has_authenticated_nav(self, page: Any) -> bool:
        selectors = [
            "[aria-label='Home']",
            "[aria-label*='Me']",
            "[aria-label*='Profile']",
            "[data-control-name='nav.settings']",
            ".global-nav",
        ]
        return await self._any_visible(page, selectors)

    async def _has_feed_container(self, page: Any) -> bool:
        selectors = [
            "[data-test-global-nav]",
            ".scaffold-finite-scroll__content",
            "[role='main'] main",
            ".feed-identity-module",
        ]
        return await self._any_visible(page, selectors)

    async def _has_search_bar(self, page: Any) -> bool:
        selectors = [
            "input[placeholder*='Search']",
            "input[aria-label*='Search']",
            "[aria-label*='Search']",
        ]
        return await self._any_visible(page, selectors)

    async def _has_profile_avatar(self, page: Any) -> bool:
        selectors = [
            "[data-control-name='nav.settings'] img",
            "[aria-label*='profile'] img",
            "[alt*='profile']",
            "[aria-label*='Me'] img",
        ]
        return await self._any_visible(page, selectors)

    async def _any_visible(self, page: Any, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible():
                    return True
            except Exception:
                continue
        return False
