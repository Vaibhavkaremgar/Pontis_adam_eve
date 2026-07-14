from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.linkedin.config import LINKEDIN_PROFILE_ROOT
from app.linkedin.playwright import BrowserManager, BrowserSessionStatus, SessionManager
from scripts.linkedin_dev_account import get_development_account_id


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run() -> int:
    started_at = datetime.now(timezone.utc)
    print(f"[{_now()}] Browser startup")
    account_id = get_development_account_id()
    profile_dir = Path(LINKEDIN_PROFILE_ROOT).expanduser().resolve() / account_id
    print(f"Selected LinkedIn account: {account_id}")
    print(f"Profile directory: {profile_dir}")
    print(f"Profile exists: {profile_dir.exists()}")
    manager = BrowserManager(account_id=account_id)
    context: Any | None = None
    exit_code = 0
    try:
        context = await manager.start()
        print(f"[{_now()}] Browser running status: {manager.is_running()}")
        page = await _current_page(context)
        await _wait_for_dom_ready(page)

        current_url = str(getattr(page, "url", "") or "")
        try:
            page_title = str(await page.title() or "")
        except Exception:
            page_title = ""

        print(f"[{_now()}] Page load")
        print(f"Current URL: {current_url}")
        print(f"Page title: {page_title}")

        session_status = await SessionManager(context).detect_session_status()
        print(f"[{_now()}] Session detection")
        print(f"Session status: {session_status.value}")

        if session_status == BrowserSessionStatus.LOGGED_IN:
            print("LOGGED_IN")
        elif session_status == BrowserSessionStatus.LOGIN_REQUIRED:
            print("LOGIN_REQUIRED")
            print("Manual login required.")
            print("Press ENTER in the terminal when you are done logging in.")
            await asyncio.to_thread(sys.stdin.readline)
        elif session_status == BrowserSessionStatus.SESSION_EXPIRED:
            print("SESSION_EXPIRED")
        else:
            print(session_status.value)

        return 0
    except Exception as exc:
        logger.exception("linkedin smoke test failed")
        print(f"ERROR: {exc}")
        exit_code = 1
    finally:
        print(f"[{_now()}] Browser shutdown")
        try:
            await manager.stop()
        except Exception as exc:
            logger.exception("linkedin smoke test shutdown failed")
            print(f"Shutdown error: {exc}")
            exit_code = 1
        finished_at = datetime.now(timezone.utc)
        duration = (finished_at - started_at).total_seconds()
        print(f"Execution duration: {duration:.3f}s")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual smoke test for LinkedIn persistent profile session")
    parser.add_argument("--account", dest="account_id", default="", help="LinkedIn development account id")
    args = parser.parse_args()
    if args.account_id:
        os.environ["LINKEDIN_DEV_ACCOUNT_ID"] = args.account_id
    return asyncio.run(_run())


async def _current_page(context: Any) -> Any:
    pages = []
    try:
        pages = list(getattr(context, "pages", []) or [])
    except Exception:
        pages = []
    if pages:
        return pages[0]
    return await context.new_page()


async def _wait_for_dom_ready(page: Any) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
