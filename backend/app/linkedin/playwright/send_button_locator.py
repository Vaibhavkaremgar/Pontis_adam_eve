"""send_button_locator.py — Send button discovery for all LinkedIn messaging surfaces.

Supports: role=button, aria-label, type=submit, visible text, SVG button.
No CSS class selectors.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selector priority list — semantic only
# ---------------------------------------------------------------------------

_SEND_CANDIDATES: list[str] = [
    # Explicit aria-label
    "[aria-label='Send'][role='button']",
    "[aria-label='Send']",
    "[aria-label*='Send message' i]",
    "[aria-label*='send' i][role='button']",
    "[aria-label*='send' i]",
    # Submit buttons
    "button[type='submit']",
    # Visible text
    "button:has-text('Send')",
    # data-control-name
    "[data-control-name*='send' i]",
    # SVG buttons with accessible name (caught by aria-label above, but belt+suspenders)
    "button svg[aria-label*='send' i]",
]

_SEND_TEXT_RE = re.compile(r"^send$", re.I)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class SendLocatorResult:
    locator: Any | None = None
    selector: str = ""
    html_snippet: str = ""

    @property
    def found(self) -> bool:
        return self.locator is not None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SendButtonLocator:
    """Locate the Send button in any LinkedIn messaging surface."""

    async def locate(self, page: Any) -> SendLocatorResult:
        """Return the first visible, enabled Send button on *page*."""
        # Strategy 1 — explicit selector list
        for selector in _SEND_CANDIDATES:
            try:
                locs = page.locator(selector)
                count = await locs.count()
            except Exception:
                continue

            for i in range(min(count, 10)):
                item = locs.nth(i)
                result = await self._evaluate_candidate(item, selector)
                if result is not None:
                    return result

        # Strategy 2 — role=button with accessible name "Send"
        try:
            loc = page.get_by_role("button", name=_SEND_TEXT_RE)
            count = await loc.count()
            for i in range(min(count, 5)):
                item = loc.nth(i)
                result = await self._evaluate_candidate(item, "role:button[name=Send]")
                if result is not None:
                    return result
        except Exception:
            pass

        logger.debug("send_button_locator exhausted all strategies")
        return SendLocatorResult()

    async def _evaluate_candidate(
        self, item: Any, selector: str
    ) -> SendLocatorResult | None:
        try:
            if not await item.is_visible():
                return None
            attrs = await item.evaluate(
                """el => ({
                    disabled:   el.disabled || false,
                    aria_label: el.getAttribute('aria-label') || '',
                    inner_text: (el.innerText || '').trim().slice(0, 80),
                    snippet:    el.outerHTML.slice(0, 300)
                })"""
            )
            if attrs["disabled"]:
                return None
            logger.info(
                "send_button_locator FOUND selector=%s label=%r text=%r",
                selector, attrs["aria_label"], attrs["inner_text"],
            )
            return SendLocatorResult(
                locator=item,
                selector=selector,
                html_snippet=attrs["snippet"],
            )
        except Exception:
            return None
