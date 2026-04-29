from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from app.core.config import (
    ATS_RETRY_INTERVAL_MINUTES,
    ENABLE_FOLLOWUPS,
    ENABLE_REPLY_POLLING,
    NO_CANDIDATES_COOLDOWN_MINUTES,
    REPLY_POLL_INTERVAL_MINUTES,
    REFRESH_CANDIDATE_LIMIT,
    REFRESH_INTERVAL_MINUTES,
    OUTREACH_FOLLOWUP_INTERVAL_MINUTES,
    REFRESH_CRON_ENABLED,
    REFRESH_JOB_SCAN_LIMIT,
    REFRESH_MIN_WINDOW_MINUTES,
)
from app.db.repositories import CandidateProfileRepository, JobRepository
from app.db.session import SessionLocal
from app.services.candidate_refresh_service import refresh_candidates
from app.services.candidate_service import refresh_candidates_for_job

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()
_status_lock = threading.Lock()

# Cycle stats
_last_candidate_refresh_at: datetime | None = None
_last_candidate_flywheel_at: datetime | None = None
_last_followup_cycle_at: datetime | None = None
_last_ats_retry_cycle_at: datetime | None = None
_last_reply_poll_cycle_at: datetime | None = None
_last_cycle_jobs_attempted = 0
_last_cycle_jobs_refreshed = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _should_skip_recent_refresh(db, *, job_id: str) -> bool:
    minimum_window = timedelta(minutes=max(1, REFRESH_MIN_WINDOW_MINUTES))
    rows = CandidateProfileRepository(db).list_for_job(job_id)
    if not rows:
        return False
    last_refreshed_at = max((row.last_refreshed_at for row in rows if row.last_refreshed_at), default=None)
    if not last_refreshed_at:
        return False
    return (_utcnow() - last_refreshed_at) < minimum_window


def _run_candidate_refresh_cycle() -> None:
    """Refresh candidate data for recent jobs."""
    global _last_candidate_refresh_at, _last_cycle_jobs_attempted, _last_cycle_jobs_refreshed

    logger.info("refresh_started")
    with _status_lock:
        _last_candidate_refresh_at = _utcnow()
        _last_cycle_jobs_attempted = 0
        _last_cycle_jobs_refreshed = 0

    try:
        with SessionLocal() as db:
            job_repo = JobRepository(db)
            jobs = job_repo.list_recent(limit=REFRESH_JOB_SCAN_LIMIT)
            now = _utcnow()
            for job in jobs:
                with _status_lock:
                    _last_cycle_jobs_attempted += 1
                try:
                    job_status = (job.job_status or "active").strip().lower()
                    if job_status == "no_candidates":
                        last_attempt = job.last_candidate_attempt_at
                        cooldown = timedelta(minutes=max(1, NO_CANDIDATES_COOLDOWN_MINUTES))
                        if not last_attempt or (now - last_attempt) < cooldown:
                            logger.info("scheduler_skipped_due_to_empty job_id=%s job_status=%s", job.id, job_status)
                            continue
                        logger.info("scheduler_retry_after_cooldown job_id=%s job_status=%s", job.id, job_status)

                    if _should_skip_recent_refresh(db, job_id=job.id):
                        logger.info("candidate_refresh_skip job_id=%s reason=window_guard", job.id)
                        continue

                    refreshed = refresh_candidates_for_job(
                        db=db,
                        job_id=job.id,
                        mode=(job.vetting_mode or "volume").strip().lower(),
                        refresh=(job_status == "no_candidates"),
                    )
                    with _status_lock:
                        _last_cycle_jobs_refreshed += 1
                    logger.info("candidate_refresh job_id=%s refreshed=%s", job.id, refreshed)
                except Exception as exc:
                    logger.warning("candidate_refresh_failed job_id=%s error=%s", job.id, str(exc), exc_info=exc)
                    db.rollback()
    finally:
        logger.info("refresh_completed")


def _run_candidate_flywheel_cycle() -> None:
    """Refresh stale candidate records and re-embed them in Qdrant."""
    global _last_candidate_flywheel_at

    with _status_lock:
        _last_candidate_flywheel_at = _utcnow()

    try:
        result = refresh_candidates(batch_size=REFRESH_CANDIDATE_LIMIT)
        logger.info(
            "candidate_flywheel_cycle_complete processed=%s refreshed=%s skipped=%s",
            result.get("processed", 0),
            result.get("refreshed", 0),
            result.get("skipped", 0),
        )
    except Exception as exc:
        logger.warning("candidate_flywheel_cycle_failed error=%s", str(exc), exc_info=exc)


def _run_followup_cycle() -> None:
    """Send follow-up emails to candidates who haven't replied."""
    global _last_followup_cycle_at

    with _status_lock:
        _last_followup_cycle_at = _utcnow()

    with SessionLocal() as db:
        try:
            from app.services.outreach_service import run_followup_cycle

            result = run_followup_cycle(db)
            logger.info("followup_cycle_complete sent=%s skipped=%s", result.get("sent", 0), result.get("skipped", 0))
        except Exception as exc:
            logger.error("followup_cycle_failed error=%s", str(exc), exc_info=exc)
            db.rollback()


def _run_ats_retry_cycle() -> None:
    """Retry failed ATS exports."""
    global _last_ats_retry_cycle_at

    with _status_lock:
        _last_ats_retry_cycle_at = _utcnow()

    with SessionLocal() as db:
        try:
            from app.services.ats_service import run_ats_retry_cycle

            result = run_ats_retry_cycle(db)
            logger.info(
                "ats_retry_cycle_complete succeeded=%s failed=%s exhausted=%s",
                result.get("succeeded", 0), result.get("failed", 0), result.get("exhausted", 0),
            )
        except Exception as exc:
            logger.error("ats_retry_cycle_failed error=%s", str(exc), exc_info=exc)
            db.rollback()


def _run_reply_poll_cycle() -> None:
    """Poll the reply inbox for new candidate responses."""
    global _last_reply_poll_cycle_at

    with _status_lock:
        _last_reply_poll_cycle_at = _utcnow()

    try:
        from app.services.reply_polling_service import poll_candidate_replies

        with SessionLocal() as db:
            result = poll_candidate_replies(db=db)
            logger.info(
                "reply_poll_cycle_complete checked=%s matched=%s stored=%s ignored=%s failed=%s",
                result.get("checked", 0),
                result.get("matched", 0),
                result.get("stored", 0),
                result.get("ignored", 0),
                result.get("failed", 0),
            )
    except Exception as exc:
        logger.error("reply_poll_cycle_failed error=%s", str(exc), exc_info=exc)


def _run_loop() -> None:
    """
    Unified scheduler loop.
    Candidate refresh, follow-ups, and ATS retries are evaluated against DB state
    and service-level due checks rather than in-memory cron timers.
    """
    while not _scheduler_stop.is_set():
        cycle_started_at = _utcnow()
        logger.info("scheduler_cycle_started at=%s", cycle_started_at.isoformat())
        try:
            _run_candidate_refresh_cycle()
        except Exception as exc:
            logger.warning("candidate_refresh_cycle_exception error=%s", str(exc), exc_info=exc)

        try:
            _run_candidate_flywheel_cycle()
        except Exception as exc:
            logger.warning("candidate_flywheel_cycle_exception error=%s", str(exc), exc_info=exc)

        if ENABLE_FOLLOWUPS:
            try:
                _run_followup_cycle()
            except Exception as exc:
                logger.warning("followup_cycle_exception error=%s", str(exc), exc_info=exc)

        if ENABLE_REPLY_POLLING:
            try:
                last_poll = _last_reply_poll_cycle_at
                poll_interval = timedelta(minutes=max(1, REPLY_POLL_INTERVAL_MINUTES))
                if not last_poll or (_utcnow() - last_poll) >= poll_interval:
                    _run_reply_poll_cycle()
            except Exception as exc:
                logger.warning("reply_poll_cycle_exception error=%s", str(exc), exc_info=exc)

        try:
            _run_ats_retry_cycle()
        except Exception as exc:
            logger.warning("ats_retry_cycle_exception error=%s", str(exc), exc_info=exc)

        logger.info("scheduler_cycle_completed at=%s", _utcnow().isoformat())
        _scheduler_stop.wait(30)


def start_scheduler() -> None:
    global _scheduler_thread

    if not REFRESH_CRON_ENABLED:
        logger.info("Scheduler disabled by configuration")
        return
    if _scheduler_thread and _scheduler_thread.is_alive():
        return

    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(target=_run_loop, name="pontis-scheduler", daemon=True)
    _scheduler_thread.start()
    logger.info(
        "Scheduler started: db-driven cycles enabled (followups=%s, followup_interval_config=%sm, ats_retry_interval_config=%sm)",
        ENABLE_FOLLOWUPS, OUTREACH_FOLLOWUP_INTERVAL_MINUTES, ATS_RETRY_INTERVAL_MINUTES,
    )


def stop_scheduler() -> None:
    _scheduler_stop.set()


def scheduler_status() -> dict:
    with _status_lock:
        running = bool(_scheduler_thread and _scheduler_thread.is_alive() and not _scheduler_stop.is_set())
        return {
            "running": running,
            "candidate_refresh_interval_minutes": REFRESH_INTERVAL_MINUTES,
            "candidate_flywheel_interval_minutes": REFRESH_INTERVAL_MINUTES,
            "followup_interval_minutes": OUTREACH_FOLLOWUP_INTERVAL_MINUTES,
            "ats_retry_interval_minutes": ATS_RETRY_INTERVAL_MINUTES,
            "last_candidate_refresh_at": _last_candidate_refresh_at.isoformat() if _last_candidate_refresh_at else None,
            "last_candidate_flywheel_at": _last_candidate_flywheel_at.isoformat() if _last_candidate_flywheel_at else None,
            "last_followup_cycle_at": _last_followup_cycle_at.isoformat() if _last_followup_cycle_at else None,
            "last_ats_retry_cycle_at": _last_ats_retry_cycle_at.isoformat() if _last_ats_retry_cycle_at else None,
            "last_reply_poll_cycle_at": _last_reply_poll_cycle_at.isoformat() if _last_reply_poll_cycle_at else None,
            "last_cycle_jobs_attempted": _last_cycle_jobs_attempted,
            "last_cycle_jobs_refreshed": _last_cycle_jobs_refreshed,
            "reply_poll_interval_minutes": REPLY_POLL_INTERVAL_MINUTES,
        }
