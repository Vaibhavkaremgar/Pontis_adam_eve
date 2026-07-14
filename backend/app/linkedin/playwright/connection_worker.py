from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from app.linkedin.playwright.browser_exceptions import SessionExpiredError
from app.linkedin.playwright.connection_result import LinkedInConnectionResult
from app.linkedin.playwright.profile_inspector import LinkedInProfileInspector
from app.linkedin.playwright.profile_types import LinkedInAvailableAction, LinkedInProfileConnectionState
from app.linkedin.playwright.session_manager import SessionManager

logger = logging.getLogger(__name__)


class LinkedInConnectionWorker:
    def __init__(self, browser_context: Any, timeout_ms: int = 30000) -> None:
        self.browser_context = browser_context
        self.timeout_ms = timeout_ms
        self.inspector = LinkedInProfileInspector(browser_context, timeout_ms=timeout_ms)
        self.session_manager = SessionManager(browser_context)

    async def send_connection_request(self, profile_url: str) -> LinkedInConnectionResult:
        started_at = datetime.now(timezone.utc)
        logger.info("linkedin connection navigation started profile_url=%s", profile_url)
        page = None
        try:
            session_status = await self.session_manager.detect_session_status()
            if session_status is session_status.LOGIN_REQUIRED:
                raise SessionExpiredError("LinkedIn session requires login")
            if session_status is session_status.SESSION_EXPIRED:
                raise SessionExpiredError("LinkedIn session is expired")

            before = await self.inspector.inspect(profile_url)
            logger.info("linkedin profile inspected profile_url=%s state=%s", profile_url, before.connection_state.value)
            if before.connection_state != LinkedInProfileConnectionState.CONNECT_AVAILABLE:
                return self._failure(profile_url, before.connection_state, before.connection_state, "Connection is not available", started_at)

            page = await self.browser_context.new_page()
            if hasattr(page, "set_default_timeout"):
                page.set_default_timeout(self.timeout_ms)
            if hasattr(page, "set_default_navigation_timeout"):
                page.set_default_navigation_timeout(self.timeout_ms)

            await page.goto(profile_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            await self._wait_for_settle(page)

            connect_button = await self._find_button(page, ["connect", "connect button"])
            if connect_button is None:
                return self._failure(profile_url, before.connection_state, LinkedInProfileConnectionState.UNKNOWN, "Connect button missing", started_at)

            await connect_button.click(timeout=self.timeout_ms)
            logger.info("linkedin connect clicked profile_url=%s", profile_url)
            await self._wait_for_dialog(page)

            send_button = await self._find_dialog_button(page, ["send", "send invitation", "send now"])
            if send_button is None:
                return self._failure(profile_url, before.connection_state, LinkedInProfileConnectionState.UNKNOWN, "Send button missing", started_at)

            await send_button.click(timeout=self.timeout_ms)
            logger.info("linkedin send clicked profile_url=%s", profile_url)
            request_timestamp = datetime.now(timezone.utc).isoformat()

            after = await self.inspector.inspect(profile_url)
            logger.info("linkedin verification completed profile_url=%s state=%s", profile_url, after.connection_state.value)
            if after.connection_state != LinkedInProfileConnectionState.REQUEST_PENDING:
                return self._failure(
                    profile_url,
                    before.connection_state,
                    after.connection_state,
                    "Connection request was not verified as pending",
                    started_at,
                    request_sent=True,
                    request_timestamp=request_timestamp,
                )

            return LinkedInConnectionResult(
                success=True,
                connection_state_before=before.connection_state,
                connection_state_after=after.connection_state,
                request_sent=True,
                request_timestamp=request_timestamp,
                execution_time=self._duration_seconds(started_at),
                profile_url=profile_url,
            )
        except SessionExpiredError as exc:
            logger.info("linkedin connection session expired profile_url=%s", profile_url)
            return LinkedInConnectionResult(
                success=False,
                connection_state_before=LinkedInProfileConnectionState.LOGIN_REQUIRED,
                connection_state_after=LinkedInProfileConnectionState.LOGIN_REQUIRED,
                request_sent=False,
                error=str(exc),
                execution_time=self._duration_seconds(started_at),
                profile_url=profile_url,
            )
        except Exception as exc:
            logger.exception("linkedin connection request failed profile_url=%s", profile_url)
            return LinkedInConnectionResult(
                success=False,
                connection_state_before=LinkedInProfileConnectionState.UNKNOWN,
                connection_state_after=LinkedInProfileConnectionState.UNKNOWN,
                request_sent=False,
                error=str(exc),
                execution_time=self._duration_seconds(started_at),
                profile_url=profile_url,
            )
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            logger.info("linkedin connection duration profile_url=%s execution_time=%s", profile_url, self._duration_seconds(started_at))

    async def _wait_for_settle(self, page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=self.timeout_ms // 2)
        except Exception:
            pass

    async def _wait_for_dialog(self, page: Any) -> None:
        selectors = ["[role='dialog']", ".artdeco-modal", ".modal", "div[aria-modal='true']"]
        for selector in selectors:
            try:
                await page.wait_for_selector(selector, timeout=self.timeout_ms // 2)
                return
            except Exception:
                continue

    async def _find_button(self, page: Any, labels: Iterable[str]) -> Any:
        return await self._find_clickable_by_labels(page, labels, scope=None)

    async def _find_dialog_button(self, page: Any, labels: Iterable[str]) -> Any:
        scope = page.locator("[role='dialog'], .artdeco-modal, .modal, div[aria-modal='true']").first
        try:
            if await scope.is_visible():
                return await self._find_clickable_by_labels(page, labels, scope=scope)
        except Exception:
            pass
        return await self._find_clickable_by_labels(page, labels, scope=None)

    async def _find_clickable_by_labels(self, page: Any, labels: Iterable[str], scope: Any | None) -> Any:
        normalized = [str(label).strip().lower() for label in labels if str(label).strip()]
        locators = [
            "button",
            "[role='button']",
            "[aria-label]",
            "[data-test-button]",
        ]
        roots = [scope] if scope is not None else [page]
        for root in roots:
            for selector in locators:
                try:
                    locator = root.locator(selector)
                    count = await locator.count()
                    for index in range(min(count, 20)):
                        item = locator.nth(index)
                        if not await self._is_visible(item):
                            continue
                        text = await self._read_label(item)
                        lowered = text.lower()
                        if any(self._label_matches(lowered, label) for label in normalized):
                            return item
                except Exception:
                    continue
        return None

    async def _is_visible(self, locator: Any) -> bool:
        try:
            return bool(await locator.is_visible())
        except Exception:
            return False

    async def _read_label(self, locator: Any) -> str:
        for method_name in ("inner_text", "text_content"):
            try:
                value = await getattr(locator, method_name)(timeout=self.timeout_ms // 2)
                text = str(value or "").strip()
                if text:
                    return text
            except Exception:
                continue
        try:
            label = str(await locator.get_attribute("aria-label") or "").strip()
            if label:
                return label
        except Exception:
            pass
        return ""

    def _label_matches(self, text: str, label: str) -> bool:
        if text == label:
            return True
        if label in text:
            return True
        return text.startswith(label)

    def _failure(
        self,
        profile_url: str,
        before: LinkedInProfileConnectionState,
        after: LinkedInProfileConnectionState,
        error: str,
        started_at: datetime,
        *,
        request_sent: bool = False,
        request_timestamp: str = "",
    ) -> LinkedInConnectionResult:
        return LinkedInConnectionResult(
            success=False,
            connection_state_before=before,
            connection_state_after=after,
            request_sent=request_sent,
            request_timestamp=request_timestamp,
            error=error,
            execution_time=self._duration_seconds(started_at),
            profile_url=profile_url,
        )

    def _duration_seconds(self, started_at: datetime) -> float:
        return round((datetime.now(timezone.utc) - started_at).total_seconds(), 3)
