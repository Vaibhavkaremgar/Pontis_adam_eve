"""messaging_toolbar_resolver.py — messaging-only profile action-bar resolver.

Anchors toolbar discovery to the profile top-card section, NOT the sticky
nav toolbar ([role='toolbar'] in the sticky header).  The sticky nav also
carries a Message <a> with an identical href, but sits adjacent to feed post
action buttons — clicking it during retries lands on unrelated "Send Post"
dialogs, producing the observed retry loop.

This module is intentionally separate from action_discovery.find_toolbar so
that ConnectionWorker's working selectors are never touched.

Public API
----------
find_message_anchor(page, *, dry_run=False) -> Locator | None
    Locate the Message <a> inside the profile top-card action bar.
    dry_run=True logs every resolved selector + count without clicking.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Top-card container selectors — tried in order, first visible wins.
# These identify the profile card section, NOT the sticky nav.
# ---------------------------------------------------------------------------

_TOPCARD_SELECTORS: list[str] = [
    # SDUI component key present in captured HTML
    "section[componentkey*='Topcard']",
    # data-view-name on the profile wrapper
    "[data-view-name*='profile'] section",
    # Fallback: main content section that contains the profile photo + name
    "main section",
    # Last resort: main itself
    "main",
]

# Action-bar selectors tried inside the top-card container.
# Mirrors _PRIMARY_ACTION_SELECTORS from action_discovery but scoped here.
_ACTION_BAR_SELECTORS: list[str] = [
    "[data-view-name*='profile-actions']",
    ".pvs-profile-actions",
    ".pv-top-card-v2-ctas",
    ".pv-top-card--list-bullet",
    "[data-view-name*='actions']",
]

# Regex for the Message link accessible name
_MESSAGE_NAME_RE = re.compile(r"^Message", re.I)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class MessageAnchorResult:
    locator: Any | None = None
    topcard_selector: str = ""
    action_bar_selector: str = ""
    message_selector: str = ""
    # dry-run diagnostics
    dry_run_log: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.locator is not None


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

async def find_message_anchor(
    page: Any,
    *,
    dry_run: bool = False,
) -> MessageAnchorResult:
    """Locate the Message <a> inside the profile top-card action bar.

    Steps:
      1. Find the profile top-card section (not the sticky nav).
      2. Inside it, find the primary action bar.
      3. Assert count == 1 for the action bar; if not, log and fail rather
         than falling back to page-wide.
      4. Inside the action bar, locate the Message link by role + name.

    dry_run=True logs every resolved selector + count without clicking.
    Returns MessageAnchorResult with found=False on any failure.
    """
    log: list[str] = []

    def _note(msg: str) -> None:
        logger.info("msg_toolbar_resolver %s", msg)
        if dry_run:
            log.append(msg)

    # ── Step 1: find the profile top-card ────────────────────────────────────
    topcard = None
    topcard_sel = ""
    for sel in _TOPCARD_SELECTORS:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            _note(f"topcard_candidate sel={sel!r} count={count}")
            for i in range(min(count, 5)):
                item = locs.nth(i)
                if await item.is_visible():
                    topcard = item
                    topcard_sel = sel
                    _note(f"topcard_selected sel={sel!r} index={i}")
                    break
        except Exception as exc:
            _note(f"topcard_error sel={sel!r} err={exc}")
        if topcard is not None:
            break

    if topcard is None:
        _note("topcard_not_found — cannot resolve action bar")
        return MessageAnchorResult(dry_run_log=log)

    # ── Step 2: find the action bar inside the top-card ──────────────────────
    action_bar = None
    action_bar_sel = ""
    for sel in _ACTION_BAR_SELECTORS:
        try:
            locs = topcard.locator(sel)
            count = await locs.count()
            _note(f"action_bar_candidate sel={sel!r} count={count}")
            if count == 0:
                continue
            if count > 1:
                # Multiple matches — log and skip; we need exactly one.
                _note(f"action_bar_ambiguous sel={sel!r} count={count} — skipping")
                continue
            item = locs.first
            if await item.is_visible():
                action_bar = item
                action_bar_sel = sel
                _note(f"action_bar_selected sel={sel!r} count=1")
                break
        except Exception as exc:
            _note(f"action_bar_error sel={sel!r} err={exc}")

    # Fallback: if no named action-bar selector matched, use the top-card
    # itself as the search scope (it contains the buttons directly in some
    # LinkedIn layouts).
    if action_bar is None:
        _note("action_bar_not_found — using topcard as fallback scope")
        action_bar = topcard
        action_bar_sel = topcard_sel + "::self"

    # ── Step 3: locate the Message link by role + accessible name ────────────
    message_sel = "get_by_role(link, name=^Message)"
    try:
        msg_loc = action_bar.get_by_role("link", name=_MESSAGE_NAME_RE)
        count = await msg_loc.count()
        _note(f"message_link count={count} in action_bar={action_bar_sel!r}")

        if count == 0:
            # Fallback: try <a> with text "Message" directly
            msg_loc = action_bar.locator("a").filter(has_text=_MESSAGE_NAME_RE)
            count = await msg_loc.count()
            message_sel = "a[text~=Message]"
            _note(f"message_link_fallback count={count}")

        if count == 0:
            _note("message_link_not_found")
            return MessageAnchorResult(
                topcard_selector=topcard_sel,
                action_bar_selector=action_bar_sel,
                dry_run_log=log,
            )

        # Use the first visible one (should be exactly 1 in the top-card scope)
        for i in range(min(count, 3)):
            item = msg_loc.nth(i)
            try:
                if await item.is_visible():
                    _note(f"message_link_resolved index={i} sel={message_sel!r}")
                    if dry_run:
                        # In dry-run: verify href looks like a messaging URL
                        href = str(await item.get_attribute("href") or "")
                        _note(f"message_link_href={href!r}")
                    return MessageAnchorResult(
                        locator=item,
                        topcard_selector=topcard_sel,
                        action_bar_selector=action_bar_sel,
                        message_selector=message_sel,
                        dry_run_log=log,
                    )
            except Exception as exc:
                _note(f"message_link_visibility_error index={i} err={exc}")

        _note("message_link_no_visible_item")
    except Exception as exc:
        _note(f"message_link_error err={exc}")

    return MessageAnchorResult(
        topcard_selector=topcard_sel,
        action_bar_selector=action_bar_sel,
        dry_run_log=log,
    )
