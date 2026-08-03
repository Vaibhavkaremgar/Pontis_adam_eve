from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.config import LINKEDIN_PROFILE_ROOT
from app.db.session import SessionLocal
from app.linkedin.playwright.browser_context import BrowserContextConfig
from app.linkedin.playwright.browser_manager import BrowserManager
from app.linkedin.playwright.session_manager import SessionManager
from app.linkedin.playwright.browser_types import BrowserSessionStatus
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
    # Onboarding must be interactive. It never receives credentials and never
    # calls storage_state(), cookies(), or any equivalent export API.
    config = BrowserContextConfig(headless=False, profile_root=str(Path(profile_path).parent))
    manager = BrowserManager(account_id=agency_id, config=config, profile_path=profile_path)
    started_at = time.monotonic()
    authenticated = False
    try:
        logger.info("launching Playwright agency_id=%s profile_path=%s", agency_id, profile_path)
        context = await manager.start()
        page = (list(context.pages)[0] if getattr(context, "pages", None) else await context.new_page())
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        logger.info("waiting for LinkedIn login agency_id=%s", agency_id)
        while True:
            if not manager.is_context_alive() or not manager.is_connected():
                _mark_failed(agency_id)
                return
            status = await SessionManager(context).detect_session_status()
            if status == BrowserSessionStatus.LOGGED_IN:
                logger.info("LinkedIn authenticated agency_id=%s", agency_id)
                authenticated = True
                break
            if status == BrowserSessionStatus.SESSION_EXPIRED:
                _mark_failed(agency_id)
                return
            if time.monotonic() - started_at >= 15 * 60:
                _mark_failed(agency_id)
                return
            await asyncio.sleep(2)
    except Exception:
        logger.exception("linkedin onboarding browser error agency_id=%s", agency_id)
        _mark_failed(agency_id)
    finally:
        try:
            await manager.stop()
        except Exception:
            logger.exception("linkedin onboarding browser cleanup failed agency_id=%s", agency_id)
            authenticated = False
        if authenticated:
            _mark_connected(agency_id, profile_path)


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
            logger.info("LinkedIn profile saved agency_id=%s profile_path=%s", agency_id, profile_path)
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
