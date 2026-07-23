"""messaging_context_validator.py — prove the current UI is LinkedIn Messaging.

Validation requires ALL of the following to be true:
  1. A messaging conversation container exists and is visible.
  2. That container contains a conversation/thread region.
  3. The compose editor belongs to that container.
  4. The Send button belongs to that same container.
  5. The compose editor is NOT inside a rejected non-messaging overlay
     (Share dialog, Send-post dialog, Comment editor, Feed overlay, Article dialog).

Returns MessagingContextResult with:
  is_messaging_context  bool
  compose_locator       Playwright Locator | None
  send_locator          Playwright Locator | None
  conversation_locator  Playwright Locator | None
  reason                str
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Messaging container selectors — ordered most-specific first
# ---------------------------------------------------------------------------

_MESSAGING_CONTAINER_SELECTORS: list[str] = [
    # Full messaging page
    "[data-test-messaging-compose]",
    ".msg-overlay-conversation-bubble",
    ".msg-convo-wrapper",
    # Semantic fallbacks
    "[aria-label*='conversation' i][role='region']",
    "[aria-label*='messaging' i][role='region']",
    "[aria-label*='message' i][role='dialog']",
    # Thread/conversation page main content
    "main[aria-label*='conversation' i]",
    "main[aria-label*='messaging' i]",
    # Generic: any element whose aria-label contains "messaging"
    "[aria-label*='messaging' i]",
]

# Thread/conversation region inside the container
_THREAD_REGION_SELECTORS: list[str] = [
    "[data-test-messaging-thread]",
    "[aria-label*='conversation' i][role='log']",
    "[aria-label*='messages' i][role='log']",
    "[role='log']",
    "[aria-label*='thread' i]",
    "[aria-label*='conversation' i]",
    "[aria-label*='messages' i]",
    # Fallback: any scrollable list of messages
    "[data-test-msg-list]",
    ".msg-s-message-list",
]

# Compose field selectors — same priority order as ComposeLocator
_COMPOSE_SELECTORS: list[str] = [
    "[aria-label*='message' i][contenteditable='true']",
    "[aria-label*='message' i][role='textbox']",
    "[aria-label*='compose' i][contenteditable='true']",
    "[aria-label*='write' i][contenteditable='true']",
    "[data-lexical-editor][contenteditable='true']",
    "[data-lexical-editor='true']",
    "[aria-multiline='true'][contenteditable='true']",
    "[role='textbox'][contenteditable='true']",
    "[role='textbox']",
    "div[contenteditable='true']",
    "[contenteditable='true']",
    "[placeholder*='message' i]",
    "textarea",
]

# Send button selectors
_SEND_SELECTORS: list[str] = [
    "[aria-label='Send'][role='button']",
    "[aria-label='Send']",
    "[aria-label*='Send message' i]",
    "[aria-label*='send' i][role='button']",
    "button[type='submit']",
    "button:has-text('Send')",
    "[data-control-name*='send' i]",
]

# ---------------------------------------------------------------------------
# Rejected ancestor patterns — if compose lives inside any of these, reject.
# Each entry: (JS ancestor-check expression, label)
# The expression receives `el` (the compose element) and must return true
# if a rejected ancestor is found.
# ---------------------------------------------------------------------------

_REJECTED_ANCESTOR_JS = """
el => {
    let node = el.parentElement;
    while (node) {
        const role  = (node.getAttribute('role') || '').toLowerCase();
        const label = (node.getAttribute('aria-label') || '').toLowerCase();
        const id    = (node.id || '').toLowerCase();
        const cls   = (node.className || '').toLowerCase();

        // Share post dialog
        if (label.includes('share') && (role === 'dialog' || role === 'alertdialog'))
            return 'share_dialog';

        // Send post / forward post dialog
        if ((label.includes('send post') || label.includes('forward'))
                && (role === 'dialog' || role === 'alertdialog'))
            return 'send_post_dialog';

        // Comment editor — identified by aria-label or data attribute
        if (label.includes('comment') && node.getAttribute('contenteditable') === 'true')
            return 'comment_editor';
        if (label.includes('add a comment') || label.includes('write a comment'))
            return 'comment_editor';

        // Feed overlay / update composer
        if (label.includes('create a post') || label.includes('share an update')
                || label.includes('start a post'))
            return 'feed_overlay';
        if (id.includes('share-box') || cls.includes('share-box'))
            return 'feed_overlay';

        // Article / newsletter editor
        if (label.includes('article') || label.includes('newsletter'))
            return 'article_editor';

        // Generic "post" dialog that is NOT messaging
        if ((label.includes('post') || label.includes('publish'))
                && (role === 'dialog' || role === 'alertdialog')
                && !label.includes('message'))
            return 'post_dialog';

        node = node.parentElement;
    }
    return null;
}
"""

# JS: check whether `child` is a DOM descendant of `ancestor`
_IS_DESCENDANT_JS = "([ancestor, child]) => ancestor.contains(child)"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class MessagingContextResult:
    is_messaging_context: bool = False
    compose_locator: Any | None = None
    send_locator: Any | None = None
    conversation_locator: Any | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class MessagingContextValidator:
    """Validate that the current page state is a LinkedIn Messaging conversation."""

    async def validate(self, page: Any) -> MessagingContextResult:
        """Run all five validation checks.

        Returns MessagingContextResult.  Never raises.
        """
        try:
            return await self._validate(page)
        except Exception as exc:
            logger.warning("messaging_context_validator unexpected error: %s", exc)
            return MessagingContextResult(
                is_messaging_context=False,
                reason=f"validator_exception: {exc}",
            )

    async def _validate(self, page: Any) -> MessagingContextResult:
        # ── Check 1: find a messaging container ──────────────────────────────
        container_loc = await self._find_container(page)
        if container_loc is None:
            return MessagingContextResult(
                is_messaging_context=False,
                reason="no_messaging_container_found",
            )

        # ── Check 2: container must have a thread/conversation region ─────────
        thread_loc = await self._find_thread_in_container(page, container_loc)
        if thread_loc is None:
            return MessagingContextResult(
                is_messaging_context=False,
                reason="no_thread_region_in_container",
            )

        # ── Check 3: find compose editor inside the container ─────────────────
        compose_loc, compose_sel = await self._find_compose_in_container(
            page, container_loc
        )
        if compose_loc is None:
            return MessagingContextResult(
                is_messaging_context=False,
                reason="no_compose_editor_in_messaging_container",
            )

        # ── Check 4: find Send button inside the same container ───────────────
        send_loc, send_sel = await self._find_send_in_container(page, container_loc)
        if send_loc is None:
            return MessagingContextResult(
                is_messaging_context=False,
                reason="no_send_button_in_messaging_container",
            )

        # ── Check 5: reject compose if it belongs to a non-messaging overlay ──
        rejection = await self._check_rejected_ancestor(compose_loc)
        if rejection:
            return MessagingContextResult(
                is_messaging_context=False,
                reason=f"compose_in_rejected_context:{rejection}",
            )

        logger.info(
            "messaging_context_validator VALID compose=%s send=%s",
            compose_sel, send_sel,
        )
        return MessagingContextResult(
            is_messaging_context=True,
            compose_locator=compose_loc,
            send_locator=send_loc,
            conversation_locator=thread_loc,
            reason="ok",
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _find_container(self, page: Any) -> Any | None:
        """Return the first visible messaging container locator, or None."""
        for sel in _MESSAGING_CONTAINER_SELECTORS:
            try:
                locs = page.locator(sel)
                count = await locs.count()
                for i in range(min(count, 5)):
                    item = locs.nth(i)
                    if await item.is_visible():
                        logger.debug("messaging_container found sel=%s", sel)
                        return item
            except Exception:
                continue
        return None

    async def _find_thread_in_container(
        self, page: Any, container: Any
    ) -> Any | None:
        """Return a visible thread/log region that is a descendant of container."""
        for sel in _THREAD_REGION_SELECTORS:
            try:
                # Search within the container subtree
                locs = container.locator(sel)
                count = await locs.count()
                for i in range(min(count, 5)):
                    item = locs.nth(i)
                    if await item.is_visible():
                        logger.debug("thread_region found sel=%s", sel)
                        return item
            except Exception:
                continue

        # Fallback: URL-based proof (full messaging page)
        try:
            url = str(getattr(page, "url", ""))
            if "/messaging/thread/" in url or "/messaging/compose/" in url:
                logger.debug("thread_region proven by URL: %s", url)
                return container  # container itself is the thread context
        except Exception:
            pass

        return None

    async def _find_compose_in_container(
        self, page: Any, container: Any
    ) -> tuple[Any | None, str]:
        """Return (locator, selector) for the first valid compose field inside container."""
        for sel in _COMPOSE_SELECTORS:
            try:
                locs = container.locator(sel)
                count = await locs.count()
                for i in range(min(count, 5)):
                    item = locs.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        if not await item.is_editable(timeout=500):
                            continue
                        logger.debug("compose_in_container found sel=%s", sel)
                        return item, sel
                    except Exception:
                        continue
            except Exception:
                continue
        return None, ""

    async def _find_send_in_container(
        self, page: Any, container: Any
    ) -> tuple[Any | None, str]:
        """Return (locator, selector) for the first visible Send button inside container."""
        for sel in _SEND_SELECTORS:
            try:
                locs = container.locator(sel)
                count = await locs.count()
                for i in range(min(count, 5)):
                    item = locs.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        disabled = await item.evaluate("el => el.disabled || false")
                        # Allow disabled Send (message not typed yet) — it still proves
                        # we are in the right container.  Typing will enable it.
                        logger.debug(
                            "send_in_container found sel=%s disabled=%s", sel, disabled
                        )
                        return item, sel
                    except Exception:
                        continue
            except Exception:
                continue
        return None, ""

    async def _check_rejected_ancestor(self, compose_loc: Any) -> str | None:
        """Return rejection label if compose lives inside a non-messaging overlay."""
        try:
            result = await compose_loc.evaluate(_REJECTED_ANCESTOR_JS)
            return result or None
        except Exception:
            return None
