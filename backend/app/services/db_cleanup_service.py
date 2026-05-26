"""
DB cleanup service — removes expired/stale rows that accumulate over time.

Runs periodically from the scheduler to keep the database lean.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def run_db_cleanup(db: Session) -> dict[str, int]:
    """
    Remove:
    - Used/expired OTPs older than 1 hour
    - Ranking run rows older than 30 days (keep last 500 per job)
    - Expired interview sessions older than 7 days
    """
    now = datetime.now(timezone.utc)
    results: dict[str, int] = {}

    # Clean up expired OTPs
    try:
        cutoff_otp = now - timedelta(hours=1)
        result = db.execute(
            text(
                "DELETE FROM otps WHERE (used = true OR expires_at < :cutoff) AND created_at < :cutoff"
            ),
            {"cutoff": cutoff_otp},
        )
        deleted_otps = result.rowcount or 0
        results["otps_deleted"] = deleted_otps
        if deleted_otps:
            logger.info("db_cleanup_otps deleted=%s", deleted_otps)
    except Exception as exc:
        logger.warning("db_cleanup_otps_failed error=%s", str(exc))
        results["otps_deleted"] = 0

    # Clean up old ranking runs (keep last 30 days)
    try:
        cutoff_runs = now - timedelta(days=30)
        result = db.execute(
            text("DELETE FROM ranking_runs WHERE created_at < :cutoff"),
            {"cutoff": cutoff_runs},
        )
        deleted_runs = result.rowcount or 0
        results["ranking_runs_deleted"] = deleted_runs
        if deleted_runs:
            logger.info("db_cleanup_ranking_runs deleted=%s", deleted_runs)
    except Exception as exc:
        logger.warning("db_cleanup_ranking_runs_failed error=%s", str(exc))
        results["ranking_runs_deleted"] = 0

    # Clean up expired interview sessions older than 7 days
    try:
        cutoff_sessions = now - timedelta(days=7)
        result = db.execute(
            text(
                "DELETE FROM interview_sessions WHERE expires_at < :cutoff AND status != 'interview_scheduled'"
            ),
            {"cutoff": cutoff_sessions},
        )
        deleted_sessions = result.rowcount or 0
        results["interview_sessions_deleted"] = deleted_sessions
        if deleted_sessions:
            logger.info("db_cleanup_interview_sessions deleted=%s", deleted_sessions)
    except Exception as exc:
        logger.warning("db_cleanup_interview_sessions_failed error=%s", str(exc))
        results["interview_sessions_deleted"] = 0

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("db_cleanup_commit_failed error=%s", str(exc))

    return results
