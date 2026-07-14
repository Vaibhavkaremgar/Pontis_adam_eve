from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.linkedin.playwright.browser_manager import BrowserManager
import app.linkedin.playwright.profile_inspector as profile_inspector_module
from app.linkedin.playwright.profile_inspector import LinkedInProfileInspector
from app.linkedin.playwright.profile_types import LinkedInProfileConnectionState
from app.linkedin.workers.connection_result import LinkedInConnectionResult
from app.linkedin.workers.connection_types import LinkedInConnectionWorkerStatus

logger = logging.getLogger(__name__)

class LinkedInConnectionWorker:
    def __init__(self, account_id: str, *, timeout_ms: int = 30000) -> None:
        self.account_id = account_id
        self.timeout_ms = timeout_ms
        self._browser_manager = BrowserManager(account_id=account_id)
        self._inspector: LinkedInProfileInspector | None = None
        self._context: Any | None = None

    async def run(self, linkedin_profile_url: str, connection_note: str | None = None) -> LinkedInConnectionResult:
        started_at = datetime.now(timezone.utc)
        note_text = (connection_note or "").strip()
        note_text = note_text[:300]
        note_sent = False
        previous_state = LinkedInProfileConnectionState.UNKNOWN
        current_state = LinkedInProfileConnectionState.UNKNOWN
        logger.info("linkedin connection worker started account_id=%s profile_url=%s", self.account_id, linkedin_profile_url)
        try:
            self._context = await self._browser_manager.start()
            logger.info("linkedin browser started account_id=%s", self.account_id)
            self._inspector = LinkedInProfileInspector(self._context, timeout_ms=self.timeout_ms)
            logger.info(
                "linkedin inspector module path=%s file=%s",
                getattr(LinkedInProfileInspector, "__module__", ""),
                getattr(profile_inspector_module, "__file__", ""),
            )

            before = await self._inspector.inspect(linkedin_profile_url)
            previous_state = before.connection_state
            current_state = before.connection_state
            logger.info("linkedin inspector returned state=%s profile_url=%s", current_state.value, linkedin_profile_url)

            if current_state == LinkedInProfileConnectionState.REQUEST_PENDING:
                return self._result(
                    status=LinkedInConnectionWorkerStatus.REQUEST_ALREADY_PENDING,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )
            if current_state == LinkedInProfileConnectionState.MESSAGE_AVAILABLE:
                return self._result(
                    status=LinkedInConnectionWorkerStatus.ALREADY_CONNECTED,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )
            if current_state in {LinkedInProfileConnectionState.FOLLOW_AVAILABLE, LinkedInProfileConnectionState.FOLLOW_ONLY}:
                return self._result(
                    status=LinkedInConnectionWorkerStatus.FOLLOW_ONLY,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )
            if current_state == LinkedInProfileConnectionState.LOGIN_REQUIRED:
                return self._result(
                    status=LinkedInConnectionWorkerStatus.LOGIN_REQUIRED,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )
            if current_state == LinkedInProfileConnectionState.PROFILE_NOT_FOUND:
                return self._result(
                    status=LinkedInConnectionWorkerStatus.PROFILE_NOT_FOUND,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )
            if current_state != LinkedInProfileConnectionState.CONNECT_AVAILABLE:
                return self._result(
                    status=LinkedInConnectionWorkerStatus.UNKNOWN_STATE,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )

            page = await self._context.new_page()
            try:
                if hasattr(page, "set_default_timeout"):
                    page.set_default_timeout(min(self.timeout_ms, 1000))
                if hasattr(page, "set_default_navigation_timeout"):
                    page.set_default_navigation_timeout(min(max(self.timeout_ms, 10000), 15000))

                await page.goto(linkedin_profile_url, wait_until="domcontentloaded", timeout=min(max(self.timeout_ms, 10000), 15000))
                logger.info("linkedin profile navigated profile_url=%s", linkedin_profile_url)
                await self._wait_for_profile_ready(page)

                connect_button = await self._find_visible_button(
                    page,
                    [
                        'button:has-text("Connect")',
                        'button[aria-label*="Connect"]',
                    ],
                )
                if connect_button is None:
                    connect_button = await self._find_role_button(page, "Connect")
                if connect_button is None:
                    return self._result(
                        status=LinkedInConnectionWorkerStatus.UNKNOWN_RESULT,
                        previous_state=previous_state,
                        current_state=current_state,
                        profile_url=linkedin_profile_url,
                        note_sent=False,
                        started_at=started_at,
                        error_message="Connect button not found",
                    )

                await connect_button.click(timeout=self.timeout_ms)
                logger.info("linkedin connect clicked profile_url=%s", linkedin_profile_url)
                await self._wait_for_any_text(page, ["Add a note", "Send invitation", "Send"])

                if note_text:
                    add_note_button = await self._find_visible_button(page, ['button:has-text("Add a note")', 'button[aria-label*="Add a note"]'])
                    if add_note_button is not None:
                        await add_note_button.click(timeout=self.timeout_ms)
                        logger.info("linkedin add note clicked profile_url=%s", linkedin_profile_url)
                    text_area = await self._find_note_textarea(page)
                    if text_area is not None:
                        await text_area.fill(note_text, timeout=self.timeout_ms)
                        actual = await self._read_value(text_area)
                        if actual.strip() != note_text:
                            raise RuntimeError("Inserted note did not match expected value")
                        note_sent = True
                        logger.info("linkedin note inserted profile_url=%s chars=%s", linkedin_profile_url, len(note_text))

                send_button = await self._find_visible_button(page, ['button:has-text("Send")', 'button[aria-label*="Send"]'])
                if send_button is None:
                    return self._result(
                        status=LinkedInConnectionWorkerStatus.UNKNOWN_RESULT,
                        previous_state=previous_state,
                        current_state=current_state,
                        profile_url=linkedin_profile_url,
                        note_sent=note_sent,
                        started_at=started_at,
                        error_message="Send button not found",
                    )
                await send_button.click(timeout=self.timeout_ms)
                logger.info("linkedin send clicked profile_url=%s", linkedin_profile_url)

                await self._wait_for_any_text(page, ["Pending", "Invitation sent", "Pending"])
                after = await self._inspector.inspect(linkedin_profile_url)
                logger.info("linkedin verification inspector state=%s profile_url=%s", after.connection_state.value, linkedin_profile_url)

                if after.connection_state == LinkedInProfileConnectionState.REQUEST_PENDING:
                    return self._result(
                        status=LinkedInConnectionWorkerStatus.REQUEST_SENT,
                        previous_state=previous_state,
                        current_state=after.connection_state,
                        profile_url=linkedin_profile_url,
                        note_sent=note_sent,
                        started_at=started_at,
                    )
                return self._result(
                    status=LinkedInConnectionWorkerStatus.UNKNOWN_RESULT,
                    previous_state=previous_state,
                    current_state=after.connection_state,
                    profile_url=linkedin_profile_url,
                    note_sent=note_sent,
                    started_at=started_at,
                    error_message="Verification did not confirm REQUEST_PENDING",
                )
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
        except Exception as exc:
            logger.exception("linkedin connection worker failed profile_url=%s", linkedin_profile_url)
            return self._result(
                status=LinkedInConnectionWorkerStatus.FAILED,
                previous_state=previous_state,
                current_state=current_state,
                profile_url=linkedin_profile_url,
                note_sent=note_sent,
                started_at=started_at,
                error_message=str(exc),
            )
        finally:
            logger.info("linkedin connection total duration profile_url=%s duration_ms=%s", linkedin_profile_url, self._duration_ms(started_at))
            try:
                await self._browser_manager.stop()
            except Exception:
                logger.debug("linkedin browser stop failed", exc_info=True)

    async def _wait_for_profile_ready(self, page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1000)
        except Exception:
            pass

    async def _wait_for_any_text(self, page: Any, texts: list[str]) -> None:
        for text in texts:
            try:
                await page.get_by_text(text, exact=False).first.wait_for(timeout=1000)
                return
            except Exception:
                continue

    async def _find_visible_button(self, page: Any, selectors: list[str]) -> Any | None:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                for index in range(min(count, 10)):
                    item = locator.nth(index)
                    if await item.is_visible():
                        return item
            except Exception:
                continue
        return None

    async def _find_role_button(self, page: Any, name: str) -> Any | None:
        try:
            locator = page.get_by_role("button", name=re.compile(name, re.I))
            count = await locator.count()
            for index in range(min(count, 10)):
                item = locator.nth(index)
                if await item.is_visible():
                    return item
        except Exception:
            return None
        return None

    async def _find_note_textarea(self, page: Any) -> Any | None:
        selectors = ["textarea", "[contenteditable='true']", "input[type='text']"]
        return await self._find_visible_button(page, selectors)

    async def _read_value(self, locator: Any) -> str:
        for method_name in ("input_value", "inner_text", "text_content"):
            try:
                value = await getattr(locator, method_name)(timeout=self.timeout_ms)
                text = str(value or "").strip()
                if text:
                    return text
            except Exception:
                continue
        try:
            return str(await locator.get_attribute("value") or "").strip()
        except Exception:
            return ""

    def _result(
        self,
        *,
        status: LinkedInConnectionWorkerStatus,
        previous_state: LinkedInProfileConnectionState,
        current_state: LinkedInProfileConnectionState,
        profile_url: str,
        note_sent: bool,
        started_at: datetime,
        error_message: str = "",
    ) -> LinkedInConnectionResult:
        return LinkedInConnectionResult(
            status=status,
            previous_state=previous_state,
            current_state=current_state,
            profile_url=profile_url,
            note_sent=note_sent,
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_ms=self._duration_ms(started_at),
            error_message=error_message,
        )

    def _duration_ms(self, started_at: datetime) -> int:
        return int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
