"""compose_locator.py — layout-agnostic compose field discovery.

Supports every LinkedIn messaging surface:
  overlay, drawer, full messaging page, conversation page, future layouts.

Never assumes a single layout.  Searches by semantic attributes only.
No CSS class selectors.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

LAYOUT_OVERLAY = "overlay"
LAYOUT_DRAWER = "drawer"
LAYOUT_MESSAGING_PAGE = "messaging_page"
LAYOUT_CONVERSATION = "conversation"
LAYOUT_DIALOG = "dialog"
LAYOUT_UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# Selector priority list — semantic only, no CSS classes
# ---------------------------------------------------------------------------

# Each entry: (selector, layout_hint)
# Tried in order; first visible + editable wins.
_COMPOSE_CANDIDATES: list[tuple[str, str]] = [
    # ── Explicit message compose containers ──────────────────────────────────
    ("[aria-label*='message' i][contenteditable='true']",   LAYOUT_UNKNOWN),
    ("[aria-label*='message' i][role='textbox']",           LAYOUT_UNKNOWN),
    ("[aria-label*='compose' i][contenteditable='true']",   LAYOUT_UNKNOWN),
    ("[aria-label*='compose' i][role='textbox']",           LAYOUT_UNKNOWN),
    ("[aria-label*='write' i][contenteditable='true']",     LAYOUT_UNKNOWN),
    ("[aria-label*='write' i][role='textbox']",             LAYOUT_UNKNOWN),
    # ── Lexical / ProseMirror / DraftJS rich-text editors ────────────────────
    ("[data-lexical-editor][contenteditable='true']",       LAYOUT_UNKNOWN),
    ("[data-lexical-editor='true']",                        LAYOUT_UNKNOWN),
    (".ProseMirror[contenteditable='true']",                LAYOUT_UNKNOWN),
    ("[class*='prosemirror'][contenteditable='true']",      LAYOUT_UNKNOWN),
    ("[class*='DraftEditor'][contenteditable='true']",      LAYOUT_UNKNOWN),
    ("[class*='public-DraftEditor'][contenteditable='true']", LAYOUT_UNKNOWN),
    # ── Generic rich-text ────────────────────────────────────────────────────
    ("[aria-multiline='true'][contenteditable='true']",     LAYOUT_UNKNOWN),
    ("[role='textbox'][contenteditable='true']",            LAYOUT_UNKNOWN),
    ("[role='textbox']",                                    LAYOUT_UNKNOWN),
    ("div[contenteditable='true']",                         LAYOUT_UNKNOWN),
    ("[contenteditable='true']",                            LAYOUT_UNKNOWN),
    # ── Placeholder-based ────────────────────────────────────────────────────
    ("[placeholder*='message' i]",                          LAYOUT_UNKNOWN),
    ("[placeholder*='write' i]",                            LAYOUT_UNKNOWN),
    ("[placeholder*='type' i]",                             LAYOUT_UNKNOWN),
    # ── Plain inputs / textareas ─────────────────────────────────────────────
    ("textarea",                                            LAYOUT_UNKNOWN),
    ("input[type='text']",                                  LAYOUT_UNKNOWN),
]

# Tags / roles that are structurally never editable
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

# Layout detection — checked after a compose field is found
_LAYOUT_SIGNALS: list[tuple[str, str]] = [
    ("[data-test-messaging-compose]",       LAYOUT_OVERLAY),
    (".msg-overlay-conversation-bubble",    LAYOUT_OVERLAY),
    (".msg-overlay-bubble-header",          LAYOUT_OVERLAY),
    ("[data-test-messaging-drawer]",        LAYOUT_DRAWER),
    (".msg-convo-wrapper",                  LAYOUT_DRAWER),
    ("[role='dialog']",                     LAYOUT_DIALOG),
    ("[role='alertdialog']",                LAYOUT_DIALOG),
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ComposeLocatorResult:
    locator: Any | None = None
    selector: str = ""
    layout: str = LAYOUT_UNKNOWN
    html_snippet: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.locator is not None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ComposeLocator:
    """Locate the editable compose field in any LinkedIn messaging layout."""

    async def locate(self, page: Any) -> ComposeLocatorResult:
        """Return the first visible, editable compose field found on *page*.

        Tries every candidate selector in priority order.
        Returns ComposeLocatorResult with found=False if nothing is found.
        """
        for selector, _ in _COMPOSE_CANDIDATES:
            try:
                locs = page.locator(selector)
                count = await locs.count()
            except Exception:
                continue

            for i in range(min(count, 10)):
                item = locs.nth(i)
                result = await self._evaluate_candidate(item, selector, page)
                if result is not None:
                    return result

        logger.debug("compose_locator exhausted all selectors")
        return ComposeLocatorResult()

    async def _evaluate_candidate(
        self, item: Any, selector: str, page: Any
    ) -> ComposeLocatorResult | None:
        try:
            attrs = await item.evaluate(
                """el => ({
                    tag:           el.tagName.toLowerCase(),
                    role:          (el.getAttribute('role') || '').toLowerCase(),
                    ce:            el.getAttribute('contenteditable'),
                    readonly_attr: el.getAttribute('readonly'),
                    disabled_prop: el.disabled,
                    input_type:    (el.getAttribute('type') || '').toLowerCase(),
                    aria_label:    el.getAttribute('aria-label') || '',
                    placeholder:   el.getAttribute('placeholder') || '',
                    snippet:       el.outerHTML.slice(0, 300)
                })"""
            )
        except Exception:
            return None

        tag: str = attrs["tag"]
        role: str = attrs["role"]
        ce = attrs["ce"]
        readonly_attr = attrs["readonly_attr"]
        disabled_prop: bool = bool(attrs["disabled_prop"])
        input_type: str = attrs["input_type"]

        if tag in _NON_EDITABLE_TAGS:
            return None
        if role and role in _NON_EDITABLE_ROLES:
            return None

        if tag == "textarea":
            structurally_editable = True
        elif tag == "input":
            structurally_editable = input_type in _EDITABLE_INPUT_TYPES
        else:
            structurally_editable = ce in ("true", "")

        if not structurally_editable:
            return None
        if readonly_attr in ("true", "readonly", ""):
            return None
        if disabled_prop:
            return None

        try:
            if not await item.is_visible():
                return None
        except Exception:
            return None

        try:
            is_editable = await item.is_editable(timeout=500)
        except Exception:
            is_editable = False
        if not is_editable:
            return None

        layout = await self._detect_layout(page)
        logger.info(
            "compose_locator FOUND selector=%s tag=%s role=%r ce=%r layout=%s",
            selector, tag, role, ce, layout,
        )
        return ComposeLocatorResult(
            locator=item,
            selector=selector,
            layout=layout,
            html_snippet=attrs["snippet"],
            metadata={
                "tag": tag,
                "role": role,
                "contenteditable": ce,
                "aria_label": attrs["aria_label"],
                "placeholder": attrs["placeholder"],
            },
        )

    async def _detect_layout(self, page: Any) -> str:
        """Infer which LinkedIn messaging surface is currently open."""
        try:
            url = str(getattr(page, "url", ""))
            if "/messaging/thread/" in url or "/messaging/compose/" in url:
                return LAYOUT_MESSAGING_PAGE
            if "/messaging/" in url:
                return LAYOUT_CONVERSATION
        except Exception:
            pass

        for selector, layout in _LAYOUT_SIGNALS:
            try:
                loc = page.locator(selector).first
                if await loc.is_visible():
                    return layout
            except Exception:
                continue

        return LAYOUT_UNKNOWN
