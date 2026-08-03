"""rich_text_engine.py — Reusable rich-text typing engine for Playwright.

Supports:
  - Lexical (LinkedIn, Facebook)
  - contenteditable (generic)
  - Draft.js
  - ProseMirror
  - textarea / plain input

This engine is INDEPENDENT of MessagingWorker.
Messaging continues using its own implementation in message_delivery_service.py.
This engine exists for future features (Job Posting, etc.).

API:
    engine = RichTextEngine(locator)
    await engine.focus()
    await engine.clear()
    await engine.type("Hello world")
    ok = await engine.verify("Hello world")
"""
from __future__ import annotations

import asyncio
import logging
import random
from enum import Enum, auto
from typing import Any

logger = logging.getLogger(__name__)


class EditorKind(Enum):
    LEXICAL = auto()
    CONTENTEDITABLE = auto()
    DRAFTJS = auto()
    PROSEMIRROR = auto()
    TEXTAREA = auto()
    UNKNOWN = auto()


def _detect_kind(locator: Any) -> EditorKind:
    """Best-effort static detection — runtime detection happens in focus()."""
    return EditorKind.UNKNOWN


class RichTextEngine:
    """Generic rich-text typing engine.

    All operations are scoped to the supplied *locator*.
    No page-wide queries are ever made.

    Args:
        locator:    Playwright Locator pointing at the editor element.
        char_delay: (min_ms, max_ms) per-keystroke delay range.
        settle_ms:  Wait after typing for the editor to reconcile.
    """

    def __init__(
        self,
        locator: Any,
        *,
        char_delay: tuple[int, int] = (40, 120),
        settle_ms: int = 300,
    ) -> None:
        self._loc = locator
        self._char_delay = char_delay
        self._settle_ms = settle_ms
        self._kind: EditorKind = EditorKind.UNKNOWN

    # ── Public API ────────────────────────────────────────────────────────────

    async def focus(self) -> None:
        """Click to focus the editor and detect its kind."""
        try:
            await self._loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            await self._loc.click(timeout=5000)
            await asyncio.sleep(0.1)
        except Exception as exc:
            logger.warning("rich_text_engine focus click failed: %s", exc)
            try:
                await self._loc.focus(timeout=3000)
            except Exception:
                pass

        self._kind = await self._detect_kind_runtime()
        logger.debug("rich_text_engine focus kind=%s", self._kind.name)

    async def clear(self) -> None:
        """Clear all content using real keyboard events (no DOM mutation).

        Uses Ctrl+A → Delete so that Lexical/Draft.js/ProseMirror internal
        state is updated through their own event handlers.
        """
        try:
            await self._loc.press("Control+a")
            await asyncio.sleep(0.05)
            await self._loc.press("Delete")
            await asyncio.sleep(0.05)
            logger.debug("rich_text_engine cleared kind=%s", self._kind.name)
        except Exception as exc:
            logger.warning("rich_text_engine clear failed: %s", exc)
            raise

    async def type(self, text: str) -> None:
        """Type *text* character-by-character with randomised human delay.

        Does NOT call fill() — fill() pastes instantly and is an obvious
        automation signal that Lexical/Draft.js may ignore or flag.
        """
        min_s = self._char_delay[0] / 1000.0
        max_s = self._char_delay[1] / 1000.0
        try:
            for char in text:
                await self._loc.type(char, delay=0)
                await asyncio.sleep(random.uniform(min_s, max_s))
            await asyncio.sleep(self._settle_ms / 1000.0)
            logger.debug("rich_text_engine typed chars=%d kind=%s", len(text), self._kind.name)
        except Exception as exc:
            logger.warning("rich_text_engine type failed: %s", exc)
            raise

    async def verify(self, expected: str) -> bool:
        """Return True if the editor currently contains *expected* text.

        Tries multiple read strategies in order of reliability.
        """
        for method in ("input_value", "inner_text", "text_content"):
            try:
                value = str(
                    await getattr(self._loc, method)(timeout=3000) or ""
                ).strip()
                if value and (expected in value or value == expected.strip()):
                    logger.debug("rich_text_engine verify=ok method=%s", method)
                    return True
            except Exception:
                continue
        try:
            value = str(await self._loc.get_attribute("value") or "").strip()
            if value and expected in value:
                return True
        except Exception:
            pass
        logger.debug("rich_text_engine verify=fail expected_len=%d", len(expected))
        return False

    # ── Runtime kind detection ────────────────────────────────────────────────

    async def _detect_kind_runtime(self) -> EditorKind:
        """Inspect DOM attributes to identify the editor framework."""
        try:
            attrs: dict = await self._loc.evaluate(
                """el => ({
                    lexical: !!el.getAttribute('data-lexical-editor'),
                    draftjs: !!(el.getAttribute('data-contents') || el.closest('[data-contents]')),
                    prosemirror: el.classList.contains('ProseMirror'),
                    contenteditable: el.getAttribute('contenteditable') === 'true',
                    tag: el.tagName.toLowerCase(),
                })"""
            )
        except Exception:
            return EditorKind.UNKNOWN

        if attrs.get("lexical"):
            return EditorKind.LEXICAL
        if attrs.get("draftjs"):
            return EditorKind.DRAFTJS
        if attrs.get("prosemirror"):
            return EditorKind.PROSEMIRROR
        if attrs.get("contenteditable"):
            return EditorKind.CONTENTEDITABLE
        if attrs.get("tag") in ("textarea", "input"):
            return EditorKind.TEXTAREA
        return EditorKind.UNKNOWN
