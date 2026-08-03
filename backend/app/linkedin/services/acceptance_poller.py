from __future__ import annotations

import logging
from collections import defaultdict
from typing import TypedDict

from app.linkedin.playwright.browser_manager import BrowserManager
from app.linkedin.playwright.profile_inspector import LinkedInProfileInspector
from app.linkedin.playwright.profile_types import LinkedInProfileConnectionState

logger = logging.getLogger(__name__)

_ACCEPTED_STATES = {
    LinkedInProfileConnectionState.CONNECTED,
    LinkedInProfileConnectionState.MESSAGE_AVAILABLE,
}
_SESSION_EXPIRED_STATES = {
    LinkedInProfileConnectionState.LOGIN_REQUIRED,
    LinkedInProfileConnectionState.SESSION_EXPIRED,
    LinkedInProfileConnectionState.ACCOUNT_RESTRICTED,
}


class AcceptancePollerSummary(TypedDict):
    checked: int
    accepted: int
    pending: int
    failed: int


async def poll_pending_connections(*, timeout_ms: int = 30000) -> AcceptancePollerSummary:
    """Inspect all linkedin_connections with status='requested'.

    Groups rows by account_id. Launches one browser session per account and
    reuses it for every pending connection belonging to that account.

    Returns a summary dict: {checked, accepted, pending, failed}.
    """
    from app.db.session import SessionLocal
    from app.linkedin.repository import LinkedInAccountRepository, LinkedInConnectionRepository

    summary: AcceptancePollerSummary = {"checked": 0, "accepted": 0, "pending": 0, "failed": 0}

    db = SessionLocal()
    try:
        conn_repo = LinkedInConnectionRepository(db)
        pending_rows = conn_repo.list_pending()
    finally:
        db.close()

    if not pending_rows:
        logger.info("acceptance_poller no pending connections found")
        return summary

    # Group by account_id
    by_account: dict[str, list] = defaultdict(list)
    for row in pending_rows:
        by_account[str(row.account_id)].append(row)

    logger.info(
        "acceptance_poller starting accounts=%d connections=%d",
        len(by_account),
        len(pending_rows),
    )

    for account_id, connections in by_account.items():
        await _poll_account(
            account_id=account_id,
            connections=connections,
            timeout_ms=timeout_ms,
            summary=summary,
        )

    logger.info(
        "acceptance_poller finished checked=%d accepted=%d pending=%d failed=%d",
        summary["checked"],
        summary["accepted"],
        summary["pending"],
        summary["failed"],
    )
    return summary


async def _poll_account(
    *,
    account_id: str,
    connections: list,
    timeout_ms: int,
    summary: AcceptancePollerSummary,
) -> None:
    from app.db.session import SessionLocal
    from app.linkedin.repository import LinkedInAccountRepository, LinkedInConnectionRepository

    browser_manager = BrowserManager(account_id=account_id)
    try:
        browser_context = await browser_manager.get_browser()
    except Exception:
        logger.exception("acceptance_poller browser_start_failed account_id=%s", account_id)
        summary["failed"] += len(connections)
        return

    inspector = LinkedInProfileInspector(browser_context, timeout_ms=timeout_ms)

    try:
        for row in connections:
            connection_id = str(row.id)
            profile_url = str(row.linkedin_url or "")
            if not profile_url:
                logger.warning(
                    "acceptance_poller skipping empty linkedin_url connection_id=%s", connection_id
                )
                summary["failed"] += 1
                continue

            try:
                result = await inspector.inspect(profile_url)
            except Exception:
                logger.exception(
                    "acceptance_poller inspect_failed connection_id=%s profile_url=%s",
                    connection_id,
                    profile_url,
                )
                summary["failed"] += 1
                continue

            state = result.connection_state
            logger.info(
                "acceptance_poller inspected connection_id=%s state=%s profile_url=%s",
                connection_id,
                state.value,
                profile_url,
            )

            if state in _SESSION_EXPIRED_STATES:
                logger.warning(
                    "acceptance_poller session_expired account_id=%s state=%s — stopping account",
                    account_id,
                    state.value,
                )
                _mark_account_unhealthy(account_id)
                summary["failed"] += len(connections) - connections.index(row)
                return

            db = SessionLocal()
            try:
                conn_repo = LinkedInConnectionRepository(db)
                if state in _ACCEPTED_STATES:
                    conn_repo.mark_accepted(connection_id)
                    db.commit()
                    summary["accepted"] += 1
                    logger.info(
                        "acceptance_poller marked_accepted connection_id=%s", connection_id
                    )
                else:
                    conn_repo.mark_checked(connection_id)
                    db.commit()
                    summary["pending"] += 1
            except Exception:
                logger.exception(
                    "acceptance_poller db_update_failed connection_id=%s", connection_id
                )
                db.rollback()
                summary["failed"] += 1
            finally:
                db.close()

            summary["checked"] += 1

    finally:
        try:
            await browser_manager.stop()
        except Exception:
            logger.debug("acceptance_poller browser_stop_failed account_id=%s", account_id, exc_info=True)


def _mark_account_unhealthy(account_id: str) -> None:
    from app.db.session import SessionLocal
    from app.linkedin.repository import LinkedInAccountRepository

    db = SessionLocal()
    try:
        LinkedInAccountRepository(db).mark_unhealthy(account_id)
        db.commit()
        logger.warning("acceptance_poller account_marked_unhealthy account_id=%s", account_id)
    except Exception:
        logger.exception("acceptance_poller mark_unhealthy_failed account_id=%s", account_id)
        db.rollback()
    finally:
        db.close()
