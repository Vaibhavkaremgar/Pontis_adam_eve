"""message_delivery_service.py — LinkedIn message delivery pipeline.

The compose container is the ONLY DOM entry point for this service.
`page` is never queried after the container is resolved — page-wide access
is impossible by construction, not just convention.

Caller responsibilities (MessagingWorker):
  1. Navigate to the profile page.
  2. Click the Message anchor (using messaging_toolbar_resolver).
  3. Wait for the compose surface to open.
  4. Resolve the compose container Locator.
  5. Call deliver(container, message_text).

This service then:
  - Finds the editor inside the container.
  - Finds the Send button inside the container.
  - Types with per-character human delay (40–120 ms).
  - Pauses 600–1500 ms before clicking Send (mimics final read-over).
  - Verifies delivery using only container-scoped locators.
  - Retries once on failure.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.linkedin.playwright.human_interaction import human_click, wait_after_action

logger = logging.getLogger(__name__)

_DEBUG_DIR = Path(__file__).resolve().parents[3] / "debug_logs" / "message_delivery"

# ---------------------------------------------------------------------------
# Container-scoped selectors
# ---------------------------------------------------------------------------

# Compose editor — tried in order inside the container only
_COMPOSE_SELECTORS: list[str] = [
    "[aria-label*='message' i][contenteditable='true']",
    "[aria-label*='message' i][role='textbox']",
    "[aria-label*='compose' i][contenteditable='true']",
    "[aria-label*='write' i][contenteditable='true']",
    "[data-lexical-editor][contenteditable='true']",
    "[data-lexical-editor='true']",
    "[aria-multiline='true'][contenteditable='true']",
    "[role='textbox'][contenteditable='true']",
    "[role='textbox']",
    "div[contenteditable='true']",
    "[contenteditable='true']",
    "[placeholder*='message' i]",
    "textarea",
]

# Send button — tried in order inside the container only
_SEND_SELECTORS: list[str] = [
    "[aria-label='Send'][role='button']",
    "[aria-label='Send']",
    "[aria-label*='Send message' i]",
    "[aria-label*='send' i][role='button']",
    "button[type='submit']",
    "button:has-text('Send')",
    "[data-control-name*='send' i]",
]

# Outgoing bubble selectors for post-send verification (container-scoped)
_SENT_BUBBLE_SELECTORS: list[str] = [
    "[class*='sent']",
    "[class*='outgoing']",
    "[data-msg-sent]",
    "[aria-label*='sent' i]",
    "[aria-label*='delivered' i]",
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class DeliveryResult:
    success: bool
    verification_method: str = ""
    compose_selector: str = ""
    send_selector: str = ""
    layout: str = ""
    error: str = ""
    attempts: int = 0


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class MessageDeliveryService:
    """Deliver a message through a LinkedIn messaging compose container.

    The container Locator is the ONLY DOM entry point.  The page object is
    accepted only for diagnostics (screenshots/HTML on failure).
    """

    def __init__(
        self,
        *,
        timeout_ms: int = 30_000,
        debug_dir: Path | None = None,
        dry_run: bool = False,
    ) -> None:
        self._timeout_ms = timeout_ms
        self._debug_dir = debug_dir or _DEBUG_DIR
        self._dry_run = dry_run

    async def deliver(
        self,
        container: Any,
        message_text: str,
        *,
        page: Any | None = None,
    ) -> DeliveryResult:
        """Run the full delivery pipeline with one retry on failure.

        Args:
            container: Playwright Locator scoping the ONE compose overlay.
            message_text: Text to send.
            page: Optional — used only for failure diagnostics (screenshots).

        Returns DeliveryResult.  Never raises.
        """
        result: DeliveryResult | None = None
        for attempt in range(1, 3):
            logger.info("delivery attempt=%d dry_run=%s", attempt, self._dry_run)
            result = await self._attempt(container, message_text, attempt)
            if result.success:
                return result
            logger.warning(
                "delivery attempt=%d failed error=%r — %s",
                attempt, result.error,
                "retrying" if attempt == 1 else "giving up",
            )
            if attempt == 1:
                await asyncio.sleep(1.5)

        if page is not None:
            await self._save_diagnostics(page, result, message_text)
        return result  # type: ignore[return-value]

    async def _attempt(
        self, container: Any, message_text: str, attempt: int
    ) -> DeliveryResult:
        # ── Step 1: find compose editor inside container ──────────────────────
        compose_loc, compose_sel = await self._find_compose(container)
        if compose_loc is None:
            logger.warning("delivery no_compose_editor attempt=%d", attempt)
            return DeliveryResult(
                success=False,
                error="no_compose_editor_in_container",
                attempts=attempt,
            )

        # ── Step 2: find Send button inside container ─────────────────────────
        send_loc, send_sel = await self._find_send(container)
        if send_loc is None:
            logger.warning("delivery no_send_button attempt=%d", attempt)
            return DeliveryResult(
                success=False,
                error="no_send_button_in_container",
                compose_selector=compose_sel,
                attempts=attempt,
            )

        logger.info(
            "delivery locators_resolved compose=%s send=%s attempt=%d",
            compose_sel, send_sel, attempt,
        )

        if self._dry_run:
            logger.info(
                "delivery DRY_RUN compose_sel=%s send_sel=%s message_len=%d",
                compose_sel, send_sel, len(message_text),
            )
            return DeliveryResult(
                success=True,
                verification_method="dry_run",
                compose_selector=compose_sel,
                send_selector=send_sel,
                attempts=attempt,
            )

        # ── Step 3: click to focus the compose editor (real pointer event) ────
        # locator.click() establishes Lexical's internal selection anchor;
        # programmatic focus() does not and causes keystrokes to be discarded.
        try:
            await compose_loc.click(timeout=5_000)
            await wait_after_action(min_ms=80, max_ms=180)
        except Exception as exc:
            logger.warning("delivery click_to_focus_failed: %s", exc)

        # ── Step 4: type with per-character human delay ───────────────────────
        try:
            await self._human_type(compose_loc, message_text)
        except Exception as exc:
            return DeliveryResult(
                success=False,
                error=f"typing failed: {exc}",
                compose_selector=compose_sel,
                send_selector=send_sel,
                attempts=attempt,
            )

        # ── Step 5: verify typed text ─────────────────────────────────────────
        if not await self._verify_typed(compose_loc, message_text):
            logger.warning("delivery typed_verification_failed attempt=%d", attempt)
            return DeliveryResult(
                success=False,
                error="typed text verification failed",
                compose_selector=compose_sel,
                send_selector=send_sel,
                attempts=attempt,
            )
        logger.info("delivery typed_verified attempt=%d", attempt)

        # ── Step 6: post-type pause (mimics final read-over) ──────────────────
        pause_ms = random.randint(600, 1500)
        logger.info("delivery pre_send_pause_ms=%d", pause_ms)
        await asyncio.sleep(pause_ms / 1000.0)

        # ── Step 7: click Send ────────────────────────────────────────────────
        try:
            await human_click(send_loc)
        except Exception as exc:
            return DeliveryResult(
                success=False,
                error=f"send click failed: {exc}",
                compose_selector=compose_sel,
                send_selector=send_sel,
                attempts=attempt,
            )

        # ── Step 8: verify sent — container-scoped only ───────────────────────
        # had_content=True: typing was verified above, so the editor was
        # non-empty before Send was clicked — required for combined signal.
        verified, method = await self._verify_sent(
            container, compose_loc, send_loc, message_text, had_content=True
        )
        if verified:
            logger.info("delivery SUCCESS method=%s attempt=%d", method, attempt)
            return DeliveryResult(
                success=True,
                verification_method=method,
                compose_selector=compose_sel,
                send_selector=send_sel,
                attempts=attempt,
            )

        return DeliveryResult(
            success=False,
            error=f"send verification failed (method attempted: {method})",
            compose_selector=compose_sel,
            send_selector=send_sel,
            attempts=attempt,
        )

    # ── Locator helpers — container-scoped only ───────────────────────────────

    async def _find_compose(self, container: Any) -> tuple[Any | None, str]:
        for sel in _COMPOSE_SELECTORS:
            try:
                locs = container.locator(sel)
                count = await locs.count()
                for i in range(min(count, 5)):
                    item = locs.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        if not await item.is_editable(timeout=500):
                            continue
                        logger.debug("delivery compose_found sel=%s", sel)
                        return item, sel
                    except Exception:
                        continue
            except Exception:
                continue
        return None, ""

    async def _find_send(self, container: Any) -> tuple[Any | None, str]:
        for sel in _SEND_SELECTORS:
            try:
                locs = container.locator(sel)
                count = await locs.count()
                for i in range(min(count, 5)):
                    item = locs.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        # Allow disabled Send — typing will enable it.
                        logger.debug("delivery send_found sel=%s", sel)
                        return item, sel
                    except Exception:
                        continue
            except Exception:
                continue
        return None, ""

    # ── Human-paced typing ────────────────────────────────────────────────────

    async def _human_type(self, locator: Any, text: str) -> None:
        """Click to focus, clear via real keypresses, then type char-by-char.

        Uses only real keyboard events — no DOM mutation (el.textContent = '')
        which corrupts Lexical's internal EditorState and causes typed content
        to be silently discarded on the next reconciler cycle.
        """
        # Real pointer click establishes Lexical's selection anchor.
        # Step 3 in _attempt already clicked, but _human_type may be called
        # independently, so click here too (idempotent for an already-focused
        # editor, and necessary when called standalone).
        await locator.click(timeout=5_000)
        # Clear via real keyboard events only — Lexical processes these through
        # its own event handlers and updates EditorState correctly.
        await locator.press("Control+a")
        await asyncio.sleep(0.05)   # let Lexical process the select-all
        await locator.press("Delete")
        await asyncio.sleep(0.05)   # let Lexical reconcile the empty state
        # Type character by character with randomised delay
        for char in text:
            await locator.type(char, delay=0)
            await asyncio.sleep(random.uniform(0.040, 0.120))
        # Settle wait: allow Lexical's reconciler to commit the typed content
        # before _verify_typed reads inner_text(). Without this, the check
        # may read a transient DOM snapshot before reconciliation completes.
        await asyncio.sleep(0.3)
        logger.info("human_type chars=%d", len(text))

    # ── Typed text verification ───────────────────────────────────────────────

    async def _verify_typed(self, locator: Any, expected: str) -> bool:
        for method in ("input_value", "inner_text", "text_content"):
            try:
                value = str(await getattr(locator, method)(timeout=3_000) or "").strip()
                if value and (expected in value or value.strip() == expected.strip()):
                    return True
            except Exception:
                continue
        try:
            value = str(await locator.get_attribute("value") or "").strip()
            if value and expected in value:
                return True
        except Exception:
            pass
        return False

    # ── Send verification — container-scoped, ordered by signal strength ──────

    async def _verify_sent(
        self,
        container: Any,
        compose_loc: Any,
        send_loc: Any,
        message_text: str,
        *,
        had_content: bool = False,
        max_wait_ms: int = 6_000,
    ) -> tuple[bool, str]:
        """Check signals in order: compose_disappeared → outgoing_bubble →
        compose_cleared+had_content+send_disabled.  All scoped to container.

        send_disabled alone is NOT a valid success signal — a disabled Send
        button is the initial state before any text is typed.  It is only
        accepted in combination with compose_cleared AND had_content (the
        editor was confirmed non-empty after typing, so clearing it is a
        genuine post-send reset).
        """
        deadline = time.monotonic() + max_wait_ms / 1000.0

        while time.monotonic() < deadline:
            # Signal 1: compose field disappeared
            try:
                if not await compose_loc.is_visible():
                    logger.info("verify_sent signal=compose_disappeared")
                    return True, "compose_disappeared"
            except Exception:
                return True, "compose_disappeared"

            # Signal 2: outgoing bubble with matching text — strongest signal
            try:
                for sel in _SENT_BUBBLE_SELECTORS:
                    bubble = container.locator(sel).last
                    if await bubble.is_visible():
                        bubble_text = str(await bubble.inner_text(timeout=1_000) or "")
                        if message_text[:20] in bubble_text:
                            logger.info("verify_sent signal=outgoing_bubble sel=%s", sel)
                            return True, f"outgoing_bubble:{sel}"
            except Exception:
                pass

            # Signal 3: compose cleared + had_content + send_disabled (combined)
            # Each condition alone is ambiguous; together they indicate a
            # genuine post-send reset of the compose surface.
            if had_content:
                compose_empty = False
                for method in ("input_value", "inner_text", "text_content"):
                    try:
                        value = str(
                            await getattr(compose_loc, method)(timeout=1_000) or ""
                        ).strip()
                        if not value:
                            compose_empty = True
                            break
                    except Exception:
                        continue

                if compose_empty:
                    send_disabled = False
                    try:
                        send_disabled = bool(
                            await send_loc.evaluate("el => el.disabled || false")
                        )
                    except Exception:
                        pass
                    if send_disabled:
                        logger.info("verify_sent signal=compose_cleared+send_disabled")
                        return True, "compose_cleared+send_disabled"
                    # compose is empty but send is still enabled — ambiguous,
                    # keep polling for the bubble or disappeared signal.

            await asyncio.sleep(0.25)

        logger.warning("verify_sent no_signal_within_%dms", max_wait_ms)
        return False, "unverified"

    # ── Diagnostics — only on failure ─────────────────────────────────────────

    async def _save_diagnostics(
        self,
        page: Any,
        result: DeliveryResult | None,
        message_text: str,
    ) -> None:
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

            try:
                await page.screenshot(
                    path=str(self._debug_dir / f"{ts}_failure.png"), full_page=False
                )
            except Exception as exc:
                logger.warning("delivery screenshot failed: %s", exc)

            try:
                html = await page.locator("body").inner_html(timeout=3_000)
                (self._debug_dir / f"{ts}_failure.html").write_text(
                    str(html), encoding="utf-8"
                )
            except Exception as exc:
                logger.warning("delivery html_save failed: %s", exc)

            meta = {
                "ts": ts,
                "url": str(getattr(page, "url", "")),
                "error": result.error if result else "",
                "message_text_len": len(message_text),
            }
            try:
                meta["title"] = str(await page.title() or "")
            except Exception:
                pass
            (self._debug_dir / f"{ts}_failure.json").write_text(
                _json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("delivery diagnostics failed: %s", exc)
