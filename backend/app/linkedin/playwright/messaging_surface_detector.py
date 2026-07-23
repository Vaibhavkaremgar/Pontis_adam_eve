"""messaging_surface_detector.py — Compose-first messaging surface detection.

Single responsibility: given a page after a Message click, determine whether
a usable messaging surface is open and return a structured result.

Detection priority (mirrors human perception):
  1. Compose overlay / drawer container
  2. Compose field (textarea / contenteditable / Lexical / DraftEditor)
  3. Full thread page (URL-based)
  4. Full messaging page (URL-based)
  5. Nothing found → opened=False

Never inspects dialog content.  Never makes business decisions.
Dialog inspection is the caller's responsibility.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Surface type constants
# ---------------------------------------------------------------------------

SURFACE_COMPOSE_OVERLAY  = "compose_overlay"
SURFACE_COMPOSE_DRAWER   = "compose_drawer"
SURFACE_COMPOSE_FIELD    = "compose_field"
SURFACE_THREAD_PAGE      = "thread_page"
SURFACE_MESSAGING_PAGE   = "messaging_page"
SURFACE_NONE             = "none"

# ---------------------------------------------------------------------------
# Selector catalogue — semantic only, no CSS class names
# ---------------------------------------------------------------------------

# Ordered: most specific → most generic
_COMPOSE_FIELD_CANDIDATES: list[tuple[str, str]] = [
    # Explicit message/compose aria-label
    ("[aria-label*='message' i][contenteditable='true']",   "aria_message_ce"),
    ("[aria-label*='message' i][role='textbox']",           "aria_message_textbox"),
    ("[aria-label*='compose' i][contenteditable='true']",   "aria_compose_ce"),
    ("[aria-label*='compose' i][role='textbox']",           "aria_compose_textbox"),
    ("[aria-label*='write' i][contenteditable='true']",     "aria_write_ce"),
    # Lexical / ProseMirror / DraftJS
    ("[data-lexical-editor][contenteditable='true']",       "lexical"),
    ("[data-lexical-editor='true']",                        "lexical_attr"),
    ("[class*='DraftEditor'][contenteditable='true']",      "draftjs"),
    ("[class*='public-DraftEditor'][contenteditable='true']", "draftjs_public"),
    (".ProseMirror[contenteditable='true']",                "prosemirror"),
    # Generic rich-text
    ("[aria-multiline='true'][contenteditable='true']",     "multiline_ce"),
    ("[role='textbox'][contenteditable='true']",            "textbox_ce"),
    ("[role='textbox']",                                    "textbox"),
    ("div[contenteditable='true']",                         "div_ce"),
    ("[contenteditable='true']",                            "ce"),
    # Placeholder-based
    ("[placeholder*='message' i]",                          "placeholder_message"),
    ("[placeholder*='write' i]",                            "placeholder_write"),
    ("[placeholder*='type' i]",                             "placeholder_type"),
    # Plain inputs
    ("textarea",                                            "textarea"),
    ("input[type='text']",                                  "input_text"),
]

_OVERLAY_SIGNALS: list[tuple[str, str]] = [
    ("[data-test-messaging-compose]",    SURFACE_COMPOSE_OVERLAY),
    (".msg-overlay-conversation-bubble", SURFACE_COMPOSE_OVERLAY),
    (".msg-overlay-bubble-header",       SURFACE_COMPOSE_OVERLAY),
    (".msg-form",                        SURFACE_COMPOSE_OVERLAY),
    ("[aria-label*='compose' i]",        SURFACE_COMPOSE_OVERLAY),
    ("[aria-label*='messaging' i]",      SURFACE_COMPOSE_OVERLAY),
    ("[data-test-messaging-drawer]",     SURFACE_COMPOSE_DRAWER),
    (".msg-convo-wrapper",               SURFACE_COMPOSE_DRAWER),
]

_NON_EDITABLE_TAGS: frozenset[str] = frozenset({
    "a", "button", "svg", "span", "img", "li", "ul", "ol", "nav",
    "header", "footer", "section", "article", "aside", "figure",
    "figcaption", "label", "legend", "fieldset", "select", "option",
    "optgroup", "datalist", "output", "progress", "meter", "details",
    "summary", "dialog", "menu", "menuitem", "canvas", "video", "audio",
    "source", "track", "map", "area", "table", "thead", "tbody", "tfoot",
    "tr", "th", "td", "caption", "colgroup", "col", "form", "hr", "br",
    "wbr", "code", "pre", "blockquote", "cite", "q", "abbr", "acronym",
    "address", "bdi", "bdo", "data", "dfn", "em", "i", "b", "strong",
    "s", "del", "ins", "mark", "small", "sub", "sup", "time", "u", "var",
    "kbd", "samp", "ruby", "rt", "rp", "picture", "iframe", "embed",
    "object", "param", "noscript", "script", "style", "link", "meta",
    "base", "title", "head", "html",
})

_NON_EDITABLE_ROLES: frozenset[str] = frozenset({
    "button", "link", "menuitem", "menuitemcheckbox", "menuitemradio",
    "tab", "option", "treeitem", "gridcell", "columnheader", "rowheader",
    "checkbox", "radio", "switch", "slider", "spinbutton",
    "scrollbar", "separator", "toolbar", "tooltip", "banner",
    "navigation", "main", "complementary", "contentinfo", "form",
    "search", "region", "alert", "alertdialog", "dialog",
    "status", "log", "marquee", "timer", "progressbar",
    "img", "figure", "list", "listitem", "group", "tree",
    "treegrid", "grid", "row", "table", "cell", "heading",
    "article", "document", "feed", "note", "presentation", "none",
})

_EDITABLE_INPUT_TYPES: frozenset[str] = frozenset({
    "text", "search", "email", "url", "tel", "number", "password", "",
})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class SurfaceDetectionResult:
    opened: bool = False
    surface_type: str = SURFACE_NONE
    selector: str = ""
    compose_type: str = ""          # e.g. "lexical", "textarea", "textbox"
    locator: Any = None
    confidence: str = "none"        # "high" | "medium" | "low" | "none"
    diagnostics: dict = field(default_factory=dict)

    def log(self) -> None:
        logger.info(
            "surface_detection opened=%s surface=%s selector=%r "
            "compose_type=%r confidence=%s",
            self.opened, self.surface_type, self.selector,
            self.compose_type, self.confidence,
        )
        for k, v in self.diagnostics.items():
            logger.debug("surface_diag %s=%r", k, v)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class MessagingSurfaceDetector:
    """Detect whether a usable messaging surface opened after a Message click.

    Compose-first: checks for editable fields before checking for dialogs.
    Never inspects dialog content — that is the caller's responsibility.
    """

    async def detect(
        self, page: Any, *, max_wait_ms: int = 8000
    ) -> SurfaceDetectionResult:
        """Poll until a messaging surface is found or timeout expires.

        Returns SurfaceDetectionResult.  Never raises.
        """
        deadline = time.monotonic() + max_wait_ms / 1000.0
        attempted_selectors: list[str] = []

        while time.monotonic() < deadline:
            result = await self._scan_once(page, attempted_selectors)
            if result.opened:
                result.log()
                return result
            await asyncio.sleep(0.15)

        diag = {"attempted_selectors": attempted_selectors}
        result = SurfaceDetectionResult(diagnostics=diag)
        result.log()
        return result

    async def _scan_once(
        self, page: Any, attempted_selectors: list[str]
    ) -> SurfaceDetectionResult:
        url = str(getattr(page, "url", ""))

        # ── 1. Thread URL ─────────────────────────────────────────────────────
        if "/messaging/thread/" in url or "/messaging/compose/" in url:
            return SurfaceDetectionResult(
                opened=True,
                surface_type=SURFACE_THREAD_PAGE,
                confidence="high",
                diagnostics={"url": url},
            )

        # ── 2. Messaging page URL ─────────────────────────────────────────────
        if "/messaging/new/" in url or "/messaging/?compose" in url:
            return SurfaceDetectionResult(
                opened=True,
                surface_type=SURFACE_MESSAGING_PAGE,
                confidence="high",
                diagnostics={"url": url},
            )

        # ── 3. Compose surface container ──────────────────────────────────────
        for sel, surface_type in _OVERLAY_SIGNALS:
            try:
                if await page.locator(sel).first.is_visible():
                    # Container found — now find the actual field inside it
                    container = page.locator(sel).first
                    field_result = await self._find_field_in(container, page)
                    return SurfaceDetectionResult(
                        opened=True,
                        surface_type=surface_type,
                        selector=field_result.get("selector", sel),
                        compose_type=field_result.get("compose_type", ""),
                        locator=field_result.get("locator"),
                        confidence="high",
                        diagnostics={"container_selector": sel, "url": url},
                    )
            except Exception:
                continue

        # ── 4. Compose field directly on page ─────────────────────────────────
        for sel, compose_type in _COMPOSE_FIELD_CANDIDATES:
            if sel not in attempted_selectors:
                attempted_selectors.append(sel)
            try:
                locs = page.locator(sel)
                count = await locs.count()
            except Exception:
                continue

            for i in range(min(count, 8)):
                item = locs.nth(i)
                if not await self._is_usable_field(item):
                    continue
                logger.info(
                    "surface_detector compose_field_found selector=%s idx=%d compose_type=%s",
                    sel, i, compose_type,
                )
                return SurfaceDetectionResult(
                    opened=True,
                    surface_type=SURFACE_COMPOSE_FIELD,
                    selector=sel,
                    compose_type=compose_type,
                    locator=item,
                    confidence="medium",
                    diagnostics={"url": url, "field_index": i},
                )

        return SurfaceDetectionResult(diagnostics={"url": url})

    async def _find_field_in(self, container: Any, page: Any) -> dict:
        """Find the first usable compose field inside a container."""
        for sel, compose_type in _COMPOSE_FIELD_CANDIDATES:
            try:
                locs = container.locator(sel)
                count = await locs.count()
                for i in range(min(count, 5)):
                    item = locs.nth(i)
                    if await self._is_usable_field(item):
                        return {"locator": item, "selector": sel, "compose_type": compose_type}
            except Exception:
                continue
        return {}

    async def _is_usable_field(self, item: Any) -> bool:
        """Return True if the element is a visible, editable compose field."""
        try:
            attrs = await item.evaluate(
                """el => ({
                    tag:        el.tagName.toLowerCase(),
                    role:       (el.getAttribute('role') || '').toLowerCase(),
                    ce:         el.getAttribute('contenteditable'),
                    readonly:   el.getAttribute('readonly'),
                    disabled:   el.disabled,
                    input_type: (el.getAttribute('type') || '').toLowerCase(),
                })"""
            )
        except Exception:
            return False

        tag: str = attrs["tag"]
        role: str = attrs["role"]
        ce = attrs["ce"]

        if tag in _NON_EDITABLE_TAGS:
            return False
        if role and role in _NON_EDITABLE_ROLES:
            return False

        if tag == "textarea":
            structurally_editable = True
        elif tag == "input":
            structurally_editable = attrs["input_type"] in _EDITABLE_INPUT_TYPES
        else:
            structurally_editable = ce in ("true", "")

        if not structurally_editable:
            return False
        if attrs["readonly"] in ("true", "readonly", ""):
            return False
        if attrs["disabled"]:
            return False

        try:
            if not await item.is_visible():
                return False
        except Exception:
            return False

        try:
            return bool(await item.is_editable(timeout=400))
        except Exception:
            return False
