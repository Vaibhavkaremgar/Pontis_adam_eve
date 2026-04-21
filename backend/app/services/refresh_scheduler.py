from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from app.core.config import REFRESH_CRON_ENABLED, REFRESH_INTERVAL_MINUTES, REFRESH_JOB_SCAN_LIMIT, REFRESH_MIN_WINDOW_MINUTES
from app.db.repositories import JobRepository
from app.db.session import SessionLocal
from app.services.candidate_service import refresh_candidates_for_job

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()
_job_refresh_locks: dict[str, threading.Lock] = {}
_last_job_refresh: dict[str, datetime] = {}
_status_lock = threading.Lock()
_last_cycle_started_at: datetime | None = None
_last_cycle_completed_at: datetime | None = None
_last_cycle_jobs_attempted = 0
_last_cycle_jobs_refreshed = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run_loop() -> None:
    interval_seconds = max(5, REFRESH_INTERVAL_MINUTES * 60)
    while not _scheduler_stop.is_set():
        try:
            _run_refresh_cycle()
        except Exception as exc:
            logger.warning("Candidate refresh cycle failed", exc_info=exc)

        _scheduler_stop.wait(interval_seconds)


def _should_skip_recent_refresh(job_id: str) -> bool:
    minimum_window = timedelta(minutes=max(1, REFRESH_MIN_WINDOW_MINUTES))
    last = _last_job_refresh.get(job_id)
    if not last:
        return False
    return (_utcnow() - last) < minimum_window


def _run_refresh_cycle() -> None:
    global _last_cycle_started_at, _last_cycle_completed_at, _last_cycle_jobs_attempted, _last_cycle_jobs_refreshed

    with _status_lock:
        _last_cycle_started_at = _utcnow()
        _last_cycle_jobs_attempted = 0
        _last_cycle_jobs_refreshed = 0

    with SessionLocal() as db:
        jobs = JobRepository(db).list_recent(limit=REFRESH_JOB_SCAN_LIMIT)
        for job in jobs:
            with _status_lock:
                _last_cycle_jobs_attempted += 1

            lock = _job_refresh_locks.setdefault(job.id, threading.Lock())
            if not lock.acquire(blocking=False):
                logger.info("candidate_refresh_skip job_id=%s reason=job_lock", job.id)
                continue
            try:
                if _should_skip_recent_refresh(job.id):
                    logger.info("candidate_refresh_skip job_id=%s reason=window_guard", job.id)
                    continue

                refreshed = refresh_candidates_for_job(db=db, job_id=job.id, mode="volume")
                _last_job_refresh[job.id] = _utcnow()
                with _status_lock:
                    _last_cycle_jobs_refreshed += 1
                logger.info("candidate_refresh job_id=%s refreshed=%s", job.id, refreshed)
            except Exception as exc:
                logger.warning("Failed refreshing candidates for job_id=%s", job.id, exc_info=exc)
                db.rollback()
            finally:
                lock.release()

    with _status_lock:
        _last_cycle_completed_at = _utcnow()


def start_scheduler() -> None:
    global _scheduler_thread

    if not REFRESH_CRON_ENABLED:
        logger.info("Candidate refresh scheduler disabled by configuration")
        return
    if _scheduler_thread and _scheduler_thread.is_alive():
        return

    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(target=_run_loop, name="candidate-refresh-scheduler", daemon=True)
    _scheduler_thread.start()
    logger.info("Candidate refresh scheduler started with interval_minutes=%s", REFRESH_INTERVAL_MINUTES)


def stop_scheduler() -> None:
    _scheduler_stop.set()


def scheduler_status() -> dict:
    with _status_lock:
        running = bool(_scheduler_thread and _scheduler_thread.is_alive() and not _scheduler_stop.is_set())
        return {
            "running": running,
            "interval_minutes": REFRESH_INTERVAL_MINUTES,
            "min_window_minutes": REFRESH_MIN_WINDOW_MINUTES,
            "last_cycle_started_at": _last_cycle_started_at.isoformat() if _last_cycle_started_at else None,
            "last_cycle_completed_at": _last_cycle_completed_at.isoformat() if _last_cycle_completed_at else None,
            "last_cycle_jobs_attempted": _last_cycle_jobs_attempted,
            "last_cycle_jobs_refreshed": _last_cycle_jobs_refreshed,
            "active_job_locks": len([job_id for job_id, lock in _job_refresh_locks.items() if lock.locked()]),
        }
