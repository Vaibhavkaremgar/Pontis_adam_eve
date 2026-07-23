"""human_interaction.py — human-like Playwright interaction primitives.

All workers must use these functions instead of raw locator.click() /
locator.type() calls.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

logger = logging.getLogger(__name__)


async def wait_after_action(*, min_ms: int = 80, max_ms: int = 220) -> None:
    """Short randomised pause that mimics human reaction time."""
    delay = random.uniform(min_ms / 1000.0, max_ms / 1000.0)
    await asyncio.sleep(delay)


async def human_scroll(locator: Any) -> None:
    """Scroll the element into the viewport."""
    try:
        await locator.scroll_into_view_if_needed(timeout=3000)
    except Exception as exc:
        logger.debug("human_scroll scroll_into_view failed: %s", exc)


async def human_hover(locator: Any) -> None:
    """Move the mouse over the element naturally."""
    try:
        await locator.hover(timeout=3000)
    except Exception as exc:
        logger.debug("human_hover failed: %s", exc)


async def human_click(locator: Any, *, profile_url: str = "") -> None:
    """Human-like click: scroll → hover → hesitate → normal click → JS fallback.

    force=True is never used.  Experiment evidence shows force=True triggers
    LinkedIn's Premium upsell handler; a normal click does not.

    Strategy:
      1. Scroll element into view.
      2. Hover (natural mouse movement).
      3. Random hesitation 150–350 ms (mimics human reaction time).
      4. locator.click() — the only click strategy.
      5. Only if step 4 raises → JS el.click() as last resort.
         JS click is safe: experiment proved it opens compose correctly.
    """
    ctx = f" profile_url={profile_url}" if profile_url else ""

    # Step 1 — scroll into view
    await human_scroll(locator)

    # Step 2 — hover
    await human_hover(locator)

    # Step 3 — human hesitation before clicking
    await wait_after_action(min_ms=150, max_ms=350)

    # Step 4 — normal click (only strategy; force=True is intentionally absent)
    try:
        await locator.click(timeout=8000)
        logger.info("human_click strategy=normal result=OK%s", ctx)
        await wait_after_action()
        return
    except Exception as exc:
        logger.info("human_click strategy=normal result=FAIL reason=%r%s", str(exc), ctx)

    # Step 5 — JS DOM click (safe fallback; proven not to trigger Premium)
    try:
        await locator.evaluate("(el) => el.click()")
        logger.info("human_click strategy=js_click result=OK%s", ctx)
        await wait_after_action()
    except Exception as exc:
        logger.error("human_click strategy=js_click result=FAIL reason=%r%s", str(exc), ctx)
        raise


async def human_type(locator: Any, text: str) -> None:
    """Focus, clear, then type character-by-character at 40–120 ms per keystroke.

    locator.fill() pastes instantly and is an obvious automation signal;
    per-character typing with randomised delay is realistic.
    """
    try:
        await locator.focus(timeout=5000)
        await locator.evaluate(
            "el => { el.textContent = ''; if (el.value !== undefined) el.value = ''; }"
        )
        await locator.press("Control+a")
        await locator.press("Delete")
        for char in text:
            await locator.type(char, delay=0)
            await asyncio.sleep(random.uniform(0.040, 0.120))
        await asyncio.sleep(0.3)
        logger.info("human_type chars=%d", len(text))
    except Exception as exc:
        logger.warning("human_type failed: %s", exc)
        raise
