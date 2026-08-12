from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.config import LINKEDIN_PROFILE_ROOT
from app.db.session import SessionLocal
from app.linkedin.playwright.browser_context import BrowserContextConfig
from app.linkedin.playwright.playwright_factory import PlaywrightFactory
from app.models.entities import CompanyEntity

logger = logging.getLogger(__name__)
_onboarding_threads: dict[str, threading.Thread] = {}
_lock = threading.Lock()


def profile_path_for_agency(agency_id: str) -> str:
    root = Path(LINKEDIN_PROFILE_ROOT).expanduser().resolve()
    if not str(LINKEDIN_PROFILE_ROOT).strip():
        raise ValueError("LINKEDIN_PROFILE_ROOT is required")
    return str((root / str(agency_id)).resolve())


def start_linkedin_onboarding(agency_id: str) -> str:
    """Launch a visible, manual-login onboarding session after agency commit."""
    agency_id = str(agency_id)
    profile_path = profile_path_for_agency(agency_id)
    logger.info("starting LinkedIn onboarding agency_id=%s profile_path=%s", agency_id, profile_path)
    Path(profile_path).mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        row = db.scalar(select(CompanyEntity).where(CompanyEntity.id == agency_id))
        if row is None:
            raise ValueError("Agency not found")
        row.linkedin_profile_path = profile_path
        row.linkedin_connected = False
        row.linkedin_connection_status = "pending"
        db.commit()
    with _lock:
        current = _onboarding_threads.get(agency_id)
        if current and current.is_alive():
            return profile_path
        thread = threading.Thread(target=_run_onboarding, args=(agency_id, profile_path), daemon=True, name=f"linkedin-onboarding-{agency_id}")
        _onboarding_threads[agency_id] = thread
        thread.start()
        logger.info("LinkedIn onboarding worker started agency_id=%s profile_path=%s", agency_id, profile_path)
    return profile_path


def _run_onboarding(agency_id: str, profile_path: str) -> None:
    try:
        asyncio.run(_run_onboarding_async(agency_id, profile_path))
    except Exception:
        logger.exception("linkedin onboarding failed agency_id=%s", agency_id)
        _mark_failed(agency_id)


async def _run_onboarding_async(agency_id: str, profile_path: str) -> None:
    # This is intentionally independent from all LinkedIn production workers.
    # It only launches a persistent context, opens /login, and observes the
    # resulting page for authenticated UI signals after manual login.
    config = BrowserContextConfig(headless=False, profile_root=str(Path(profile_path).parent))
    factory = PlaywrightFactory(config)
    playwright = None
    context = None
    authenticated = False
    try:
        logger.info("launching Playwright agency_id=%s profile_path=%s", agency_id, profile_path)
        playwright = await factory.start_playwright()
        launch_config = factory.launch_config_for_profile(profile_path)
        context = await playwright.chromium.launch_persistent_context(
            **launch_config,
            headless=False,
        )
        pages = list(getattr(context, "pages", []) or [])
        page = pages[0] if pages else await context.new_page()
        for extra_page in pages[1:]:
            await extra_page.close()
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        logger.info("waiting for LinkedIn login agency_id=%s", agency_id)
        while True:
            if context.is_closed() or page.is_closed():
                logger.info("LinkedIn onboarding browser closed before authentication agency_id=%s", agency_id)
                _mark_failed(agency_id)
                return

            if await _has_authenticated_linkedin_ui(page):
                logger.info("LinkedIn authenticated agency_id=%s", agency_id)
                authenticated = True
                break
            await asyncio.sleep(2)
    except Exception:
        logger.exception("linkedin onboarding browser error agency_id=%s", agency_id)
        _mark_failed(agency_id)
    finally:
        try:
            if context is not None and not context.is_closed():
                await context.close()
            if playwright is not None:
                await playwright.stop()
        except Exception:
            logger.warning("linkedin onboarding browser already closed agency_id=%s", agency_id)
            authenticated = False
        if authenticated:
            logger.info("LinkedIn profile saved agency_id=%s profile_path=%s", agency_id, profile_path)
            _mark_connected(agency_id, profile_path)


async def _has_authenticated_linkedin_ui(page: object) -> bool:
    """Return true only when the current page exposes authenticated UI signals."""
    try:
        current_url = str(getattr(page, "url", "") or "").lower()
        if any(token in current_url for token in ("/login", "/uas/login", "/checkpoint", "/challenge")):
            return False
        selectors = (
            "[data-test-global-nav]",
            "[aria-label='Home']",
            "[aria-label*='Me']",
            "a[href='/feed/']",
            "input[placeholder*='Search']",
            "input[aria-label*='Search']",
        )
        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.is_visible():
                return True
    except Exception:
        return False
    return False


def _mark_connected(agency_id: str, profile_path: str) -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.scalar(select(CompanyEntity).where(CompanyEntity.id == agency_id))
        if row:
            row.linkedin_connected = True
            row.linkedin_connection_status = "connected"
            row.linkedin_connected_at = row.linkedin_connected_at or now
            row.linkedin_last_verified_at = now
            row.linkedin_profile_path = profile_path
            db.commit()
            logger.info("LinkedIn metadata updated agency_id=%s status=connected", agency_id)


def _mark_failed(agency_id: str) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(CompanyEntity).where(CompanyEntity.id == agency_id))
        if row:
            row.linkedin_connected = False
            row.linkedin_connection_status = "failed"
            db.commit()
            logger.info("LinkedIn metadata updated agency_id=%s status=failed", agency_id)
