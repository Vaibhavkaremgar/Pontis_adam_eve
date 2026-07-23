"""action_discovery.py — single source of truth for all LinkedIn Playwright selectors.

Every worker and inspector must import from here.  No selector logic lives
anywhere else.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.linkedin.playwright.profile_types import ProfileCapabilities

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical selector lists — the ONLY place these exist in the codebase
# ---------------------------------------------------------------------------

_INTERACTIVE_SELECTORS: list[str] = [
    "button",
    "[role='button']",
    "a[role='button']",
    "[aria-label]",
    "a[href^='/messaging/']",
]

_HEADER_SELECTORS: list[str] = [
    "[data-view-name*='profile'] header",
    "main header",
    "main section",
    "main",
]

_PRIMARY_ACTION_SELECTORS: list[str] = [
    "[data-view-name*='profile-actions']",
    ".pvs-profile-actions",
    ".pv-top-card-v2-ctas",
    ".pv-top-card--list-bullet",
    ".ph5 .pvs-profile-actions",
    "[data-view-name*='profile'] .pvs-profile-actions",
    "[data-view-name*='profile'] [data-view-name*='actions']",
    "[role='toolbar']",
]

_COMPOSE_SELECTORS: list[str] = [
    "textarea",
    "input[type='text']",
    "div[contenteditable='true']",
    "[contenteditable='true']",
    "[role='textbox']",
    "[aria-label*='message' i][contenteditable]",
    "[aria-label*='message' i][role='textbox']",
    "[placeholder*='message' i]",
    "[placeholder*='write' i]",
    "[placeholder*='type' i]",
    "[aria-multiline='true'][contenteditable]",
    "[data-lexical-editor][contenteditable]",
]

_SEND_SELECTORS: list[str] = [
    "button:has-text('Send')",
    "[aria-label*='Send' i]",
    "button[type='submit']",
    "[data-control-name*='send' i]",
]

_NON_EDITABLE_TAGS: frozenset[str] = frozenset(
    {"a", "button", "svg", "span", "img", "li", "ul", "ol", "nav",
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
     "base", "title", "head", "html"}
)

_NON_EDITABLE_ROLES: frozenset[str] = frozenset(
    {"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio",
     "tab", "option", "treeitem", "gridcell", "columnheader", "rowheader",
     "checkbox", "radio", "switch", "slider", "spinbutton",
     "scrollbar", "separator", "toolbar", "tooltip", "banner",
     "navigation", "main", "complementary", "contentinfo", "form",
     "search", "region", "alert", "alertdialog", "dialog",
     "status", "log", "marquee", "timer", "progressbar",
     "img", "figure", "list", "listitem", "group", "tree",
     "treegrid", "grid", "row", "table", "cell", "heading",
     "article", "document", "feed", "note", "presentation", "none"}
)

_EDITABLE_INPUT_TYPES: frozenset[str] = frozenset(
    {"text", "search", "email", "url", "tel", "number", "password", ""}
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _is_visible(locator: Any) -> bool:
    try:
        return bool(await locator.is_visible())
    except Exception:
        return False


async def _read_label(locator: Any) -> str:
    for method_name in ("inner_text", "text_content"):
        try:
            value = await getattr(locator, method_name)(timeout=600)
            text = str(value or "").strip()
            if text:
                return text
        except Exception:
            continue
    try:
        return str(await locator.get_attribute("aria-label") or "").strip()
    except Exception:
        return ""


def _normalize(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(label or "")).strip()
    return cleaned.strip(" .,-\u2022\u00b7\u2013\u2014")


async def _action_score(container: Any) -> int:
    score = 0
    for sel in _INTERACTIVE_SELECTORS:
        try:
            locs = container.locator(sel)
            count = await locs.count()
            for i in range(min(count, 10)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                label = _normalize(await _read_label(item)).lower()
                if any(t in label for t in ("connect", "message", "follow", "more", "pending", "request sent", "withdraw")):
                    score += 1
        except Exception:
            continue
    return score


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def find_toolbar(page: Any) -> Any | None:
    """Return the primary action toolbar locator, or None.

    Tiered discovery:
      1. Primary selectors scoped to profile header.
      2. Page-wide primary selectors.
      3. Profile header itself as fallback.
    """
    profile_header = await _find_profile_header(page)

    # Tier 1 — scoped to header
    if profile_header is not None:
        for sel in _PRIMARY_ACTION_SELECTORS:
            try:
                loc = profile_header.locator(sel).first
                if await _is_visible(loc) and await _action_score(loc) > 0:
                    logger.debug("find_toolbar tier=1 selector=%s", sel)
                    return loc
            except Exception:
                continue

    # Tier 2 — page-wide
    for sel in _PRIMARY_ACTION_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await _is_visible(loc) and await _action_score(loc) > 0:
                logger.debug("find_toolbar tier=2 selector=%s", sel)
                return loc
        except Exception:
            continue

    # Tier 3 — header itself
    if profile_header is not None:
        try:
            if await _is_visible(profile_header) and await _action_score(profile_header) > 0:
                logger.debug("find_toolbar tier=3 selector=profile_header")
                return profile_header
        except Exception:
            pass

    return None


async def find_primary_actions(page: Any) -> list[dict[str, str]]:
    """Return all visible interactive items from the primary action toolbar.

    Each item is a dict with keys: label, aria_label, accessible_name.
    """
    toolbar = await find_toolbar(page)
    if toolbar is None:
        return []
    items: list[dict[str, str]] = []
    for sel in _INTERACTIVE_SELECTORS:
        try:
            locator = toolbar.locator(sel)
            count = await locator.count()
        except Exception:
            continue
        for index in range(min(count, 20)):
            try:
                item = locator.nth(index)
                if not await _is_visible(item):
                    continue
                label = await _read_label(item)
                if label:
                    aria = str(await item.get_attribute("aria-label") or "").strip()
                    items.append({"label": label, "aria_label": aria, "accessible_name": aria})
            except Exception:
                continue
    return items


async def find_connect_action(page: Any) -> Any | None:
    """Return the visible Connect button/menu-item locator, or None.

    Strategy:
      1. Scan the primary toolbar for a direct Connect button.
      2. If not found, click the More (3-dots) button and scan the dropdown.
    """
    toolbar = await find_toolbar(page)
    if toolbar is None:
        return None

    # Strategy 1 — direct Connect button in toolbar
    connect = await _find_connect_in_container(toolbar)
    if connect is not None:
        logger.info("find_connect_action found=direct")
        return connect

    # Strategy 2 — Connect hidden inside More dropdown
    connect = await _find_connect_via_more(page, toolbar)
    if connect is not None:
        logger.info("find_connect_action found=more_dropdown")
        return connect

    return None


async def find_message_action(page: Any) -> Any | None:
    """Return the visible Message button locator, or None.

    Supports both <a href="/messaging/..."> and button-based layouts.
    """
    toolbar = await find_toolbar(page)
    if toolbar is None:
        return None
    for sel in _INTERACTIVE_SELECTORS:
        try:
            locs = toolbar.locator(sel)
            count = await locs.count()
            for i in range(min(count, 20)):
                item = locs.nth(i)
                try:
                    if not await _is_visible(item):
                        continue
                    label = _normalize(await _read_label(item)).lower()
                    if label == "message" or label.startswith("message "):
                        logger.debug("find_message_action found selector=%s", sel)
                        return item
                except Exception:
                    continue
        except Exception:
            continue
    return None


async def find_more_action(page: Any) -> Any | None:
    """Return the visible More/overflow button locator, or None."""
    toolbar = await find_toolbar(page)
    if toolbar is None:
        return None
    for selector in ["button[aria-label*='More']", "button", "[role='button']"]:
        try:
            locator = toolbar.locator(selector)
            count = await locator.count()
            for index in range(count):
                item = locator.nth(index)
                if not await _is_visible(item):
                    continue
                label = await _read_label(item)
                if "more" in label.lower():
                    logger.debug("find_more_action found selector=%s", selector)
                    return item
        except Exception:
            continue
    return None


async def find_compose_editor(page: Any) -> dict[str, Any]:
    """Return compose editor info dict with keys: locator, selector, metadata.

    Detects: textarea, contenteditable, Lexical editor, role=textbox,
    input[type=text], placeholder variants.

    Returns {"locator": None, "selector": "", "metadata": {}} if not found.
    """
    for sel in _COMPOSE_SELECTORS:
        logger.debug("find_compose_editor selector=%s", sel)
        try:
            locs = page.locator(sel)
            count = await locs.count()
        except Exception:
            continue

        for i in range(min(count, 10)):
            item = locs.nth(i)
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
                        snippet:       el.outerHTML.slice(0, 200)
                    })"""
                )
                tag: str = attrs["tag"]
                role: str = attrs["role"]
                ce = attrs["ce"]
                readonly_attr = attrs["readonly_attr"]
                disabled_prop: bool = bool(attrs["disabled_prop"])
                input_type: str = attrs["input_type"]

                if tag in _NON_EDITABLE_TAGS:
                    continue
                if role and role in _NON_EDITABLE_ROLES:
                    continue

                if tag == "textarea":
                    structurally_editable = True
                elif tag == "input":
                    structurally_editable = input_type in _EDITABLE_INPUT_TYPES
                else:
                    structurally_editable = ce in ("true", "")

                if not structurally_editable:
                    continue
                if readonly_attr in ("true", "readonly", ""):
                    continue
                if disabled_prop:
                    continue
                if not await _is_visible(item):
                    continue

                try:
                    is_editable = await item.is_editable(timeout=500)
                except Exception:
                    is_editable = False
                if not is_editable:
                    continue

                logger.info(
                    "find_compose_editor ACCEPTED selector=%s idx=%d tag=%s role=%r ce=%r",
                    sel, i, tag, role, ce,
                )
                return {
                    "locator": item,
                    "selector": sel,
                    "metadata": {
                        "tag": tag,
                        "role": role,
                        "contenteditable": ce,
                        "aria_label": attrs["aria_label"],
                        "placeholder": attrs["placeholder"],
                    },
                }
            except Exception:
                continue

    logger.debug("find_compose_editor exhausted all selectors")
    return {"locator": None, "selector": "", "metadata": {}}


async def find_send_action(page: Any) -> Any | None:
    """Return the first visible, enabled Send button locator, or None.

    Supports: button text, accessible name, aria-label, submit button,
    role=button.  Never uses LinkedIn CSS classes.
    """
    for sel in _SEND_SELECTORS:
        logger.debug("find_send_action selector=%s", sel)
        try:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(min(count, 10)):
                item = locs.nth(i)
                try:
                    if not await _is_visible(item):
                        continue
                    disabled = await item.evaluate("el => el.disabled || false")
                    if disabled:
                        continue
                    label = str(await item.evaluate(
                        "el => (el.getAttribute('aria-label') || el.innerText || '').trim().slice(0,80)"
                    ))
                    logger.info("find_send_action ACCEPTED selector=%s label=%r", sel, label)
                    return item
                except Exception:
                    continue
        except Exception:
            continue

    # Fallback: role=button with accessible name "Send"
    try:
        loc = page.get_by_role("button", name=re.compile(r"^send$", re.I))
        count = await loc.count()
        for i in range(min(count, 5)):
            item = loc.nth(i)
            if await _is_visible(item):
                disabled = await item.evaluate("el => el.disabled || false")
                if not disabled:
                    logger.info("find_send_action ACCEPTED selector=role:button[name=Send]")
                    return item
    except Exception:
        pass

    return None


async def build_capabilities(
    page: Any,
    *,
    body_text: str = "",
    page_url: str = "",
    login_required: bool = False,
    session_expired: bool = False,
    profile_not_found: bool = False,
    profile_private: bool = False,
) -> ProfileCapabilities:
    """Build a ProfileCapabilities model from the live page.

    Reads every visible toolbar label independently.
    Flags are NOT inferred from each other.

    connected=True only when explicit evidence is present:
      - "Remove connection" label
      - "1st" in body_text
      - "Connected" in body_text
    Never set connected=True because can_message=True.
    """
    # Blocked states — no toolbar reading needed.
    if login_required or session_expired or profile_not_found or profile_private:
        return ProfileCapabilities(
            login_required=login_required,
            session_expired=session_expired,
            profile_not_found=profile_not_found,
            profile_private=profile_private,
        )

    # Collect all visible toolbar labels.
    raw_labels: list[str] = []
    toolbar = await find_toolbar(page)
    if toolbar is not None:
        for sel in _INTERACTIVE_SELECTORS:
            try:
                locs = toolbar.locator(sel)
                count = await locs.count()
                for i in range(min(count, 20)):
                    item = locs.nth(i)
                    try:
                        if not await _is_visible(item):
                            continue
                        label = _normalize(await _read_label(item))
                        if label and label not in raw_labels:
                            raw_labels.append(label)
                    except Exception:
                        continue
            except Exception:
                continue

    labels_lower = {lbl.lower() for lbl in raw_labels}

    # --- pending: explicit pending/withdraw signals ---
    pending = any(
        "pending" in lbl
        or "request sent" in lbl
        or "invitation sent" in lbl
        or "withdraw invitation" in lbl
        for lbl in labels_lower
    )

    # --- connected: ONLY explicit evidence ---
    # "Remove connection" in toolbar OR degree/connected badge in body text.
    connected_by_label = any(
        "remove connection" in lbl or lbl == "remove"
        for lbl in labels_lower
    )
    body_lower = body_text.lower()
    connected_by_body = (
        "1st" in body_lower
        or "connected" in body_lower
        or "1st degree" in body_lower
    )
    connected = connected_by_label or connected_by_body
    connection_verified = connected_by_label  # label evidence is stronger

    # --- individual capabilities — independent of each other ---
    can_connect = any(
        lbl == "connect" or lbl.startswith("connect ")
        for lbl in labels_lower
    )
    can_message = any(
        lbl == "message" or lbl.startswith("message ") or "inmail" in lbl
        for lbl in labels_lower
    )
    can_follow = any("follow" in lbl for lbl in labels_lower)
    has_more = any(
        lbl == "more" or lbl.startswith("more ")
        for lbl in labels_lower
    )

    caps = ProfileCapabilities(
        can_connect=can_connect,
        can_message=can_message,
        can_follow=can_follow,
        has_more=has_more,
        pending=pending,
        connected=connected,
        connection_verified=connection_verified,
        login_required=login_required,
        session_expired=session_expired,
        profile_not_found=profile_not_found,
        profile_private=profile_private,
        raw_labels=raw_labels,
    )
    logger.info(
        "build_capabilities page_url=%s "
        "can_connect=%s can_message=%s can_follow=%s has_more=%s "
        "pending=%s connected=%s connection_verified=%s labels=%s",
        page_url,
        caps.can_connect, caps.can_message, caps.can_follow, caps.has_more,
        caps.pending, caps.connected, caps.connection_verified,
        raw_labels,
    )
    return caps


# ---------------------------------------------------------------------------
# Internal helpers (not part of public API)
# ---------------------------------------------------------------------------


async def close_messaging_overlays(page: Any) -> None:
    """Close all messaging overlay panels that may cover the profile page.

    Targets every overlay variant LinkedIn renders:
      - .msg-overlay-list-bubble  (the full chat list panel)
      - .msg-overlay-conversation-bubble  (individual conversation bubbles)
      - .msg-overlay-window  (detached chat windows)

    Uses the close/dismiss button inside each overlay.  Non-fatal — any
    individual failure is silently skipped.
    """
    import asyncio as _asyncio
    _OVERLAY_SELECTORS = [
        ".msg-overlay-list-bubble",
        ".msg-overlay-conversation-bubble",
        ".msg-overlay-window",
    ]
    _CLOSE_BUTTON_SELECTORS = [
        "button[aria-label*='Close' i]",
        "button[aria-label*='Dismiss' i]",
        "button.msg-overlay-bubble-header__control",
        "button.msg-overlay-list-bubble__close-btn",
    ]
    closed = 0
    for overlay_sel in _OVERLAY_SELECTORS:
        try:
            overlays = page.locator(overlay_sel)
            count = await overlays.count()
            for i in range(count):
                overlay = overlays.nth(i)
                if not await _is_visible(overlay):
                    continue
                clicked = False
                # Try aria-label / class selectors first
                for btn_sel in _CLOSE_BUTTON_SELECTORS:
                    try:
                        btn = overlay.locator(btn_sel).first
                        if await btn.is_visible():
                            await btn.click(timeout=3000)
                            closed += 1
                            await _asyncio.sleep(0.2)
                            clicked = True
                            break
                    except Exception:
                        continue
                if clicked:
                    continue
                # Fallback: any button whose inner_text starts with "Close"
                try:
                    btns = overlay.locator("button")
                    btn_count = await btns.count()
                    for j in range(btn_count):
                        btn = btns.nth(j)
                        if not await btn.is_visible():
                            continue
                        txt = str(await btn.inner_text(timeout=400) or "").strip()
                        if txt.lower().startswith("close"):
                            await btn.click(timeout=3000)
                            closed += 1
                            await _asyncio.sleep(0.2)
                            break
                except Exception:
                    pass
        except Exception:
            continue
    if closed:
        logger.info("close_messaging_overlays closed=%d", closed)


async def _find_connect_in_container(container: Any) -> Any | None:
    """Scan a container for a visible Connect button/item."""
    for sel in _INTERACTIVE_SELECTORS:
        try:
            locs = container.locator(sel)
            count = await locs.count()
            for i in range(min(count, 20)):
                item = locs.nth(i)
                try:
                    if not await _is_visible(item):
                        continue
                    label = _normalize(await _read_label(item)).lower()
                    if label == "connect" or label.startswith("connect "):
                        return item
                except Exception:
                    continue
        except Exception:
            continue
    return None


async def _find_connect_via_more(page: Any, toolbar: Any) -> Any | None:
    """Click the More (3-dots) button in the toolbar and look for Connect in the dropdown.

    Returns the Connect menu item locator (already visible in the open dropdown),
    or None if More button not found or Connect not in the menu.
    Leaves the dropdown open so the caller can click the returned locator.
    """
    import asyncio as _asyncio
    # Find the More button
    more_btn = None
    for sel in ["button[aria-label*='More' i]", "button", "[role='button']"]:
        try:
            locs = toolbar.locator(sel)
            count = await locs.count()
            for i in range(min(count, 20)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                label = _normalize(await _read_label(item)).lower()
                if label == "more" or label.startswith("more "):
                    more_btn = item
                    break
        except Exception:
            continue
        if more_btn is not None:
            break

    if more_btn is None:
        return None

    try:
        await more_btn.click(timeout=5000)
        logger.info("find_connect_via_more more_clicked")
    except Exception as exc:
        logger.debug("find_connect_via_more more_click_failed: %s", exc)
        return None

    await _asyncio.sleep(0.4)

    # Scan dropdown menus / listboxes for Connect item
    for dropdown_sel in ["[role='menu']", "[role='listbox']", "[role='dialog']", "[aria-modal='true']"]:
        try:
            menus = page.locator(dropdown_sel)
            count = await menus.count()
            for i in range(count):
                menu = menus.nth(i)
                if not await _is_visible(menu):
                    continue
                connect = await _find_connect_in_container(menu)
                if connect is not None:
                    return connect
        except Exception:
            continue

    logger.info("find_connect_via_more connect_not_in_dropdown")
    return None


async def _find_profile_header(page: Any) -> Any | None:
    for sel in _HEADER_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await _is_visible(loc):
                return loc
        except Exception:
            continue
    return None
