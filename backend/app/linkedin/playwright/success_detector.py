"""success_detector.py — LinkedIn job publish confirmation detector.

Detects LinkedIn publish-state confirmation using a combination of:
  - post-publish URL state
  - LinkedIn-specific publish text
  - LinkedIn-specific job-management action text

Generic success words are intentionally ignored.
Works with any container Locator or page object.

Usage:
    detector = SuccessDetector()
    ok, signal = await detector.detect(container, timeout_ms=8000)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Ordered by signal strength (strongest first)
_TOAST_SELECTORS = [
    "[role='alert']",
    "[role='status']",
    "[aria-live='assertive']",
    "[aria-live='polite']",
    "[class*='toast']",
    "[class*='snackbar']",
    "[class*='notification']",
]

_BANNER_SELECTORS = [
    "[role='banner']",
    "[class*='banner']",
    "[class*='success']",
    "[class*='confirmation']",
    "[class*='alert-success']",
]

_DIALOG_SELECTORS = [
    "[role='dialog']",
    "[role='alertdialog']",
    "dialog",
]

_LINKEDIN_POST_PUBLISH_URL_HINTS = [
    "/jobs/view/",
    "/jobs/collections/",
    "/jobs/manage/",
    "/talent/jobs/",
]

_LINKEDIN_POST_PUBLISH_TEXT_TOKENS = [
    "job posted",
    "job has been posted",
    "your job is live",
    "job is now live",
    "job listing is live",
]

_LINKEDIN_POST_PUBLISH_ACTION_TOKENS = [
    "view your job",
    "view job",
    "manage job posts",
    "manage jobs",
    "job details",
    "edit job",
]


class SuccessDetector:
    """Detect LinkedIn job publish confirmation signals in a container or page.

    Returns (True, signal_name) on first match, (False, "undetected") on timeout.
    """

    async def detect(
        self,
        container: Any,
        *,
        timeout_ms: int = 8000,
        url_fragment: str = "",
        page: Any | None = None,
    ) -> tuple[bool, str]:
        """Poll for LinkedIn publish confirmation until *timeout_ms* expires.

        Args:
            container:    Locator scoping the search (use page.locator("body") for full page).
            timeout_ms:   How long to poll.
            url_fragment: Unused by LinkedIn publish confirmation; kept for compatibility.
            page:         Page object — required for URL and button-disabled checks.
        """
        deadline = time.monotonic() + timeout_ms / 1000.0

        while time.monotonic() < deadline:
            url = str(getattr(page, "url", "") or "") if page is not None else ""
            body_text = ""
            try:
                body_text = str(await container.inner_text(timeout=2000) or "").lower()
            except Exception:
                body_text = ""

            url_signal = self._check_publish_url(url)
            text_signal = self._check_text_token(body_text, _LINKEDIN_POST_PUBLISH_TEXT_TOKENS)
            action_signal = self._check_text_token(body_text, _LINKEDIN_POST_PUBLISH_ACTION_TOKENS)

            # Require a LinkedIn publish-state URL plus a LinkedIn-specific text cue.
            # The action cue is a corroborating signal when present.
            if url_signal and text_signal:
                if action_signal:
                    logger.info(
                        "success_detector signal=linkedin_publish url=%s text=%s action=%s",
                        url_signal,
                        text_signal,
                        action_signal,
                    )
                    return True, f"linkedin_publish:{url_signal}:{text_signal}:{action_signal}"
                logger.info(
                    "success_detector signal=linkedin_publish url=%s text=%s",
                    url_signal,
                    text_signal,
                )
                return True, f"linkedin_publish:{url_signal}:{text_signal}"

            await asyncio.sleep(0.25)

        logger.debug("success_detector timeout_ms=%d no_signal", timeout_ms)
        return False, "undetected"

    # ── Signal checkers ───────────────────────────────────────────────────────

    def _check_publish_url(self, url: str) -> str:
        lowered = url.lower()
        if "/job-posting/" in lowered:
            return ""
        for hint in _LINKEDIN_POST_PUBLISH_URL_HINTS:
            if hint in lowered:
                return hint
        return ""

    @staticmethod
    def _check_text_token(text: str, tokens: list[str]) -> str:
        for token in tokens:
            if token in text:
                return token
        return ""
