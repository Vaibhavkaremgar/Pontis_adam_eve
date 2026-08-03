from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.linkedin.playwright.browser_manager import BrowserManager
import app.linkedin.playwright.profile_inspector as profile_inspector_module
from app.linkedin.playwright.action_discovery import find_connect_action
from app.linkedin.playwright.human_interaction import human_click
from app.linkedin.playwright.profile_inspector import LinkedInProfileInspector
from app.linkedin.playwright.profile_types import LinkedInProfileConnectionState, ProfileCapabilities
from app.linkedin.workers.connection_result import LinkedInConnectionResult
from app.linkedin.workers.connection_types import LinkedInConnectionWorkerStatus

logger = logging.getLogger(__name__)


def _resolve_account_uuid(browser_profile_name: str) -> str | None:
    """Return the linkedin_accounts.id UUID for the given browser_profile_name, or None."""
    try:
        from app.db.session import SessionLocal
        from app.linkedin.models import LinkedInAccountEntity

        db = SessionLocal()
        try:
            row = (
                db.query(LinkedInAccountEntity)
                .filter(LinkedInAccountEntity.browser_profile_name == browser_profile_name)
                .first()
            )
            if row is None:
                logger.error(
                    "linkedin account not found for browser_profile_name=%s — persistence skipped",
                    browser_profile_name,
                )
                return None
            return str(row.id)
        finally:
            db.close()
    except Exception:
        logger.exception(
            "linkedin account uuid resolution failed browser_profile_name=%s",
            browser_profile_name,
        )
        return None


def _persist_connection(
    *,
    candidate_id: str,
    account_id: str,
    linkedin_url: str,
    connection_status: str,
    request_sent_at: datetime | None,
    profile_snapshot: dict,
) -> None:
    """Write or update the linkedin_connections row for this candidate."""
    try:
        from app.db.session import SessionLocal
        from app.linkedin.models import LinkedInConnectionEntity
        from app.linkedin.repository import LinkedInConnectionRepository

        with SessionLocal() as db:
            repo = LinkedInConnectionRepository(db)
            existing = (
                db.query(LinkedInConnectionEntity)
                .filter(
                    LinkedInConnectionEntity.candidate_id == candidate_id,
                    LinkedInConnectionEntity.account_id == account_id,
                )
                .first()
            )
            now = datetime.now(timezone.utc)
            if existing is not None:
                existing.linkedin_url = linkedin_url or existing.linkedin_url
                existing.connection_status = connection_status
                existing.request_sent_at = request_sent_at or existing.request_sent_at
                existing.last_checked_at = now
                existing.profile_snapshot_json = profile_snapshot or existing.profile_snapshot_json
                existing.updated_at = now
                db.flush()
            else:
                entity = LinkedInConnectionEntity(
                    id=str(uuid4()),
                    candidate_id=candidate_id,
                    account_id=account_id,
                    linkedin_url=linkedin_url,
                    connection_status=connection_status,
                    request_sent_at=request_sent_at,
                    last_checked_at=now,
                    profile_snapshot_json=profile_snapshot,
                    created_at=now,
                    updated_at=now,
                )
                repo.create(entity)
            db.commit()
            logger.info(
                "linkedin connection persisted candidate_id=%s account_id=%s status=%s",
                candidate_id,
                account_id,
                connection_status,
            )
    except Exception:
        logger.exception(
            "linkedin connection persistence failed candidate_id=%s account_id=%s",
            candidate_id,
            account_id,
        )


class LinkedInConnectionWorker:
    def __init__(self, account_id: str, *, timeout_ms: int = 30000) -> None:
        self.account_id = account_id
        self.timeout_ms = timeout_ms
        self._browser_manager = BrowserManager(account_id=account_id)
        self._inspector: LinkedInProfileInspector | None = None
        self._context: Any | None = None

    async def run(
        self,
        linkedin_profile_url: str,
        connection_note: str | None = None,
        *,
        candidate_id: str = "",
    ) -> LinkedInConnectionResult:
        started_at = datetime.now(timezone.utc)
        note_text = (connection_note or "").strip()[:300]
        note_sent = False
        previous_state = LinkedInProfileConnectionState.UNKNOWN
        current_state = LinkedInProfileConnectionState.UNKNOWN
        logger.info("linkedin connection worker started account_id=%s profile_url=%s", self.account_id, linkedin_profile_url)
        try:
            self._context = await self._browser_manager.get_browser()
            logger.info("linkedin browser started account_id=%s", self.account_id)
            self._inspector = LinkedInProfileInspector(self._context, timeout_ms=self.timeout_ms)
            logger.info(
                "linkedin inspector module path=%s file=%s",
                getattr(LinkedInProfileInspector, "__module__", ""),
                getattr(profile_inspector_module, "__file__", ""),
            )

            # --- Use capability model instead of collapsed state enum ---
            caps: ProfileCapabilities = await self._inspector.inspect_capabilities(linkedin_profile_url)
            caps.log_summary(linkedin_profile_url, logger)

            # Map capabilities to legacy state for result objects (backward compat)
            if caps.login_required:
                current_state = LinkedInProfileConnectionState.LOGIN_REQUIRED
            elif caps.session_expired:
                current_state = LinkedInProfileConnectionState.SESSION_EXPIRED
            elif caps.profile_not_found:
                current_state = LinkedInProfileConnectionState.PROFILE_NOT_FOUND
            elif caps.profile_private:
                current_state = LinkedInProfileConnectionState.PRIVATE_PROFILE
            elif caps.pending:
                current_state = LinkedInProfileConnectionState.REQUEST_PENDING
            elif caps.connected:
                current_state = LinkedInProfileConnectionState.CONNECTED
            elif caps.can_connect:
                current_state = LinkedInProfileConnectionState.CONNECT_AVAILABLE
            elif caps.can_message:
                current_state = LinkedInProfileConnectionState.MESSAGE_AVAILABLE
            elif caps.can_follow:
                current_state = LinkedInProfileConnectionState.FOLLOW_AVAILABLE
            else:
                current_state = LinkedInProfileConnectionState.UNKNOWN
            previous_state = current_state

            logger.info(
                "connection_worker capabilities profile_url=%s "
                "can_connect=%s can_message=%s pending=%s connected=%s connection_verified=%s",
                linkedin_profile_url,
                caps.can_connect, caps.can_message,
                caps.pending, caps.connected, caps.connection_verified,
            )

            # --- Decision tree based on capabilities, NOT inferred state ---

            if caps.login_required or caps.session_expired:
                return self._result(
                    status=LinkedInConnectionWorkerStatus.LOGIN_REQUIRED,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )

            if caps.profile_not_found:
                return self._result(
                    status=LinkedInConnectionWorkerStatus.PROFILE_NOT_FOUND,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )

            if caps.pending:
                # Already sent a request — do not re-send.
                return self._result(
                    status=LinkedInConnectionWorkerStatus.REQUEST_ALREADY_PENDING,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )

            if caps.connected:
                # Explicitly connected — do not send another request.
                return self._result(
                    status=LinkedInConnectionWorkerStatus.ALREADY_CONNECTED,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )

            if not caps.can_connect:
                # No Connect button visible — follow-only, private, or unknown.
                if caps.can_follow and not caps.can_message:
                    return self._result(
                        status=LinkedInConnectionWorkerStatus.FOLLOW_ONLY,
                        previous_state=previous_state,
                        current_state=current_state,
                        profile_url=linkedin_profile_url,
                        note_sent=False,
                        started_at=started_at,
                    )
                return self._result(
                    status=LinkedInConnectionWorkerStatus.UNKNOWN_STATE,
                    previous_state=previous_state,
                    current_state=current_state,
                    profile_url=linkedin_profile_url,
                    note_sent=False,
                    started_at=started_at,
                )

            # --- can_connect=True: proceed to send the request ---
            page = await self._context.new_page()
            try:
                if hasattr(page, "set_default_timeout"):
                    page.set_default_timeout(self.timeout_ms)
                if hasattr(page, "set_default_navigation_timeout"):
                    page.set_default_navigation_timeout(min(max(self.timeout_ms, 10000), 15000))

                await page.goto(linkedin_profile_url, wait_until="domcontentloaded", timeout=min(max(self.timeout_ms, 10000), 15000))
                logger.info("linkedin profile navigated profile_url=%s", linkedin_profile_url)
                await self._wait_for_profile_ready(page)
                await self._close_page_messaging_bubbles(page)

                connect_button = await find_connect_action(page)
                if connect_button is None:
                    return self._result(
                        status=LinkedInConnectionWorkerStatus.UNKNOWN_RESULT,
                        previous_state=previous_state,
                        current_state=current_state,
                        profile_url=linkedin_profile_url,
                        note_sent=False,
                        started_at=started_at,
                        error_message="Connect button not found on live page",
                    )

                await self._click_connect_button(connect_button, linkedin_profile_url)
                logger.info("linkedin connect clicked profile_url=%s", linkedin_profile_url)
                await self._close_page_messaging_bubbles(page)
                await self._wait_for_any_text(page, ["Add a note", "Send invitation", "Send"])
                await self._dump_connect_dialog(page, linkedin_profile_url)

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

                send_button = await self._find_dialog_send_button(page)
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

                confirmed = await self._wait_for_pending_state(page)
                logger.info("linkedin pending state confirmed=%s profile_url=%s", confirmed, linkedin_profile_url)

                # Verify with capability model
                after_caps = await self._inspector.inspect_capabilities(linkedin_profile_url)
                after_caps.log_summary(linkedin_profile_url, logger)
                logger.info("linkedin verification caps_pending=%s profile_url=%s", after_caps.pending, linkedin_profile_url)

                if after_caps.pending:
                    return self._result(
                        status=LinkedInConnectionWorkerStatus.REQUEST_SENT,
                        previous_state=previous_state,
                        current_state=LinkedInProfileConnectionState.REQUEST_PENDING,
                        profile_url=linkedin_profile_url,
                        note_sent=note_sent,
                        started_at=started_at,
                    )

                return self._result(
                    status=LinkedInConnectionWorkerStatus.UNKNOWN_RESULT,
                    previous_state=previous_state,
                    current_state=current_state,
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

    async def _close_page_messaging_bubbles(self, page: Any) -> None:
        from app.linkedin.playwright.action_discovery import close_messaging_overlays
        await close_messaging_overlays(page)

    async def _click_connect_button(self, connect_button: Any, profile_url: str) -> None:
        """Click the Connect button, handling both <button> and <a> variants.

        LinkedIn renders Connect as:
          - <button>  — normal profiles: normal click → force fallback
          - <a href="/preload/custom-invite/...">  — premium/custom invite flow:
            JS .click() fires the React handler without navigating away.
            force=True on an anchor navigates the page, so we skip it.
        """
        from app.linkedin.playwright.human_interaction import human_scroll, human_hover, wait_after_action
        await human_scroll(connect_button)
        await human_hover(connect_button)
        await wait_after_action(min_ms=150, max_ms=350)

        # Detect element tag to choose the right click strategy
        try:
            tag = str(await connect_button.evaluate("el => el.tagName.toLowerCase()") or "").strip()
        except Exception:
            tag = ""
        logger.info("connection_worker connect_button tag=%r profile_url=%s", tag, profile_url)

        if tag == "a":
            # Anchor: JS click fires React handler without navigating
            try:
                await connect_button.evaluate("(el) => el.click()")
                logger.info("connection_worker connect_click strategy=js_click(anchor) profile_url=%s", profile_url)
                await wait_after_action()
                return
            except Exception as exc:
                logger.info("connection_worker connect_click strategy=js_click(anchor) FAIL reason=%r", str(exc))
            # Last resort: dispatch a synthetic click event
            try:
                await connect_button.dispatch_event("click")
                logger.info("connection_worker connect_click strategy=dispatch_event profile_url=%s", profile_url)
                await wait_after_action()
                return
            except Exception as exc:
                logger.info("connection_worker connect_click strategy=dispatch_event FAIL reason=%r", str(exc))
            return

        # Button: normal click → force fallback → JS fallback
        try:
            await connect_button.click(timeout=8000)
            logger.info("connection_worker connect_click strategy=normal profile_url=%s", profile_url)
            await wait_after_action()
            return
        except Exception as exc:
            logger.info("connection_worker connect_click strategy=normal FAIL reason=%r — retrying with force=True", str(exc))
        try:
            await connect_button.click(force=True, timeout=5000)
            logger.info("connection_worker connect_click strategy=force profile_url=%s", profile_url)
            await wait_after_action()
            return
        except Exception as exc:
            logger.info("connection_worker connect_click strategy=force FAIL reason=%r — falling back to js_click", str(exc))
        await connect_button.evaluate("(el) => el.click()")
        logger.info("connection_worker connect_click strategy=js_click profile_url=%s", profile_url)
        await wait_after_action()

    async def _wait_for_profile_ready(self, page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=6000)
            logger.info("connection_worker networkidle_reached")
        except Exception:
            logger.info("connection_worker networkidle_timeout — proceeding")
        try:
            await page.wait_for_selector(
                "[role='toolbar'], section[componentkey*='Topcard']",
                timeout=10000,
            )
            logger.info("connection_worker topcard_ready")
        except Exception:
            logger.info("connection_worker topcard_wait_timeout — proceeding")

    async def _wait_for_any_text(self, page: Any, texts: list[str]) -> None:
        for text in texts:
            try:
                await page.get_by_text(text, exact=False).first.wait_for(timeout=1000)
                return
            except Exception:
                continue

    async def _wait_for_pending_state(self, page: Any, *, max_wait_ms: int = 12000) -> bool:
        """Poll the live page DOM until a post-send confirmation token is visible.

        Returns True as soon as one of the confirmation tokens is detected.
        Returns False if max_wait_ms expires without detection.
        No arbitrary sleep — polls with a short async yield between checks.
        """
        _CONFIRMATION_TOKENS = [
            "Pending",
            "Request Sent",
            "Invitation Sent",
            "Withdraw Invitation",
        ]
        deadline = self._deadline_ms(max_wait_ms)
        while self._ms_remaining(deadline) > 0:
            for token in _CONFIRMATION_TOKENS:
                try:
                    locator = page.get_by_text(token, exact=False).first
                    if await locator.is_visible():
                        logger.info("linkedin pending token detected token=%r profile_page=%s", token, page.url)
                        return True
                except Exception:
                    continue
            await asyncio.sleep(0)
        logger.info(
            "linkedin pending state not detected within %sms — proceeding to final inspect",
            max_wait_ms,
        )
        return False

    def _deadline_ms(self, duration_ms: int) -> float:
        import time
        return time.monotonic() + duration_ms / 1000.0

    def _ms_remaining(self, deadline: float) -> float:
        import time
        return (deadline - time.monotonic()) * 1000.0

    async def _find_dialog_send_button(self, page: Any) -> Any | None:
        """Find the Send button scoped to the invite confirmation dialog.

        Filters [role='dialog'] to only those containing invite-specific text
        ("add a note" / "send without a note" / "send invitation") so that
        stale messaging overlay panels — which also expose role='dialog' but
        whose Send button is permanently disabled — are never matched.

        Returns None with a distinct log tag on two failure modes:
          invite_dialog_not_found   — no invite dialog in DOM at all
          send_button_never_enabled — dialog found but Send stayed disabled
        """
        _INVITE_TEXT = re.compile(
            r"add a note|send without a note|send invitation", re.I
        )
        # Scope to the invite modal specifically; fall back to broader selectors
        # only if the role-filtered locator finds nothing.
        invite_dialog = page.locator("[role='dialog']").filter(
            has=page.get_by_text(_INVITE_TEXT)
        )
        count = await invite_dialog.count()
        logger.info("find_dialog_send_button invite_dialog_count=%d", count)

        if count == 0:
            # Try broader selectors that are invite-specific by class/attribute.
            for sel in ("[data-test-modal]", ".send-invite", ".artdeco-modal"):
                try:
                    loc = page.locator(sel).filter(has=page.get_by_text(_INVITE_TEXT))
                    if await loc.count() > 0:
                        invite_dialog = loc
                        count = await loc.count()
                        logger.info(
                            "find_dialog_send_button fallback_sel=%s count=%d", sel, count
                        )
                        break
                except Exception:
                    continue

        if count == 0:
            logger.warning(
                "find_dialog_send_button invite_dialog_not_found "
                "— no dialog with invite text visible"
            )
            return None

        dialog = invite_dialog.first
        send_btn = dialog.get_by_role("button", name=re.compile(r"^Send", re.I))
        btn_count = await send_btn.count()
        logger.info("find_dialog_send_button send_btn_count=%d", btn_count)

        # Log all candidates for diagnostics.
        for i in range(min(btn_count, 5)):
            item = send_btn.nth(i)
            visible = await item.is_visible()
            try:
                label = str(await item.inner_text(timeout=500) or "").strip()
            except Exception:
                label = ""
            logger.info(
                "find_dialog_send_button candidate[%d] visible=%s label=%r",
                i, visible, label,
            )

        if btn_count == 0:
            logger.warning(
                "find_dialog_send_button send_button_never_enabled "
                "— invite dialog found but no Send button present"
            )
            return None

        # Wait up to 5 s for the button to become enabled (it starts disabled
        # before the user types a note, but is enabled for "Send without a note").
        first_btn = send_btn.first
        try:
            from playwright.async_api import expect
            await expect(first_btn).to_be_enabled(timeout=5000)
        except Exception:
            logger.warning(
                "find_dialog_send_button send_button_never_enabled "
                "— button present but stayed disabled for 5 s"
            )
            return None

        return first_btn

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

    async def _dump_connect_dialog(self, page: Any, profile_url: str) -> None:
        """Capture and log the full state of the connect dialog for debugging."""
        import json as _json
        import os
        from pathlib import Path

        debug_dir = Path(__file__).resolve().parents[3] / "debug_logs" / "connect_dialog"
        debug_dir.mkdir(parents=True, exist_ok=True)

        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", profile_url.rstrip("/").split("/")[-1])[:40]
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        prefix = debug_dir / f"{ts}_{slug}"

        # --- screenshot ---
        screenshot_path = str(prefix) + "_dialog.png"
        try:
            await page.screenshot(path=screenshot_path, full_page=False)
            logger.info("connect_dialog_screenshot saved path=%s", screenshot_path)
        except Exception as exc:
            logger.warning("connect_dialog_screenshot failed: %s", exc)

        # --- locate the dialog container ---
        dialog_html = ""
        dialog_selectors = [
            "[role='dialog']",
            "[role='alertdialog']",
            "[data-test-modal]",
            ".send-invite",
            ".artdeco-modal",
        ]
        dialog_locator = None
        for sel in dialog_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible():
                    dialog_locator = loc
                    logger.info("connect_dialog_found selector=%s", sel)
                    break
            except Exception:
                continue

        if dialog_locator is None:
            logger.warning("connect_dialog_not_found — falling back to full page body")
            try:
                dialog_html = await page.locator("body").inner_html(timeout=3000)
            except Exception as exc:
                logger.warning("connect_dialog_body_html failed: %s", exc)
        else:
            try:
                dialog_html = await dialog_locator.evaluate("el => el.outerHTML")
            except Exception as exc:
                logger.warning("connect_dialog_outerHTML failed: %s", exc)

        # --- save HTML ---
        html_path = str(prefix) + "_dialog.html"
        try:
            Path(html_path).write_text(dialog_html, encoding="utf-8")
            logger.info("connect_dialog_html saved path=%s", html_path)
        except Exception as exc:
            logger.warning("connect_dialog_html_write failed: %s", exc)

        # --- enumerate every visible clickable element ---
        clickable_selectors = [
            "button",
            "[role='button']",
            "a",
            "input[type='submit']",
            "input[type='button']",
        ]
        container = dialog_locator if dialog_locator is not None else page.locator("body")
        elements: list[dict] = []
        for sel in clickable_selectors:
            try:
                locs = container.locator(sel)
                count = await locs.count()
                for i in range(min(count, 30)):
                    item = locs.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        attrs = await item.evaluate(
                            """el => ({
                                tag: el.tagName,
                                id: el.id || '',
                                class: el.className || '',
                                role: el.getAttribute('role') || '',
                                aria_label: el.getAttribute('aria-label') || '',
                                data_control_name: el.getAttribute('data-control-name') || '',
                                inner_text: (el.innerText || '').trim().slice(0, 120),
                                type: el.getAttribute('type') || '',
                                disabled: el.disabled || false
                            })"""
                        )
                        attrs["selector"] = sel
                        elements.append(attrs)
                    except Exception:
                        continue
            except Exception:
                continue

        # deduplicate by (tag, inner_text, aria_label)
        seen: set[tuple] = set()
        unique_elements: list[dict] = []
        for el in elements:
            key = (el.get("tag"), el.get("inner_text"), el.get("aria_label"))
            if key not in seen:
                seen.add(key)
                unique_elements.append(el)

        # --- structured log ---
        logger.info(
            "connect_dialog_elements_found count=%d profile_url=%s",
            len(unique_elements),
            profile_url,
        )
        for idx, el in enumerate(unique_elements):
            logger.info(
                "connect_dialog_element[%d] tag=%s text=%r aria_label=%r role=%r "
                "data_control_name=%r class=%r id=%r type=%r disabled=%s",
                idx,
                el.get("tag"),
                el.get("inner_text"),
                el.get("aria_label"),
                el.get("role"),
                el.get("data_control_name"),
                el.get("class"),
                el.get("id"),
                el.get("type"),
                el.get("disabled"),
            )

        # --- save JSON ---
        json_path = str(prefix) + "_elements.json"
        try:
            Path(json_path).write_text(
                _json.dumps(unique_elements, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("connect_dialog_elements_json saved path=%s", json_path)
        except Exception as exc:
            logger.warning("connect_dialog_elements_json_write failed: %s", exc)

    def _duration_ms(self, started_at: datetime) -> int:
        return int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
