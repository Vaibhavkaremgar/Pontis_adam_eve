"""linkedin_cleanup.py — pre-test helpers for the LinkedIn dev-account browser.

close_chat_overlays(account_id):
    Navigates to linkedin.com/messaging and closes every visible chat overlay
    bubble so the bubble count starts at 0 for the next test.  Call this at
    the top of any test script that exercises messaging or connection flows.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.linkedin.playwright.browser_manager import BrowserManager

logger = logging.getLogger(__name__)

_MESSAGING_URL = "https://www.linkedin.com/messaging/"
_FEED_URL = "https://www.linkedin.com/feed/"

# Selectors for the close / minimise button on each overlay bubble.
# LinkedIn has used several over time; we try all of them.
_CLOSE_BUTTON_SELECTORS = [
    "button[aria-label*='Close' i]",
    "button[aria-label*='Dismiss' i]",
    "button[data-control-name*='close' i]",
    "button.msg-overlay-bubble-header__control",
    ".msg-overlay-conversation-bubble__close-btn",
]


async def close_chat_overlays(account_id: str) -> None:
    """Close all open/minimised chat overlay bubbles for *account_id*.

    LinkedIn's overlay bubbles (.msg-overlay-conversation-bubble) only render
    on non-messaging pages (feed, profile pages, etc.) — they are absent on
    /messaging/ itself.  We navigate to the feed to surface them, then click
    the close button on each one.
    """
    manager = BrowserManager(account_id=account_id)
    try:
        context = await manager.get_browser()
        page = await context.new_page()
        try:
            await page.goto(_FEED_URL, wait_until="domcontentloaded", timeout=15000)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            await asyncio.sleep(2.0)  # let overlay bubbles render

            closed = 0
            bubbles = page.locator(".msg-overlay-conversation-bubble")
            count = await bubbles.count()
            logger.info("close_chat_overlays bubble_count=%d account_id=%s", count, account_id)

            for i in range(count):
                bubble = bubbles.nth(i)
                for sel in _CLOSE_BUTTON_SELECTORS:
                    try:
                        btn = bubble.locator(sel).first
                        if await btn.is_visible():
                            await btn.click(timeout=3000)
                            closed += 1
                            logger.info(
                                "close_chat_overlays closed bubble=%d sel=%s", i, sel
                            )
                            await asyncio.sleep(0.3)
                            break
                    except Exception:
                        continue

            logger.info(
                "close_chat_overlays done closed=%d of %d account_id=%s",
                closed, count, account_id,
            )
        finally:
            try:
                await page.close()
            except Exception:
                pass
    finally:
        try:
            await manager.stop()
        except Exception:
            pass
