"""dropdown_engine.py — Generic Playwright dropdown interaction engine.

Supports:
  - Native <select>
  - ARIA listbox
  - ARIA combobox
  - Searchable dropdowns (type-to-filter)
  - Keyboard selection (Arrow + Enter)
  - Mouse selection (click option)
  - Post-selection verification

No LinkedIn-specific logic. Pure Playwright primitives.

Usage:
    engine = DropdownEngine(trigger_locator, container=container_loc)
    await engine.select("Remote")
    ok = await engine.verify("Remote")
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ARIA roles that represent option items
_OPTION_ROLES = ["option", "menuitem", "menuitemradio", "menuitemcheckbox", "treeitem"]

# Selectors tried when looking for the open listbox/menu
_LISTBOX_SELECTORS = [
    "[role='listbox']",
    "[role='menu']",
    "[role='tree']",
    "ul[role='listbox']",
    "[aria-expanded='true'] + *",
    "[data-test-dropdown-options]",
    "[class*='dropdown'] [class*='option']",
    "[class*='select'] [class*='option']",
    "[class*='typeahead'] [class*='result']",
]


class DropdownEngine:
    """Generic dropdown selection engine.

    Args:
        locator:    Playwright Locator for the trigger element
                    (the <select>, combobox input, or button that opens the list).
        container:  Parent container Locator — listbox is searched inside this.
                    Falls back to page-level search if None.
        timeout_ms: Default operation timeout.
    """

    def __init__(
        self,
        locator: Any,
        *,
        container: Any | None = None,
        timeout_ms: int = 10_000,
    ) -> None:
        self._loc = locator
        self._container = container
        self._t = timeout_ms

    # ── Public API ────────────────────────────────────────────────────────────

    async def select(self, option_text: str) -> None:
        """Select an option by its visible text.

        Strategy order:
          1. Native <select> → select_option()
          2. Open trigger → find listbox → click matching option (mouse)
          3. Open trigger → type to filter → keyboard select
        """
        # Strategy 1: native <select>
        if await self._is_native_select():
            await self._select_native(option_text)
            return

        # Strategy 2: open + mouse click
        await self._open()
        matched = await self._find_option_mouse(option_text)
        if matched is not None:
            await matched.scroll_into_view_if_needed(timeout=self._t)
            await matched.click(timeout=self._t)
            logger.debug("dropdown_engine select mouse option=%r", option_text)
            return

        # Strategy 3: type-to-filter + keyboard
        await self._open()
        await self._type_to_filter(option_text)
        matched = await self._find_option_mouse(option_text)
        if matched is not None:
            await matched.click(timeout=self._t)
            logger.debug("dropdown_engine select type_filter option=%r", option_text)
            return

        # Strategy 4: keyboard arrow navigation
        await self._keyboard_select(option_text)

    async def select_by_keyboard(self, option_text: str) -> None:
        """Select using only keyboard (Arrow keys + Enter)."""
        await self._open()
        await self._keyboard_select(option_text)

    async def verify(self, expected_value: str) -> bool:
        """Return True if the dropdown currently shows *expected_value*."""
        from app.linkedin.playwright.verification_helpers import verify_dropdown
        container = self._container
        if container is None:
            # Wrap the locator itself as a minimal container
            return await _verify_locator_value(self._loc, expected_value, self._t)
        return await verify_dropdown(
            container,
            selector="*",   # caller should pass a more specific selector via FormEngine
            expected_value=expected_value,
            timeout_ms=self._t,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _is_native_select(self) -> bool:
        try:
            tag = str(await self._loc.evaluate("el => el.tagName.toLowerCase()") or "")
            return tag == "select"
        except Exception:
            return False

    async def _select_native(self, option_text: str) -> None:
        try:
            await self._loc.select_option(label=option_text, timeout=self._t)
            logger.debug("dropdown_engine native_select option=%r", option_text)
        except Exception:
            await self._loc.select_option(value=option_text, timeout=self._t)

    async def _open(self) -> None:
        """Click the trigger to open the dropdown."""
        try:
            await self._loc.scroll_into_view_if_needed(timeout=self._t)
            await self._loc.click(timeout=self._t)
            await asyncio.sleep(0.2)
        except Exception as exc:
            logger.warning("dropdown_engine open failed: %s", exc)

    async def _find_option_mouse(self, option_text: str) -> Any | None:
        """Find a visible option element matching *option_text*."""
        search_root = self._container
        for sel in _LISTBOX_SELECTORS:
            try:
                root = search_root.locator(sel) if search_root else None
                if root is None:
                    continue
                count = await root.count()
                if count == 0:
                    continue
                # Search for option text inside each listbox candidate
                for i in range(min(count, 5)):
                    listbox = root.nth(i)
                    for role in _OPTION_ROLES:
                        try:
                            opts = listbox.get_by_role(role)
                            opt_count = await opts.count()
                            for j in range(min(opt_count, 50)):
                                opt = opts.nth(j)
                                try:
                                    text = str(await opt.inner_text(timeout=500) or "")
                                    if option_text.lower() in text.lower():
                                        if await opt.is_visible():
                                            return opt
                                except Exception:
                                    continue
                        except Exception:
                            continue
            except Exception:
                continue
        return None

    async def _type_to_filter(self, option_text: str) -> None:
        """Type into a searchable combobox to filter options."""
        try:
            await self._loc.fill("", timeout=self._t)
            for char in option_text:
                await self._loc.type(char, delay=0)
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.3)
        except Exception as exc:
            logger.debug("dropdown_engine type_filter failed: %s", exc)

    async def _keyboard_select(self, option_text: str) -> None:
        """Navigate with Arrow keys and press Enter on the matching option."""
        try:
            await self._loc.press("ArrowDown")
            await asyncio.sleep(0.1)
            for _ in range(30):
                focused_text = ""
                try:
                    focused_text = str(
                        await self._loc.evaluate(
                            "() => document.activeElement ? document.activeElement.textContent : ''"
                        ) or ""
                    )
                except Exception:
                    pass
                if option_text.lower() in focused_text.lower():
                    await self._loc.press("Enter")
                    logger.debug("dropdown_engine keyboard_select option=%r", option_text)
                    return
                await self._loc.press("ArrowDown")
                await asyncio.sleep(0.05)
        except Exception as exc:
            logger.warning("dropdown_engine keyboard_select failed: %s", exc)


async def _verify_locator_value(loc: Any, expected: str, timeout_ms: int) -> bool:
    for method in ("input_value", "inner_text", "text_content"):
        try:
            val = str(await getattr(loc, method)(timeout=timeout_ms) or "")
            if expected.lower() in val.lower():
                return True
        except Exception:
            continue
    return False
