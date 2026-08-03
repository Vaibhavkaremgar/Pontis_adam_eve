"""verification_helpers.py — Generic Playwright verification helpers.

Reusable across Job Posting, Company Pages, Applicant Forms, Recruiter Settings.
No LinkedIn-specific assumptions.

All helpers accept a *container* Locator to scope queries.
Pass page.locator("body") as container for page-wide checks.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


async def verify_text(
    container: Any,
    expected: str,
    *,
    selector: str = "*",
    timeout_ms: int = 5000,
    exact: bool = False,
) -> bool:
    """Return True if *expected* text is visible inside *container*."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            if exact:
                loc = container.get_by_text(expected, exact=True).first
            else:
                loc = container.locator(selector).filter(has_text=expected).first
            if await loc.is_visible():
                logger.debug("verify_text ok expected=%r", expected)
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    logger.debug("verify_text fail expected=%r", expected)
    return False


async def verify_dropdown(
    container: Any,
    selector: str,
    expected_value: str,
    *,
    timeout_ms: int = 3000,
) -> bool:
    """Return True if a dropdown/select shows *expected_value*."""
    try:
        loc = container.locator(selector).first
        # native <select>
        try:
            value = await loc.input_value(timeout=timeout_ms)
            if expected_value.lower() in value.lower():
                return True
        except Exception:
            pass
        # ARIA combobox / listbox
        for attr in ("aria-label", "value", "data-value"):
            try:
                val = str(await loc.get_attribute(attr) or "")
                if expected_value.lower() in val.lower():
                    return True
            except Exception:
                pass
        # inner text fallback
        text = str(await loc.inner_text(timeout=timeout_ms) or "")
        return expected_value.lower() in text.lower()
    except Exception as exc:
        logger.debug("verify_dropdown error: %s", exc)
        return False


async def verify_checkbox(
    container: Any,
    selector: str,
    *,
    expected_checked: bool = True,
    timeout_ms: int = 3000,
) -> bool:
    """Return True if the checkbox matches *expected_checked* state."""
    try:
        loc = container.locator(selector).first
        checked = await loc.is_checked()
        return checked == expected_checked
    except Exception as exc:
        logger.debug("verify_checkbox error: %s", exc)
        return False


async def verify_upload(
    container: Any,
    selector: str,
    filename: str,
    *,
    timeout_ms: int = 5000,
) -> bool:
    """Return True if an uploaded file name is visible in the container."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            # Check input value
            loc = container.locator(selector).first
            val = str(await loc.input_value(timeout=1000) or "")
            if filename in val:
                return True
        except Exception:
            pass
        # Check visible text anywhere in container
        try:
            if await container.locator(f"*:has-text('{filename}')").first.is_visible():
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    logger.debug("verify_upload fail filename=%r", filename)
    return False


async def verify_success(
    container: Any,
    *,
    timeout_ms: int = 8000,
) -> tuple[bool, str]:
    """Return (True, signal_name) when any success signal is detected.

    Checks: toast, banner, success text, URL change, button disabled.
    """
    from app.linkedin.playwright.success_detector import SuccessDetector
    detector = SuccessDetector()
    return await detector.detect(container, timeout_ms=timeout_ms)


async def verify_toast(
    container: Any,
    *,
    expected_text: str = "",
    timeout_ms: int = 5000,
) -> bool:
    """Return True if a toast notification is visible (optionally matching text)."""
    _TOAST_SELECTORS = [
        "[role='alert']",
        "[role='status']",
        "[class*='toast']",
        "[class*='notification']",
        "[class*='snackbar']",
        "[class*='banner']",
        "[aria-live='polite']",
        "[aria-live='assertive']",
    ]
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        for sel in _TOAST_SELECTORS:
            try:
                loc = container.locator(sel).first
                if not await loc.is_visible():
                    continue
                if not expected_text:
                    logger.debug("verify_toast ok sel=%s", sel)
                    return True
                text = str(await loc.inner_text(timeout=1000) or "")
                if expected_text.lower() in text.lower():
                    logger.debug("verify_toast ok sel=%s text=%r", sel, text[:60])
                    return True
            except Exception:
                continue
        await asyncio.sleep(0.2)
    logger.debug("verify_toast fail expected=%r", expected_text)
    return False


async def verify_dialog(
    container: Any,
    *,
    expected_text: str = "",
    timeout_ms: int = 5000,
) -> bool:
    """Return True if a dialog is visible (optionally matching text)."""
    _DIALOG_SELECTORS = [
        "[role='dialog']",
        "[role='alertdialog']",
        "dialog",
        "[class*='modal']",
    ]
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        for sel in _DIALOG_SELECTORS:
            try:
                loc = container.locator(sel).first
                if not await loc.is_visible():
                    continue
                if not expected_text:
                    return True
                text = str(await loc.inner_text(timeout=1000) or "")
                if expected_text.lower() in text.lower():
                    return True
            except Exception:
                continue
        await asyncio.sleep(0.2)
    return False


async def verify_navigation(
    page: Any,
    expected_url_fragment: str,
    *,
    timeout_ms: int = 10_000,
) -> bool:
    """Return True if the page URL contains *expected_url_fragment*."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            url = str(getattr(page, "url", "") or "")
            if expected_url_fragment in url:
                logger.debug("verify_navigation ok fragment=%r url=%s", expected_url_fragment, url)
                return True
        except Exception:
            pass
        await asyncio.sleep(0.25)
    logger.debug("verify_navigation fail fragment=%r", expected_url_fragment)
    return False
