"""navigation_tracker.py — LinkedIn page structural state tracker.

Registers Playwright page events and provides wait_for_page_state() which
reports WHAT structural surface is open after a click.

Responsibilities:
  - Observe URL changes, frame events, network requests.
  - Classify the structural page state (thread page, compose page, etc.).
  - Log all observations for diagnostics.

NOT responsible for:
  - Deciding whether messaging succeeded or failed.
  - Detecting premium popups.
  - Any business logic whatsoever.

Business decisions belong exclusively in MessagingWorker.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structural page-state constants
# ---------------------------------------------------------------------------

PAGE_PROFILE          = "PROFILE"           # Still on the profile page
PAGE_MESSAGE_COMPOSE  = "MESSAGE_COMPOSE"   # Compose overlay / drawer / panel opened
PAGE_MESSAGE_THREAD   = "MESSAGE_THREAD"    # Navigated to a full thread URL
PAGE_MESSAGE_PAGE     = "MESSAGE_PAGE"      # Full /messaging/ page (no specific thread)
PAGE_DIALOG           = "DIALOG"            # A modal dialog is open (content unknown)
PAGE_UNKNOWN          = "UNKNOWN"           # Could not classify within timeout

# ---------------------------------------------------------------------------
# Structural surface selectors — describe WHAT is open, not WHY
# ---------------------------------------------------------------------------

# Compose surface: any of these visible means a messaging input panel is open
_COMPOSE_SURFACE_SELECTORS: list[str] = [
    "[data-test-messaging-compose]",
    "[aria-label*='messaging' i]",
    "[aria-label*='compose' i]",
    ".msg-overlay-conversation-bubble",
    ".msg-overlay-bubble-header",
    ".msg-form",
    "[data-test-messaging-drawer]",
    ".msg-convo-wrapper",
]

# Compose field: a direct editable element is present
_COMPOSE_FIELD_SELECTORS: list[str] = [
    "textarea",
    "[contenteditable='true']",
    "[role='textbox']",
    "[data-lexical-editor]",
    "[placeholder*='message' i]",
    "[placeholder*='write' i]",
]


class NavigationTracker:
    """Attach to a Playwright page and track structural navigation events.

    Usage:
        tracker = NavigationTracker(page, profile_url)
        await tracker.poll_checkpoints()
        state = await tracker.wait_for_page_state()
        # state is one of the PAGE_* constants above
    """

    def __init__(self, page: Any, profile_url: str = "") -> None:
        self._page = page
        self._profile_url = profile_url
        self._events: list[dict] = []
        self._attach()

    # ------------------------------------------------------------------
    # Event attachment — observation only, no decisions
    # ------------------------------------------------------------------

    def _attach(self) -> None:
        page = self._page
        profile_url = self._profile_url

        def _on_framenavigated(frame: Any) -> None:
            try:
                url = getattr(frame, "url", "")
                logger.info("nav_event framenavigated url=%s profile_url=%s", url, profile_url)
                self._events.append({"event": "framenavigated", "url": url})
            except Exception:
                pass

        def _on_load(page_obj: Any) -> None:
            try:
                url = getattr(page_obj, "url", "")
                logger.info("nav_event load url=%s profile_url=%s", url, profile_url)
                self._events.append({"event": "load", "url": url})
            except Exception:
                pass

        def _on_domcontentloaded(page_obj: Any) -> None:
            try:
                url = getattr(page_obj, "url", "")
                logger.info("nav_event domcontentloaded url=%s profile_url=%s", url, profile_url)
                self._events.append({"event": "domcontentloaded", "url": url})
            except Exception:
                pass

        def _on_popup(popup: Any) -> None:
            try:
                url = getattr(popup, "url", "")
                logger.info("nav_event popup url=%s profile_url=%s", url, profile_url)
                self._events.append({"event": "popup", "url": url})
            except Exception:
                pass

        def _on_request(request: Any) -> None:
            try:
                url = getattr(request, "url", "")
                if "/messaging/" in url or "/voyager/" in url:
                    logger.debug("nav_event request url=%s", url)
                    self._events.append({"event": "request", "url": url})
            except Exception:
                pass

        def _on_response(response: Any) -> None:
            try:
                url = getattr(response, "url", "")
                if "/messaging/" in url or "/voyager/" in url:
                    logger.debug("nav_event response url=%s", url)
                    self._events.append({"event": "response", "url": url})
            except Exception:
                pass

        def _on_frameattached(frame: Any) -> None:
            try:
                url = getattr(frame, "url", "")
                logger.debug("nav_event frameattached url=%s", url)
                self._events.append({"event": "frameattached", "url": url})
            except Exception:
                pass

        try:
            page.on("framenavigated", _on_framenavigated)
            page.on("load", _on_load)
            page.on("domcontentloaded", _on_domcontentloaded)
            page.on("popup", _on_popup)
            page.on("request", _on_request)
            page.on("response", _on_response)
            page.on("frameattached", _on_frameattached)
        except Exception as exc:
            logger.debug("navigation_tracker attach_failed: %s", exc)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def poll_checkpoints(self) -> None:
        """Log URL at 250 ms, 500 ms, 1 s, 2 s after a click."""
        checkpoints = [0.25, 0.5, 1.0, 2.0]
        elapsed = 0.0
        for delay in checkpoints:
            await asyncio.sleep(delay - elapsed)
            elapsed = delay
            url = str(getattr(self._page, "url", ""))
            logger.info("nav_poll elapsed=%.2fs url=%s", elapsed, url)
        try:
            await self._page.wait_for_load_state("networkidle", timeout=3000)
            logger.info("nav_poll networkidle url=%s", str(getattr(self._page, "url", "")))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Structural state detection — NO business decisions
    # ------------------------------------------------------------------

    async def wait_for_page_state(self, *, max_wait_ms: int = 8000) -> str:
        """Detect the structural page state after a click.

        Returns one of the PAGE_* constants.  Never returns a business
        outcome like 'premium' — that classification belongs in the worker.

        Priority order:
          1. Thread URL  → PAGE_MESSAGE_THREAD
          2. Compose URL → PAGE_MESSAGE_PAGE
          3. Compose surface visible → PAGE_MESSAGE_COMPOSE
          4. Compose field visible   → PAGE_MESSAGE_COMPOSE
          5. Any dialog visible      → PAGE_DIALOG
          6. Timeout                 → PAGE_UNKNOWN
        """
        deadline = time.monotonic() + max_wait_ms / 1000.0
        page = self._page

        while time.monotonic() < deadline:
            url = str(getattr(page, "url", ""))

            # 1. Full thread navigation
            if "/messaging/thread/" in url or "/messaging/compose/" in url:
                logger.info("nav_state=%s url=%s", PAGE_MESSAGE_THREAD, url)
                return PAGE_MESSAGE_THREAD

            # 2. Messaging page (compose or inbox)
            if "/messaging/new/" in url or "/messaging/?compose" in url or (
                "/messaging/" in url and url != self._profile_url
            ):
                logger.info("nav_state=%s url=%s", PAGE_MESSAGE_PAGE, url)
                return PAGE_MESSAGE_PAGE

            # 3. Compose surface container visible
            for sel in _COMPOSE_SURFACE_SELECTORS:
                try:
                    if await page.locator(sel).first.is_visible():
                        logger.info("nav_state=%s selector=%s", PAGE_MESSAGE_COMPOSE, sel)
                        return PAGE_MESSAGE_COMPOSE
                except Exception:
                    continue

            # 4. Compose field directly visible (overlay without container marker)
            for sel in _COMPOSE_FIELD_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible():
                        try:
                            editable = await loc.is_editable(timeout=300)
                        except Exception:
                            editable = False
                        if editable:
                            logger.info("nav_state=%s field_selector=%s", PAGE_MESSAGE_COMPOSE, sel)
                            return PAGE_MESSAGE_COMPOSE
                except Exception:
                    continue

            # 5. Any dialog is open — content unknown, worker will inspect
            try:
                dloc = page.locator("[role='dialog'], [role='alertdialog'], dialog").first
                if await dloc.is_visible():
                    logger.info("nav_state=%s url=%s", PAGE_DIALOG, url)
                    return PAGE_DIALOG
            except Exception:
                pass

            await asyncio.sleep(0.15)

        logger.info("nav_state=%s (timeout after %dms)", PAGE_UNKNOWN, max_wait_ms)
        return PAGE_UNKNOWN

    @property
    def events(self) -> list[dict]:
        """Read-only access to all observed events for diagnostics."""
        return list(self._events)
