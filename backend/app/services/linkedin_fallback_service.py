"""linkedin_fallback_service.py — Internal-candidate → LinkedIn fallback.

When a newly created job has zero qualified internal candidates, this service
enqueues the existing ``linkedin_job_posting`` queue job so the Playwright
worker can post the job to LinkedIn.

Design constraints:
- Reuses the existing ``linkedin_job_posting`` queue type and handler verbatim.
- Reuses ``match_internal_candidates_for_job`` result's ``fallback_eligible``
  flag — no new threshold invented.
- Duplicate prevention: reads ``structured_data["linkedinPosting"]["status"]``
  before enqueuing; skips if a posting is already pending/in-progress/posted.
- Respects ``LINKEDIN_JOB_POST_MODE`` (DRY_RUN / LIVE) via the existing worker.
- Never touches Eve, the consent flow, or the matching engine.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from sqlalchemy.orm import Session

from app.core.config import EVE_BASE_URL, LINKEDIN_JOB_POST_MODE
from app.db.repositories import JobRepository
from app.services.job_queue_service import enqueue_job

logger = logging.getLogger(__name__)

# Statuses that mean a LinkedIn posting is already in flight or done.
# If any of these are present we must NOT create a duplicate.
_TERMINAL_OR_ACTIVE_STATUSES = frozenset(
    {
        "queued",
        "processing",
        "ok",
        "skipped",
        "posted",
        "dry_run_ok",
    }
)


def build_eve_application_url() -> str:
    """Return the Eve application URL. No job_id — candidates enter the global talent pool."""
    base_url = (EVE_BASE_URL or "").rstrip("/")
    if not base_url:
        raise ValueError("EVE_BASE_URL is required to build the Eve application URL")
    return f"{base_url}/application"


def _linkedin_posting_status(job: Any) -> str:
    """Return the current linkedinPosting status from structured_data, or ''."""
    structured = dict(getattr(job, "structured_data", {}) or {})
    posting = dict(structured.get("linkedinPosting") or {})
    return str(posting.get("status") or "").strip().lower()


def maybe_trigger_linkedin_fallback(
    *,
    db: Session,
    job_id: str,
    match_result: dict[str, Any],
) -> dict[str, Any]:
    """Trigger the LinkedIn job-posting fallback if no internal candidates matched.

    Called immediately after ``match_internal_candidates_for_job`` returns.
    Safe to call even when LinkedIn is not configured — the queue handler
    handles the ``linkedin_account_missing`` case gracefully.

    Args:
        db:           Active SQLAlchemy session (used to read/write job state).
        job_id:       The Adam job UUID.
        match_result: The dict returned by ``match_internal_candidates_for_job``.

    Returns:
        A dict describing what action was taken.
    """
    match_status: str = str(match_result.get("status") or "ok")

    # ── Guard: index_not_ready is a transient state, not a true "no candidates" ─
    if match_status == "index_not_ready":
        logger.info(
            "[linkedin-fallback] Internal index not ready for job %s — deferring fallback",
            job_id,
        )
        return {"triggered": False, "reason": "index_not_ready"}

    fallback_eligible: bool = bool(match_result.get("fallback_eligible", False))

    # ── Guard: only trigger when the matching engine says so ─────────────────
    if not fallback_eligible:
        logger.info(
            "[linkedin-fallback] Internal candidates found for job %s — fallback not triggered",
            job_id,
        )
        return {"triggered": False, "reason": "internal_candidates_sufficient"}

    logger.info(
        "[linkedin-fallback] No internal candidates for job %s",
        job_id,
    )

    # ── Guard: duplicate prevention ──────────────────────────────────────────
    job = JobRepository(db).get(job_id)
    if not job:
        logger.warning(
            "[linkedin-fallback] Job %s not found — cannot trigger LinkedIn posting",
            job_id,
        )
        return {"triggered": False, "reason": "job_not_found"}

    current_status = _linkedin_posting_status(job)
    if current_status in _TERMINAL_OR_ACTIVE_STATUSES:
        logger.info(
            "[linkedin-fallback] LinkedIn posting already in state %r for job %s — skipping duplicate",
            current_status,
            job_id,
        )
        return {"triggered": False, "reason": f"already_{current_status}"}

    # ── Mark the posting as queued in structured_data before enqueuing ───────
    # This is the idempotency anchor: if the scheduler runs again before the
    # worker picks up the job, the status check above will prevent a second enqueue.
    structured = dict(getattr(job, "structured_data", {}) or {})
    structured["linkedinPosting"] = {
        **dict(structured.get("linkedinPosting") or {}),
        "status": "queued",
        "applicationUrl": build_eve_application_url(),
        "triggeredBy": "linkedin_fallback",
        "triggeredAt": datetime.now(timezone.utc).isoformat(),
        "executionMode": LINKEDIN_JOB_POST_MODE,
    }
    JobRepository(db).update_structured_fields(job_id=job_id, structured_data=structured)
    db.flush()

    # ── Enqueue via the existing linkedin_job_posting queue ──────────────────
    idempotency_key = f"linkedin_fallback:{job_id}"
    try:
        result = enqueue_job(
            "linkedin_job_posting",
            {
                "job_id": job_id,
                "apply_url": structured["linkedinPosting"]["applicationUrl"],
            },
            idempotency_key=idempotency_key,
            max_attempts=3,
        )
        logger.info(
            "[linkedin-fallback] Triggering LinkedIn posting for job %s (mode=%s queue_job_id=%s)",
            job_id,
            LINKEDIN_JOB_POST_MODE,
            result.get("job_id"),
        )
        return {
            "triggered": True,
            "queue_job_id": result.get("job_id"),
            "execution_mode": LINKEDIN_JOB_POST_MODE,
            "deduplicated": bool(result.get("deduplicated")),
        }
    except Exception as exc:
        # Roll back the structured_data update so the next scheduler cycle can retry.
        logger.error(
            "[linkedin-fallback] LinkedIn posting failed for job %s: %s",
            job_id,
            exc,
        )
        structured["linkedinPosting"]["status"] = "enqueue_failed"
        structured["linkedinPosting"]["lastError"] = str(exc)
        try:
            JobRepository(db).update_structured_fields(job_id=job_id, structured_data=structured)
            db.flush()
        except Exception:
            pass
        return {"triggered": False, "reason": "enqueue_failed", "error": str(exc)}
