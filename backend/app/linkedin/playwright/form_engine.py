"""form_engine.py — Reusable Playwright form interaction engine.

All operations are scoped to a supplied *container* Locator.
Never queries the whole page.
No LinkedIn-specific business logic — pure Playwright primitives.

Supported controls:
  text input, textarea, rich text, dropdown, checkbox, radio,
  toggle, file upload, date picker, multi-select,
  button (Next / Publish / Save Draft / generic)

Usage:
    form = FormEngine(container_locator)
    await form.fill_text("[name='title']", "Senior Engineer")
    await form.fill_textarea("[name='description']", "...")
    await form.select_dropdown("[data-test='location']", "Remote")
    await form.click_button("Next")
"""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FormEngine:
    """Container-scoped form interaction engine.

    Args:
        container: Playwright Locator that scopes ALL operations.
                   Must resolve to exactly one element.
        timeout_ms: Default timeout for individual operations.
    """

    def __init__(self, container: Any, *, timeout_ms: int = 10_000) -> None:
        self._c = container
        self._t = timeout_ms

    # ── Text input ────────────────────────────────────────────────────────────

    async def fill_text(self, selector: str, value: str) -> None:
        """Clear and fill a plain text input."""
        loc = self._c.locator(selector).first
        await loc.scroll_into_view_if_needed(timeout=self._t)
        await loc.click(timeout=self._t)
        await loc.press("Control+a")
        await loc.press("Delete")
        await loc.fill(value, timeout=self._t)
        logger.debug("form_engine fill_text sel=%s len=%d", selector, len(value))

    # ── Textarea ──────────────────────────────────────────────────────────────

    async def fill_textarea(self, selector: str, value: str) -> None:
        """Clear and fill a textarea."""
        await self.fill_text(selector, value)

    # ── Rich text (contenteditable / Lexical / Draft.js / ProseMirror) ────────

    async def fill_rich_text(self, selector: str, value: str) -> None:
        """Type into a rich-text editor character-by-character."""
        from app.linkedin.playwright.rich_text_engine import RichTextEngine
        loc = self._c.locator(selector).first
        engine = RichTextEngine(loc)
        await engine.focus()
        await engine.clear()
        await engine.type(value)
        logger.debug("form_engine fill_rich_text sel=%s len=%d", selector, len(value))

    # ── Dropdown ──────────────────────────────────────────────────────────────

    async def select_dropdown(self, selector: str, option_text: str) -> None:
        """Select an option from a native <select> or ARIA listbox/combobox."""
        from app.linkedin.playwright.dropdown_engine import DropdownEngine
        loc = self._c.locator(selector).first
        engine = DropdownEngine(loc, container=self._c, timeout_ms=self._t)
        await engine.select(option_text)
        logger.debug("form_engine select_dropdown sel=%s option=%r", selector, option_text)

    # ── Checkbox ──────────────────────────────────────────────────────────────

    async def set_checkbox(self, selector: str, *, checked: bool = True) -> None:
        """Set a checkbox to the desired state."""
        loc = self._c.locator(selector).first
        current = await loc.is_checked()
        if current != checked:
            await loc.click(timeout=self._t)
        logger.debug("form_engine set_checkbox sel=%s checked=%s", selector, checked)

    # ── Radio ─────────────────────────────────────────────────────────────────

    async def select_radio(self, selector: str) -> None:
        """Click a radio button."""
        loc = self._c.locator(selector).first
        await loc.click(timeout=self._t)
        logger.debug("form_engine select_radio sel=%s", selector)

    # ── Toggle ────────────────────────────────────────────────────────────────

    async def set_toggle(self, selector: str, *, enabled: bool = True) -> None:
        """Set a toggle/switch to the desired state.

        Reads aria-checked or aria-pressed to determine current state.
        """
        loc = self._c.locator(selector).first
        current_raw = await loc.get_attribute("aria-checked") or \
                      await loc.get_attribute("aria-pressed") or "false"
        current = current_raw.lower() == "true"
        if current != enabled:
            await loc.click(timeout=self._t)
        logger.debug("form_engine set_toggle sel=%s enabled=%s", selector, enabled)

    # ── File upload ───────────────────────────────────────────────────────────

    async def upload_file(self, selector: str, file_path: str | Path) -> None:
        """Upload a file via an input[type=file] element."""
        from app.linkedin.playwright.file_upload_engine import FileUploadEngine
        loc = self._c.locator(selector).first
        engine = FileUploadEngine(loc, container=self._c, timeout_ms=self._t)
        await engine.upload(file_path)
        logger.debug("form_engine upload_file sel=%s path=%s", selector, file_path)

    # ── Date picker ───────────────────────────────────────────────────────────

    async def fill_date(self, selector: str, value: str) -> None:
        """Fill a date input (ISO format: YYYY-MM-DD or locale string)."""
        loc = self._c.locator(selector).first
        await loc.click(timeout=self._t)
        await loc.fill(value, timeout=self._t)
        await loc.press("Tab")
        logger.debug("form_engine fill_date sel=%s value=%s", selector, value)

    # ── Multi-select ──────────────────────────────────────────────────────────

    async def select_multi(self, selector: str, options: list[str]) -> None:
        """Select multiple options from a multi-select control."""
        from app.linkedin.playwright.dropdown_engine import DropdownEngine
        loc = self._c.locator(selector).first
        engine = DropdownEngine(loc, container=self._c, timeout_ms=self._t)
        for option in options:
            await engine.select(option)
            await asyncio.sleep(random.uniform(0.1, 0.3))
        logger.debug("form_engine select_multi sel=%s count=%d", selector, len(options))

    # ── Buttons ───────────────────────────────────────────────────────────────

    async def click_button(self, label: str) -> None:
        """Click a button by its visible text label (case-insensitive)."""
        import re
        btn = self._c.get_by_role("button", name=re.compile(label, re.I)).first
        await btn.scroll_into_view_if_needed(timeout=self._t)
        await btn.click(timeout=self._t)
        logger.debug("form_engine click_button label=%r", label)

    async def click_next(self) -> None:
        """Click the Next / Continue button."""
        await self.click_button("Next")

    async def click_publish(self) -> None:
        """Click the Publish / Post button."""
        import re
        for label in ("Publish", "Post", "Submit"):
            try:
                await self.click_button(label)
                return
            except Exception:
                continue
        raise RuntimeError("form_engine: no Publish/Post/Submit button found")

    async def click_save_draft(self) -> None:
        """Click the Save Draft button."""
        import re
        for label in ("Save draft", "Save as draft", "Save"):
            try:
                await self.click_button(label)
                return
            except Exception:
                continue
        raise RuntimeError("form_engine: no Save Draft button found")
