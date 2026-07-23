"""reply_sync_service.py — LinkedIn reply synchronization.

For a given LinkedIn account, opens each persisted conversation in the
browser, reads the latest messages, and persists any that are not already
stored.

Public API:
    sync_replies(account_id) -> ReplySyncSummary

Design principles:
- Reuses BrowserManager, HumanInteraction, NavigationTracker.
- No selector logic duplicated from other modules.
- Deduplication is DB-first: linkedin_message_id is the primary key.
  Text+timestamp window is the fallback when LinkedIn does not expose IDs.
- Never raises — all per-conversation failures are caught and counted.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypedDict
from uuid import uuid4

from app.linkedin.playwright.browser_manager import BrowserManager
from app.linkedin.playwright.human_interaction import human_scroll, wait_after_action

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LinkedIn messaging page selectors — semantic only, no CSS classes
# ---------------------------------------------------------------------------

# The main messaging inbox / thread list
_MESSAGING_URL = "https://www.linkedin.com/messaging/"

# Conversation list items in the left panel
_CONV_LIST_SELECTORS = [
    "[aria-label*='conversation' i]",
    "[data-test-messaging-conversation-list-item]",
    "[role='listitem']",
]

# Individual message bubbles inside an open thread
_MSG_BUBBLE_SELECTORS = [
    "[data-test-messaging-message]",
    "[aria-label*='message' i][role='listitem']",
    "[role='listitem']",
]

# Sender name inside a bubble
_SENDER_SELECTORS = [
    "[data-test-messaging-message-sender]",
    "[aria-label*='sender' i]",
    "strong",
    "span[class*='sender' i]",
]

# Message body text inside a bubble
_BODY_SELECTORS = [
    "[data-test-messaging-message-body]",
    "[aria-label*='message body' i]",
    "p",
    "span[class*='body' i]",
    "[role='region']",
]

# Timestamp inside a bubble
_TIME_SELECTORS = [
    "time",
    "[aria-label*='time' i]",
    "[datetime]",
    "span[class*='time' i]",
]

# Thread URL pattern: /messaging/thread/<id>/
_THREAD_URL_RE = re.compile(r"/messaging/thread/([^/?#]+)")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class ReplySyncSummary(TypedDict):
    checked: int       # conversations inspected
    updated: int       # conversations that had at least one new message
    new_messages: int  # total new messages persisted
    failed: int        # conversations that raised an error


@dataclass
class _ScrapedMessage:
    """Raw message data extracted from the page."""
    linkedin_message_id: str   # extracted from DOM data-id or URL fragment; "" if unavailable
    sender_name: str
    sender_type: str           # "candidate" | "system"
    message_text: str
    sent_at: datetime          # UTC; falls back to now() if not parseable
    raw_timestamp: str         # original string from DOM for logging


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def sync_replies(account_id: str, *, timeout_ms: int = 30000) -> ReplySyncSummary:
    """Sync inbound replies for all persisted conversations of *account_id*.

    Opens one browser session, iterates every conversation row for this
    account, navigates to each thread, reads messages, and persists new ones.

    Returns ReplySyncSummary.
    """
    summary: ReplySyncSummary = {
        "checked": 0,
        "updated": 0,
        "new_messages": 0,
        "failed": 0,
    }

    logger.info("reply_sync START account_id=%s", account_id)

    conversations = _load_conversations(account_id)
    if not conversations:
        logger.info("reply_sync no_conversations account_id=%s", account_id)
        return summary

    logger.info(
        "reply_sync conversations_loaded count=%d account_id=%s",
        len(conversations), account_id,
    )

    browser = BrowserManager(account_id=account_id)
    try:
        context = await browser.start()
    except Exception:
        logger.exception("reply_sync browser_start_failed account_id=%s", account_id)
        summary["failed"] = len(conversations)
        return summary

    page = await context.new_page()
    try:
        if hasattr(page, "set_default_timeout"):
            page.set_default_timeout(timeout_ms)
        if hasattr(page, "set_default_navigation_timeout"):
            page.set_default_navigation_timeout(min(max(timeout_ms, 10000), 15000))

        for conv_row in conversations:
            conv_id_str = str(conv_row.id)
            linkedin_conv_id = str(conv_row.conversation_id or "").strip()
            candidate_id = str(conv_row.candidate_id or "").strip()

            logger.info(
                "reply_sync conversation START "
                "conv_id=%s linkedin_conv_id=%r candidate_id=%s",
                conv_id_str, linkedin_conv_id, candidate_id,
            )

            if not linkedin_conv_id:
                logger.warning(
                    "reply_sync conversation SKIP no_linkedin_conv_id conv_id=%s", conv_id_str
                )
                summary["failed"] += 1
                continue

            try:
                new_count = await _sync_conversation(
                    page=page,
                    conv_row=conv_row,
                    timeout_ms=timeout_ms,
                )
                summary["checked"] += 1
                if new_count > 0:
                    summary["updated"] += 1
                    summary["new_messages"] += new_count
                logger.info(
                    "reply_sync conversation FINISH conv_id=%s new_messages=%d",
                    conv_id_str, new_count,
                )
            except Exception:
                logger.exception(
                    "reply_sync conversation FAILED conv_id=%s", conv_id_str
                )
                summary["failed"] += 1

    finally:
        try:
            await page.close()
        except Exception:
            pass
        try:
            await browser.stop()
        except Exception:
            logger.debug("reply_sync browser_stop_failed account_id=%s", account_id, exc_info=True)

    logger.info(
        "reply_sync FINISH account_id=%s "
        "checked=%d updated=%d new_messages=%d failed=%d",
        account_id,
        summary["checked"], summary["updated"],
        summary["new_messages"], summary["failed"],
    )
    return summary


# ---------------------------------------------------------------------------
# Per-conversation sync
# ---------------------------------------------------------------------------

async def _sync_conversation(
    *,
    page: Any,
    conv_row: Any,
    timeout_ms: int,
) -> int:
    """Navigate to the thread, scrape messages, persist new ones.

    Returns the count of newly persisted messages.
    """
    linkedin_conv_id = str(conv_row.conversation_id).strip()
    thread_url = f"https://www.linkedin.com/messaging/thread/{linkedin_conv_id}/"

    # Navigate to the thread
    nav_timeout = min(max(timeout_ms, 10000), 15000)
    await page.goto(thread_url, wait_until="domcontentloaded", timeout=nav_timeout)
    await _wait_for_messages(page)

    logger.info("reply_sync navigated thread_url=%s", thread_url)

    # Scrape visible messages
    scraped = await _scrape_messages(page, conv_row)
    if not scraped:
        logger.info("reply_sync no_messages_scraped conv_id=%s", conv_row.id)
        _touch_synced(conv_row)
        return 0

    # Persist new messages
    new_count = _persist_new_messages(conv_row, scraped)
    _touch_synced(conv_row, last_message_at=scraped[-1].sent_at if scraped else None)
    return new_count


# ---------------------------------------------------------------------------
# Page scraping
# ---------------------------------------------------------------------------

async def _wait_for_messages(page: Any) -> None:
    """Wait for the message list to be present in the DOM."""
    for sel in _MSG_BUBBLE_SELECTORS:
        try:
            await page.wait_for_selector(sel, timeout=5000)
            return
        except Exception:
            continue
    # Fallback: short fixed wait
    await asyncio.sleep(1.5)


async def _scrape_messages(page: Any, conv_row: Any) -> list[_ScrapedMessage]:
    """Extract all visible message bubbles from the current thread page."""
    # Scroll to bottom to ensure latest messages are loaded
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.4)
    except Exception:
        pass

    messages: list[_ScrapedMessage] = []

    # Try each bubble selector until we find items
    for bubble_sel in _MSG_BUBBLE_SELECTORS:
        try:
            locs = page.locator(bubble_sel)
            count = await locs.count()
        except Exception:
            continue

        if count == 0:
            continue

        for i in range(min(count, 50)):  # cap at 50 per sync pass
            item = locs.nth(i)
            try:
                if not await item.is_visible():
                    continue
                msg = await _extract_message(item, page, conv_row)
                if msg is not None and msg.message_text.strip():
                    messages.append(msg)
            except Exception:
                continue

        if messages:
            break  # found messages with this selector, stop trying others

    logger.info(
        "reply_sync scraped count=%d conv_id=%s", len(messages), conv_row.id
    )
    return messages


async def _extract_message(
    bubble: Any, page: Any, conv_row: Any
) -> _ScrapedMessage | None:
    """Extract structured data from a single message bubble element."""
    try:
        attrs = await bubble.evaluate(
            """el => ({
                data_id:    el.getAttribute('data-id') || el.getAttribute('data-msg-id') || '',
                aria_label: el.getAttribute('aria-label') || '',
                outer_html: el.outerHTML.slice(0, 600)
            })"""
        )
    except Exception:
        return None

    linkedin_message_id = str(attrs.get("data_id") or "").strip()

    # Sender
    sender_name = await _read_first_text(bubble, _SENDER_SELECTORS)

    # Body text
    message_text = await _read_first_text(bubble, _BODY_SELECTORS)
    if not message_text:
        # Fallback: aria-label of the bubble itself
        message_text = str(attrs.get("aria_label") or "").strip()
    if not message_text:
        return None

    # Timestamp
    raw_ts, sent_at = await _read_timestamp(bubble)

    # Direction: if sender matches the account's display name → outbound (system)
    # Otherwise → inbound (candidate reply)
    account_display = str(getattr(conv_row, "_account_display_name", "") or "").lower()
    sender_lower = sender_name.lower()
    if account_display and account_display in sender_lower:
        sender_type = "system"
    elif not sender_name:
        sender_type = "unknown"
    else:
        sender_type = "candidate"

    return _ScrapedMessage(
        linkedin_message_id=linkedin_message_id,
        sender_name=sender_name,
        sender_type=sender_type,
        message_text=message_text,
        sent_at=sent_at,
        raw_timestamp=raw_ts,
    )


async def _read_first_text(container: Any, selectors: list[str]) -> str:
    """Try each selector in order; return the first non-empty inner text."""
    for sel in selectors:
        try:
            loc = container.locator(sel).first
            if await loc.is_visible():
                text = str(await loc.inner_text(timeout=800) or "").strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


async def _read_timestamp(bubble: Any) -> tuple[str, datetime]:
    """Extract timestamp from a message bubble.  Returns (raw_str, datetime_utc)."""
    for sel in _TIME_SELECTORS:
        try:
            loc = bubble.locator(sel).first
            if not await loc.is_visible():
                continue
            # Prefer datetime attribute
            dt_attr = str(await loc.get_attribute("datetime") or "").strip()
            if dt_attr:
                parsed = _parse_iso(dt_attr)
                if parsed:
                    return dt_attr, parsed
            # Fallback: inner text
            text = str(await loc.inner_text(timeout=500) or "").strip()
            if text:
                parsed = _parse_iso(text)
                if parsed:
                    return text, parsed
                return text, datetime.now(timezone.utc)
        except Exception:
            continue
    return "", datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 string to UTC datetime.  Returns None on failure."""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _persist_new_messages(conv_row: Any, scraped: list[_ScrapedMessage]) -> int:
    """Persist messages that are not already in the DB.  Returns new count."""
    from app.db.session import SessionLocal
    from app.linkedin.models import LinkedInMessageEntity
    from app.linkedin.repository import LinkedInMessageRepository

    conv_id = str(conv_row.id)
    candidate_id = str(conv_row.candidate_id or "")
    new_count = 0

    db = SessionLocal()
    try:
        repo = LinkedInMessageRepository(db)

        for msg in scraped:
            # ── Primary deduplication: linkedin_message_id ────────────────────
            if msg.linkedin_message_id:
                if repo.exists_by_linkedin_id(conv_id, msg.linkedin_message_id):
                    logger.debug(
                        "reply_sync duplicate linkedin_message_id=%s conv_id=%s",
                        msg.linkedin_message_id, conv_id,
                    )
                    continue
            else:
                # ── Fallback deduplication: text + timestamp window ────────────
                if repo.exists_by_text_and_time(conv_id, msg.message_text, msg.sent_at):
                    logger.debug(
                        "reply_sync duplicate text+time conv_id=%s ts=%s",
                        conv_id, msg.raw_timestamp,
                    )
                    continue

            # New message — persist it
            entity = LinkedInMessageEntity(
                id=str(uuid4()),
                conversation_id=conv_id,
                candidate_id=candidate_id,
                sender_type=msg.sender_type,
                message_type="reply",
                message_text=msg.message_text,
                linkedin_message_id=msg.linkedin_message_id,
                attachment_count=0,
                sent_at=msg.sent_at,
                created_at=datetime.now(timezone.utc),
            )
            db.add(entity)
            db.flush()
            new_count += 1
            logger.info(
                "reply_sync new_message persisted "
                "message_id=%s conv_id=%s sender_type=%s ts=%s chars=%d",
                entity.id, conv_id, msg.sender_type,
                msg.raw_timestamp, len(msg.message_text),
            )

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("reply_sync persist_failed conv_id=%s", conv_id)
        raise
    finally:
        db.close()

    return new_count


def _touch_synced(conv_row: Any, *, last_message_at: datetime | None = None) -> None:
    """Update last_synced_at (and optionally last_message_at) on the conversation."""
    from app.db.session import SessionLocal
    from app.linkedin.repository import LinkedInConversationRepository

    db = SessionLocal()
    try:
        repo = LinkedInConversationRepository(db)
        row = repo.get(str(conv_row.id))
        if row is not None:
            repo.touch_synced(row, last_message_at=last_message_at)
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("reply_sync touch_synced_failed conv_id=%s", conv_row.id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_conversations(account_id: str) -> list[Any]:
    """Load all conversation rows for this account, enriched with account display name."""
    from app.db.session import SessionLocal
    from app.linkedin.models import LinkedInAccountEntity
    from app.linkedin.repository import LinkedInConversationRepository

    db = SessionLocal()
    try:
        rows = LinkedInConversationRepository(db).list_for_account(account_id)
        # Attach account display name so _extract_message can classify direction
        account_row = db.get(LinkedInAccountEntity, account_id)
        display_name = str(getattr(account_row, "display_name", "") or "").strip().lower()
        for row in rows:
            object.__setattr__(row, "_account_display_name", display_name) if hasattr(row, "__setattr__") else None
            try:
                row._account_display_name = display_name  # type: ignore[attr-defined]
            except Exception:
                pass
        return rows
    except Exception:
        logger.exception("reply_sync load_conversations_failed account_id=%s", account_id)
        return []
    finally:
        db.close()
