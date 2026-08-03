from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

from app.linkedin.playwright.browser_manager import BrowserManager
from app.linkedin.playwright.dialog_classifier import DialogClassifier
from app.linkedin.playwright.human_interaction import human_hover, human_scroll, wait_after_action
from app.linkedin.playwright.message_delivery_service import MessageDeliveryService
from app.linkedin.playwright.messaging_surface_detector import (
    SURFACE_NONE,
    MessagingSurfaceDetector,
)
from app.linkedin.playwright.messaging_toolbar_resolver import find_message_anchor
from app.linkedin.playwright.profile_inspector import LinkedInProfileInspector
from app.linkedin.playwright.profile_types import LinkedInProfileConnectionState
from app.linkedin.workers.messaging_result import LinkedInMessagingResult
from app.linkedin.workers.messaging_types import LinkedInMessagingWorkerStatus

logger = logging.getLogger(__name__)


class _ComposeContainerError(RuntimeError):
    """Raised by _resolve_compose_container when no unambiguous container is found."""


def _recipient_name_from_url(profile_url: str) -> str:
    """Best-effort display name from a LinkedIn profile URL slug.

    Rules:
      - vaibhav-karemgar          → "Vaibhav Karemgar"   (no suffix, keep all)
      - suram-sai-vignesh-5b285a391 → "Suram Sai Vignesh" (strip generated ID)
      - bisagani-shashivardhan    → "Bisagani Shashivardhan" (all-alpha, keep all)

    A trailing segment is stripped only when it matches ^[a-z0-9]{6,}$ AND
    contains at least one digit.  Pure-alpha segments are never stripped.
    Used only for bubble filtering; never shown to users.
    """
    import re as _re
    _GENERATED_ID = _re.compile(r'^[a-z0-9]{6,}$')
    try:
        slug = profile_url.rstrip("/").split("/in/")[-1].split("/")[0].split("?")[0]
        parts = slug.split("-")
        if (
            len(parts) > 1
            and _GENERATED_ID.match(parts[-1])
            and any(c.isdigit() for c in parts[-1])
        ):
            parts = parts[:-1]
        return " ".join(p.capitalize() for p in parts if p)
    except Exception:
        return ""


class LinkedInMessagingWorker:
    """Orchestrate LinkedIn message delivery end-to-end.

    Click flow:
      1. Navigate to profile.
      2. Locate Message anchor inside the profile top-card (NOT sticky nav).
      3. Pre-click pause 400–900 ms → scroll → hover → hesitate → normal click.
      4. Wait 5–8 s for compose surface.
         → Found → resolve container → deliver.
      5. If not found: page.goto(href).
      6. Wait again for compose surface.
         → Found → resolve container → deliver.
      7. Only if compose is still absent AND a dialog is visible:
         classify dialog text → PREMIUM_REQUIRED / NOT_MESSAGEABLE / UNKNOWN_DIALOG.
      8. No compose, no dialog → DIALOG_NOT_DETECTED.

    dry_run=True logs every resolved selector + count without clicking or
    sending — use this to verify container counts == 1 before live sends.
    force=True is never used anywhere in this flow.
    """

    def __init__(
        self,
        account_id: str,
        *,
        timeout_ms: int = 30000,
        dry_run: bool = False,
    ) -> None:
        self.account_id = account_id
        self.timeout_ms = timeout_ms
        self._dry_run = dry_run
        self._browser_manager = BrowserManager(account_id=account_id)
        self._surface_detector = MessagingSurfaceDetector()
        self._dialog_classifier = DialogClassifier()

    async def run(
        self,
        linkedin_profile_url: str,
        message_text: str = "",
        *,
        dry_run: bool | None = None,
    ) -> LinkedInMessagingResult:
        """Send a message to a LinkedIn profile.

        dry_run=True (or set on __init__) logs every resolved selector and
        count without clicking or sending — use this to verify container
        counts == 1 before enabling live sends.
        """
        is_dry_run = dry_run if dry_run is not None else self._dry_run
        started_at = datetime.now(timezone.utc)
        connection_state = LinkedInProfileConnectionState.UNKNOWN
        logger.info(
            "messaging_worker started account_id=%s profile_url=%s dry_run=%s",
            self.account_id, linkedin_profile_url, is_dry_run,
        )
        try:
            context = await self._browser_manager.get_browser()
            inspector = LinkedInProfileInspector(context, timeout_ms=self.timeout_ms)

            # ── Phase 1: capability check ─────────────────────────────────────
            caps = await inspector.inspect_capabilities(linkedin_profile_url)
            logger.info(
                "messaging_worker caps can_message=%s pending=%s connected=%s "
                "login_required=%s session_expired=%s "
                "profile_not_found=%s profile_private=%s profile_url=%s",
                caps.can_message, caps.pending, caps.connected,
                caps.login_required, caps.session_expired,
                caps.profile_not_found, caps.profile_private,
                linkedin_profile_url,
            )

            if caps.login_required:
                return self._result(
                    LinkedInMessagingWorkerStatus.LOGIN_REQUIRED,
                    connection_state, linkedin_profile_url, started_at,
                )
            if caps.session_expired:
                return self._result(
                    LinkedInMessagingWorkerStatus.SESSION_EXPIRED,
                    connection_state, linkedin_profile_url, started_at,
                    error_message="session expired or account restricted",
                )
            if caps.profile_not_found:
                return self._result(
                    LinkedInMessagingWorkerStatus.PROFILE_NOT_FOUND,
                    connection_state, linkedin_profile_url, started_at,
                )
            if not caps.can_message:
                return self._result(
                    LinkedInMessagingWorkerStatus.NOT_MESSAGEABLE,
                    connection_state, linkedin_profile_url, started_at,
                    error_message="profile does not expose a Message action",
                )

            # ── Phase 2: navigate to profile ──────────────────────────────────
            nav_timeout = min(max(self.timeout_ms, 10000), 15000)
            page = await context.new_page()
            try:
                if hasattr(page, "set_default_timeout"):
                    page.set_default_timeout(self.timeout_ms)
                if hasattr(page, "set_default_navigation_timeout"):
                    page.set_default_navigation_timeout(nav_timeout)

                await page.goto(
                    linkedin_profile_url,
                    wait_until="domcontentloaded",
                    timeout=nav_timeout,
                )
                await self._wait_for_dom(page)
                logger.info("messaging_worker navigated profile_url=%s", linkedin_profile_url)

                # ── JS-readiness wait: let LinkedIn's deferred bundles initialise ──
                # Step 1: networkidle — resolves once background XHRs quiet down.
                # LinkedIn's persistent polling sometimes prevents true networkidle;
                # timeout is non-fatal.
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                    logger.info("messaging_worker networkidle_reached profile_url=%s", linkedin_profile_url)
                except Exception:
                    logger.info("messaging_worker networkidle_timeout — proceeding profile_url=%s", linkedin_profile_url)

                # Step 3 (floor): flat settle regardless of networkidle outcome.
                # Guarantees at least 2.5–3 s after domcontentloaded before any
                # DOM query, so deferred React bundles have time to attach handlers.
                settle_ms = random.randint(2500, 3000)
                logger.info("messaging_worker js_settle_ms=%d profile_url=%s", settle_ms, linkedin_profile_url)
                await asyncio.sleep(settle_ms / 1000.0)

                # ── Phase 3: locate Message anchor (top-card scoped) ──────────
                anchor_result = await find_message_anchor(page, dry_run=is_dry_run)
                for entry in anchor_result.dry_run_log:
                    logger.info("dry_run_toolbar %s", entry)

                if not anchor_result.found:
                    return self._result(
                        LinkedInMessagingWorkerStatus.MESSAGE_BUTTON_NOT_FOUND,
                        connection_state, linkedin_profile_url, started_at,
                        error_message="Message button not found in profile top-card",
                    )

                message_anchor = anchor_result.locator
                logger.info(
                    "messaging_worker message_anchor topcard=%s action_bar=%s",
                    anchor_result.topcard_selector,
                    anchor_result.action_bar_selector,
                )

                # Step 2: React fiber check — confirm the anchor has a live
                # event handler attached, not just a DOM node.
                try:
                    el_handle = await message_anchor.element_handle()
                    await page.wait_for_function(
                        """(el) => {
                            const key = Object.keys(el).find(
                                k => k.startsWith('__reactProps') || k.startsWith('__reactEventHandlers')
                            );
                            return !!key;
                        }""",
                        arg=el_handle,
                        timeout=5000,
                    )
                    logger.info("messaging_worker react_fiber_detected profile_url=%s", linkedin_profile_url)
                except Exception:
                    logger.info("messaging_worker react_fiber_timeout — proceeding profile_url=%s", linkedin_profile_url)

                # Read href before clicking — needed for goto fallback
                href = ""
                try:
                    href = str(await message_anchor.get_attribute("href") or "")
                except Exception:
                    pass
                logger.info(
                    "messaging_worker message_anchor_href=%r profile_url=%s",
                    href, linkedin_profile_url,
                )

                if is_dry_run:
                    logger.info(
                        "dry_run_complete profile_url=%s href=%r",
                        linkedin_profile_url, href,
                    )
                    return self._result(
                        LinkedInMessagingWorkerStatus.DIALOG_REACHED,
                        connection_state, linkedin_profile_url, started_at,
                        compose_selector=anchor_result.message_selector,
                    )

                # ── Phase 4: human click — scroll/hover/hesitate/click ─────────
                await human_scroll(message_anchor)
                await human_hover(message_anchor)
                await wait_after_action(min_ms=150, max_ms=350)

                # Normal click — force=True intentionally absent
                try:
                    await message_anchor.click(timeout=8000)
                    logger.info("messaging_worker message_anchor_clicked profile_url=%s", linkedin_profile_url)
                    await wait_after_action()
                except Exception as exc:
                    logger.info("messaging_worker click_normal_failed reason=%r — trying js_click", str(exc))
                    try:
                        await message_anchor.evaluate("(el) => el.click()")
                        logger.info("messaging_worker message_anchor_clicked strategy=js profile_url=%s", linkedin_profile_url)
                        await wait_after_action()
                    except Exception as exc2:
                        return self._result(
                            LinkedInMessagingWorkerStatus.MESSAGE_BUTTON_NOT_FOUND,
                            connection_state, linkedin_profile_url, started_at,
                            error_message=f"Message click failed: {exc2}",
                        )

                # ── Phase 5: wait 5–8 s for compose surface ───────────────────
                compose_wait = random.uniform(5.0, 8.0)
                logger.info("messaging_worker waiting_for_compose wait_s=%.1f", compose_wait)
                surface = await self._surface_detector.detect(
                    page, max_wait_ms=int(compose_wait * 1000)
                )
                logger.info(
                    "messaging_worker surface_after_click opened=%s surface=%s "
                    "selector=%r compose_type=%r confidence=%s",
                    surface.opened, surface.surface_type,
                    surface.selector, surface.compose_type, surface.confidence,
                )

                # ── Phase 6: goto fallback if compose not found ───────────────
                if not surface.opened:
                    if href:
                        full_href = (
                            href if href.startswith("http")
                            else f"https://www.linkedin.com{href}"
                        )
                        logger.info(
                            "messaging_worker compose_not_found_after_click "
                            "trying goto href=%r profile_url=%s",
                            full_href, linkedin_profile_url,
                        )
                        try:
                            await page.goto(
                                full_href,
                                wait_until="domcontentloaded",
                                timeout=nav_timeout,
                            )
                            await self._wait_for_dom(page)
                        except Exception as exc:
                            logger.warning("messaging_worker goto_failed href=%r error=%s", full_href, exc)

                        surface = await self._surface_detector.detect(page, max_wait_ms=8000)
                        logger.info(
                            "messaging_worker surface_after_goto opened=%s surface=%s "
                            "selector=%r compose_type=%r confidence=%s",
                            surface.opened, surface.surface_type,
                            surface.selector, surface.compose_type, surface.confidence,
                        )
                    else:
                        logger.warning(
                            "messaging_worker no_href_for_goto_fallback profile_url=%s",
                            linkedin_profile_url,
                        )

                # ── Phase 7: compose found → resolve container → deliver ───────
                if surface.opened and surface.surface_type != SURFACE_NONE:
                    recipient_name = _recipient_name_from_url(linkedin_profile_url)
                    logger.info(
                        "messaging_worker recipient_name=%r profile_url=%s",
                        recipient_name, linkedin_profile_url,
                    )
                    try:
                        container = await self._resolve_compose_container(
                            page, href, surface, recipient_name=recipient_name
                        )
                    except _ComposeContainerError as exc:
                        logger.error(
                            "messaging_worker compose_container_resolution_failed "
                            "reason=%r profile_url=%s",
                            str(exc), linkedin_profile_url,
                        )
                        return self._result(
                            LinkedInMessagingWorkerStatus.SEND_FAILED,
                            connection_state, linkedin_profile_url, started_at,
                            error_message=str(exc),
                        )
                    return await self._deliver(
                        page, container, message_text, surface,
                        connection_state, linkedin_profile_url, started_at,
                    )

                # ── Phase 8: compose absent → inspect dialog (last resort) ─────
                dialog_visible = False
                try:
                    dialog_visible = await page.locator(
                        "[role='dialog'], [role='alertdialog'], dialog"
                    ).first.is_visible()
                except Exception:
                    pass

                if dialog_visible:
                    return await self._handle_dialog(
                        page, connection_state, linkedin_profile_url, started_at,
                    )

                logger.warning(
                    "messaging_worker surface_not_found no_dialog profile_url=%s",
                    linkedin_profile_url,
                )
                return self._result(
                    LinkedInMessagingWorkerStatus.DIALOG_NOT_DETECTED,
                    connection_state, linkedin_profile_url, started_at,
                    error_message="No messaging surface found after click and goto fallback",
                )

            finally:
                try:
                    await page.close()
                except Exception:
                    pass

        except Exception as exc:
            logger.exception("messaging_worker failed profile_url=%s", linkedin_profile_url)
            return self._result(
                LinkedInMessagingWorkerStatus.FAILED,
                connection_state, linkedin_profile_url, started_at,
                error_message=str(exc),
            )
        finally:
            logger.info(
                "messaging_worker finished profile_url=%s duration_ms=%d",
                linkedin_profile_url, self._duration_ms(started_at),
            )
            try:
                await self._browser_manager.stop()
            except Exception:
                logger.debug("messaging_worker browser_stop_failed", exc_info=True)

    # ── Compose container resolver ────────────────────────────────────────────

    async def _resolve_compose_container(
        self, page: Any, href: str, surface: Any = None, *, recipient_name: str = ""
    ) -> Any:
        """Return the ONE compose container Locator for this conversation.

        For .msg-overlay-conversation-bubble (multiple bubbles open):
          1. URN filter  — bubbles.filter(has=page.locator('a[href*="{urn}"]'))
                           Playwright's has= searches the full bubble subtree,
                           so deeply-nested profile links are found correctly.
             count == 1  → return immediately
             count  > 1  → raise (ambiguous even with URN — hard failure)
             count == 0  → fall through to name matching
          2. Header-name filter — reads only the bubble header element text
             (not full subtree) to avoid false positives from message history.

        For all other selectors: count == 1 → return; count > 1 → raise.

        Raises _ComposeContainerError on ambiguous or unresolved matches.
        """
        _CONTAINER_SELECTORS = [
            "[data-test-messaging-compose]",
            ".msg-overlay-conversation-bubble",
            ".msg-form",
            "[aria-label*='conversation' i][role='region']",
            "[aria-label*='messaging' i][role='region']",
            "[aria-label*='message' i][role='dialog']",
            "main[aria-label*='conversation' i]",
            "main[aria-label*='messaging' i]",
        ]
        # Extract the bare profile ID from the `recipient=` query param.
        # Bubble hrefs use /in/<profileId> format, NOT the full URN, so we
        # match against the profile ID directly.
        # e.g. href contains recipient=ACoAADI12b0B... → profile_id=ACoAADI12b0B...
        profile_id = ""
        if "recipient=" in href:
            try:
                profile_id = href.split("recipient=")[1].split("&")[0]
            except Exception:
                pass
        # Also keep the full URN (URL-decoded) for logging only.
        profile_urn = ""
        if "profileUrn=" in href:
            try:
                from urllib.parse import unquote
                profile_urn = unquote(href.split("profileUrn=")[1].split("&")[0])
            except Exception:
                pass
        logger.info(
            "compose_container resolving profile_id=%r profile_urn=%r "
            "recipient_name=%r href=%r",
            profile_id, profile_urn, recipient_name, href,
        )

        for sel in _CONTAINER_SELECTORS:
            try:
                locs = page.locator(sel)
                count = await locs.count()
                if count == 0:
                    continue
                logger.info("compose_container sel=%s count=%d", sel, count)

                if count == 1:
                    item = locs.first
                    if await item.is_visible():
                        return item

                # ── Multiple containers: bubble-specific disambiguation ────────
                # Dump per-bubble link hrefs so we can see in the log whether
                # the profile ID appears in each bubble's subtree.
                for i in range(count):
                    try:
                        bubble_hrefs = await locs.nth(i).evaluate(
                            "el => Array.from(el.querySelectorAll('a[href]'))"
                            ".map(a => a.getAttribute('href')).slice(0, 10)"
                        )
                        logger.info(
                            "compose_container bubble[%d] hrefs=%s", i, bubble_hrefs
                        )
                    except Exception:
                        logger.info("compose_container bubble[%d] hrefs=<error>", i)

                # ── Strategy 1: profile-ID / slug filter ────────────────────
                # Bubble hrefs use either /in/<profileId> or /in/<slug> format.
                # Try the bare profile ID from `recipient=` first, then fall
                # back to the slug extracted from the profile URL.
                match_tokens: list[str] = []
                if profile_id:
                    match_tokens.append(profile_id)
                # Extract slug from the profile URL passed via recipient_name
                # context — the caller already has it as part of href parsing.
                # Derive it from href's profileUrn path or from recipient_name.
                # Simplest: pull the slug from the page URL stored in href's
                # referrer context.  We don't have profile_url here directly,
                # but we can derive the slug from profile_urn's last segment
                # or from the bubble hrefs themselves via recipient_name.
                # Most reliable: also try matching by slug from the page locator.
                # We pass recipient_name as a hint — convert it back to a slug
                # approximation for href matching.
                if recipient_name:
                    # e.g. "Bisagani Shashivardhan" → "bisagani-shashivardhan"
                    slug_approx = recipient_name.lower().replace(" ", "-")
                    match_tokens.append(slug_approx)

                for token in match_tokens:
                    token_matches = locs.filter(
                        has=page.locator(f'a[href*="{token}"]')
                    )
                    token_count = await token_matches.count()
                    logger.info(
                        "compose_container token_match sel=%s token=%r count=%d",
                        sel, token, token_count,
                    )
                    if token_count == 1:
                        return token_matches.first
                    if token_count > 1:
                        await self._dump_container_failure(
                            page, tag="compose_container_token_ambiguous",
                            detail=(
                                f"sel={sel} token_count={token_count} "
                                f"token={token!r}"
                            ),
                        )
                        raise _ComposeContainerError(
                            f"compose_container_token_ambiguous sel={sel} "
                            f"token_count={token_count} token={token!r}"
                        )

                if match_tokens:
                    logger.info(
                        "compose_container token_no_match sel=%s tokens=%r "
                        "— trying active-editor strategy",
                        sel, match_tokens,
                    )

                # ── Strategy 2: active-editor bubble ──────────────────────────
                # A freshly-opened compose bubble contains a focused/active
                # contenteditable editor; stale minimised bubbles do not.
                # Match the bubble that contains the compose editor.
                editor_matches = locs.filter(
                    has=page.locator(
                        ".msg-form__contenteditable, "
                        "[aria-label*='message' i][contenteditable='true'], "
                        "[contenteditable='true'].msg-form__contenteditable"
                    )
                )
                editor_count = await editor_matches.count()
                logger.info(
                    "compose_container editor_match sel=%s count=%d",
                    sel, editor_count,
                )
                if editor_count >= 1:
                    # LinkedIn always appends the newly-opened compose bubble
                    # last in the DOM.  Use .last regardless of count so that
                    # a stale open conversation (earlier in DOM order) is never
                    # mistaken for the fresh compose target.
                    logger.info(
                        "compose_container editor_match_last sel=%s count=%d",
                        sel, editor_count,
                    )
                    return editor_matches.last
                # editor_count == 0: fall through to name matching
                logger.info(
                    "compose_container editor_no_match sel=%s "
                    "— falling back to name match", sel,
                )

                # ── Strategy 2: header-name match (secondary fallback only) ───
                if recipient_name:
                    _HEADER_SELECTORS = [
                        ".msg-overlay-bubble-header__title",
                        ".msg-overlay-conversation-bubble__title",
                        "[class*='bubble-header'] [class*='title']",
                        "[class*='bubble-header'] h2",
                        "[class*='bubble-header'] span",
                    ]
                    header_matched: Any | None = None
                    for i in range(count):
                        item = locs.nth(i)
                        try:
                            if not await item.is_visible():
                                continue
                            for hsel in _HEADER_SELECTORS:
                                try:
                                    htext = str(
                                        await item.locator(hsel).first.inner_text(timeout=500)
                                        or ""
                                    ).strip()
                                    if recipient_name.lower() in htext.lower():
                                        logger.info(
                                            "compose_container matched_by_header_name "
                                            "sel=%s index=%d hsel=%s htext=%r",
                                            sel, i, hsel, htext,
                                        )
                                        header_matched = item
                                        break
                                except Exception:
                                    continue
                            if header_matched is not None:
                                break
                        except Exception:
                            continue
                    if header_matched is not None:
                        return header_matched
                    logger.info(
                        "compose_container header_name_no_match sel=%s recipient=%r",
                        sel, recipient_name,
                    )

                # No match by URN or header-name — do NOT fall back to positional.
                await self._dump_container_failure(
                    page, tag="compose_container_unresolved",
                    detail=(
                        f"sel={sel} count={count} "
                        f"profile_urn={profile_urn!r} recipient_name={recipient_name!r}"
                    ),
                )
                raise _ComposeContainerError(
                    f"compose_container_unresolved sel={sel} count={count} "
                    f"profile_urn={profile_urn!r} recipient_name={recipient_name!r}"
                )
            except _ComposeContainerError:
                raise
            except Exception:
                continue

        # URL-based: full messaging page — use main as container (unambiguous)
        url = str(getattr(page, "url", ""))
        if "/messaging/" in url:
            logger.info("compose_container url_based url=%s", url)
            return page.locator("main").first

        await self._dump_container_failure(
            page, tag="compose_container_unresolved",
            detail="no selector matched and not on /messaging/ page",
        )
        raise _ComposeContainerError(
            "compose_container_unresolved: no selector matched"
        )

    async def _dump_container_failure(
        self, page: Any, *, tag: str, detail: str
    ) -> None:
        """Save screenshot + HTML on compose-container resolution failure."""
        from pathlib import Path
        import json as _json
        debug_dir = Path(__file__).resolve().parents[3] / "debug_logs" / "compose_container"
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            prefix = debug_dir / f"{ts}_{tag}"
            logger.error("%s detail=%s", tag, detail)
            try:
                await page.screenshot(path=str(prefix) + ".png", full_page=False)
            except Exception as exc:
                logger.warning("%s screenshot_failed: %s", tag, exc)
            try:
                html = await page.locator("body").inner_html(timeout=3000)
                Path(str(prefix) + ".html").write_text(str(html), encoding="utf-8")
            except Exception as exc:
                logger.warning("%s html_save_failed: %s", tag, exc)
            try:
                Path(str(prefix) + ".json").write_text(
                    _json.dumps({"tag": tag, "detail": detail, "url": str(getattr(page, "url", ""))},
                                indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
        except Exception as exc:
            logger.warning("%s dump_failed: %s", tag, exc)

    # ── Bubble close helper ───────────────────────────────────────────────────

    async def _close_compose_bubble(self, container: Any) -> None:
        """Click the close button on the resolved compose bubble after delivery.

        Prevents bubble accumulation when messaging multiple candidates in the
        same process.  Non-fatal: any failure is logged and swallowed.
        """
        _CLOSE_SELECTORS = [
            "button[aria-label*='Close' i]",
            "button[aria-label*='Dismiss' i]",
            "button[data-control-name*='close' i]",
            "button.msg-overlay-bubble-header__control",
            ".msg-overlay-conversation-bubble__close-btn",
        ]
        for sel in _CLOSE_SELECTORS:
            try:
                btn = container.locator(sel).first
                if await btn.is_visible():
                    await btn.click(timeout=3000)
                    logger.info("compose_bubble_closed sel=%s", sel)
                    return
            except Exception:
                continue
        logger.debug("compose_bubble_close_not_found — no close button matched")

    # ── Dialog handling ───────────────────────────────────────────────────────

    async def _handle_dialog(
        self,
        page: Any,
        connection_state: LinkedInProfileConnectionState,
        profile_url: str,
        started_at: datetime,
    ) -> LinkedInMessagingResult:
        """Classify a visible dialog by text only. Called only after compose detection fails."""
        classification = await self._dialog_classifier.classify(page)
        logger.info(
            "messaging_worker dialog_classified outcome=%s signal=%r "
            "text_sample=%r profile_url=%s",
            classification.outcome,
            classification.matched_signal,
            classification.dialog_text[:120],
            profile_url,
        )

        if classification.is_premium:
            return self._result(
                LinkedInMessagingWorkerStatus.PREMIUM_REQUIRED,
                connection_state, profile_url, started_at,
                error_message=(
                    f"LinkedIn showed a premium upsell dialog "
                    f"(signal={classification.matched_signal!r})"
                ),
            )

        if classification.is_connection:
            return self._result(
                LinkedInMessagingWorkerStatus.NOT_MESSAGEABLE,
                connection_state, profile_url, started_at,
                error_message=(
                    f"LinkedIn requires a connection before messaging "
                    f"(signal={classification.matched_signal!r})"
                ),
            )

        return self._result(
            LinkedInMessagingWorkerStatus.UNKNOWN_DIALOG,
            connection_state, profile_url, started_at,
            error_message=(
                f"Unrecognised dialog appeared after Message click "
                f"(text={classification.dialog_text[:120]!r})"
            ),
        )

    # ── Delivery ──────────────────────────────────────────────────────────────

    async def _deliver(
        self,
        page: Any,
        container: Any,
        message_text: str,
        surface: Any,
        connection_state: LinkedInProfileConnectionState,
        profile_url: str,
        started_at: datetime,
    ) -> LinkedInMessagingResult:
        """Hand off to MessageDeliveryService with the resolved container."""
        if not message_text:
            return self._result(
                LinkedInMessagingWorkerStatus.DIALOG_REACHED,
                connection_state, profile_url, started_at,
                compose_selector=surface.selector,
            )

        delivery = MessageDeliveryService(
            timeout_ms=self.timeout_ms,
            dry_run=self._dry_run,
        )
        delivery_result = await delivery.deliver(container, message_text, page=page)

        # Close the bubble regardless of outcome to prevent accumulation.
        await self._close_compose_bubble(container)

        if delivery_result.success:
            logger.info(
                "messaging_worker sent method=%s compose=%s send=%s layout=%s",
                delivery_result.verification_method,
                delivery_result.compose_selector,
                delivery_result.send_selector,
                delivery_result.layout,
            )
            return self._result(
                LinkedInMessagingWorkerStatus.MESSAGE_SENT,
                connection_state, profile_url, started_at,
                message_text=message_text,
                compose_selector=delivery_result.compose_selector,
                send_selector=delivery_result.send_selector,
                verification_method=delivery_result.verification_method,
            )

        logger.warning(
            "messaging_worker delivery_failed error=%r attempts=%d profile_url=%s",
            delivery_result.error, delivery_result.attempts, profile_url,
        )
        return self._result(
            LinkedInMessagingWorkerStatus.SEND_FAILED,
            connection_state, profile_url, started_at,
            error_message=delivery_result.error,
            compose_selector=delivery_result.compose_selector,
            send_selector=delivery_result.send_selector,
        )

    # ── DOM helpers ───────────────────────────────────────────────────────────

    async def _wait_for_dom(self, page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1500)
        except Exception:
            pass
        try:
            await page.wait_for_selector("main, [role='main'], body", timeout=2000)
        except Exception:
            pass

    # ── Result builder ────────────────────────────────────────────────────────

    def _result(
        self,
        status: LinkedInMessagingWorkerStatus,
        connection_state: LinkedInProfileConnectionState,
        profile_url: str,
        started_at: datetime,
        *,
        error_message: str = "",
        message_text: str = "",
        compose_selector: str = "",
        send_selector: str = "",
        verification_method: str = "",
    ) -> LinkedInMessagingResult:
        return LinkedInMessagingResult(
            status=status,
            connection_state=connection_state,
            profile_url=profile_url,
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_ms=self._duration_ms(started_at),
            error_message=error_message,
            message_text=message_text,
            compose_selector=compose_selector,
            send_selector=send_selector,
            verification_method=verification_method,
        )

    def _duration_ms(self, started_at: datetime) -> int:
        return int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
