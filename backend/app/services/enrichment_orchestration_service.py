"""
enrichment_orchestration_service.py  —  Sprint 6: Selection-Driven Enrichment.

Single canonical entry point for candidate enrichment.

Design rules
------------
* Enrichment only runs for explicitly selected/approved candidates.
* Dedup: if enrichment already completed recently or is in-progress, skip.
* All operations safe-fail — recruiter action flow never blocked.
* Uses existing AutomationJobEntity + unique automation_key as the dedup gate.
* Profile merge lives in apify_enrichment_service (unchanged).
* Enrichment state stored in CandidateProfileEntity.ats_metadata.enrichmentStatus
  and candidate_status column (both already exist — no schema change needed).

Enrichment state lifecycle
--------------------------
not_requested → queued → enriching → enriched / missing_email / failed / partial

Trigger states (enrichment allowed)
------------------------------------
  accept, shortlisted, selected, advanced

Skip states (enrichment blocked / not needed)
----------------------------------------------
  reject, pass, maybe, not_now (hold)
  already enriched recently (< ENRICH_RECENCY_HOURS)
  already in-progress
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────

# Actions that warrant enrichment
ENRICHMENT_TRIGGER_ACTIONS = {"accept", "selected", "shortlisted", "advanced"}

# Enrichment state values (canonical)
ENRICH_STATE_NOT_REQUESTED = "not_requested"
ENRICH_STATE_QUEUED        = "queued"
ENRICH_STATE_IN_PROGRESS   = "enriching"
ENRICH_STATE_COMPLETED     = "enriched"
ENRICH_STATE_MISSING_EMAIL = "missing_email"
ENRICH_STATE_FAILED        = "failed"
ENRICH_STATE_PARTIAL       = "partial"

# Terminal / skip states — don't re-enrich
_TERMINAL_STATES = {ENRICH_STATE_COMPLETED, ENRICH_STATE_MISSING_EMAIL, ENRICH_STATE_IN_PROGRESS}

# How recently must enrichment have succeeded to skip re-enrichment (hours)
ENRICH_RECENCY_HOURS: int = 48


# ── diagnostics dataclass ─────────────────────────────────────────────────────

@dataclass
class EnrichmentOrchestrationResult:
    candidate_id: str = ""
    job_id: str = ""
    action: str = ""
    triggered: bool = False
    skipped: bool = False
    skip_reason: str = ""          # "not_trigger_action"|"already_complete"|"in_progress"|"no_linkedin"|"queue_error"
    queue_job_id: str = ""
    enrichment_state: str = ENRICH_STATE_NOT_REQUESTED
    error: str = ""


# ── helpers ───────────────────────────────────────────────────────────────────

def _t(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _current_enrichment_state(profile: Any) -> str:
    """Read the current enrichment state from a CandidateProfileEntity."""
    ats_meta = dict(getattr(profile, "ats_metadata", {}) or {})
    state = _t(ats_meta.get("enrichmentStatus") or "")
    if state:
        return state.lower()
    # fall back to candidate_status if ats_metadata is empty
    candidate_status = _t(getattr(profile, "candidate_status", "") or "")
    status_map = {
        "enriched": ENRICH_STATE_COMPLETED,
        "enrichment_failed": ENRICH_STATE_FAILED,
        "missing_email": ENRICH_STATE_MISSING_EMAIL,
        "enriching": ENRICH_STATE_IN_PROGRESS,
    }
    return status_map.get(candidate_status.lower(), ENRICH_STATE_NOT_REQUESTED)


def _enrichment_completed_at(profile: Any) -> datetime | None:
    """Return when enrichment last completed, or None."""
    ats_meta = dict(getattr(profile, "ats_metadata", {}) or {})
    raw = _t(ats_meta.get("updatedAt") or ats_meta.get("enrichmentCompletedAt") or "")
    if not raw:
        last_refreshed = getattr(profile, "last_refreshed_at", None)
        if isinstance(last_refreshed, datetime):
            return last_refreshed
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _is_recently_enriched(profile: Any, *, recency_hours: int = ENRICH_RECENCY_HOURS) -> bool:
    """Return True if enrichment completed within recency_hours."""
    state = _current_enrichment_state(profile)
    if state not in {ENRICH_STATE_COMPLETED, ENRICH_STATE_MISSING_EMAIL}:
        return False
    completed_at = _enrichment_completed_at(profile)
    if completed_at is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, recency_hours))
    return completed_at >= cutoff


def _mark_enrichment_queued(profile: Any, db: Session) -> None:
    """Update profile ats_metadata to reflect queued state."""
    try:
        ats_meta = dict(getattr(profile, "ats_metadata", {}) or {})
        ats_meta["enrichmentStatus"] = ENRICH_STATE_QUEUED
        ats_meta["enrichmentRequestedAt"] = datetime.now(timezone.utc).isoformat()
        ats_meta["enrichmentSource"] = "enrichment_orchestration"
        profile.ats_metadata = ats_meta

        raw_data = dict(getattr(profile, "raw_data", {}) or {})
        enrichment = dict(raw_data.get("enrichment") or {})
        enrichment["status"] = ENRICH_STATE_QUEUED
        enrichment["enrichmentRequestedAt"] = ats_meta["enrichmentRequestedAt"]
        raw_data["enrichment"] = enrichment
        profile.raw_data = raw_data

        db.flush()
    except Exception as exc:
        logger.debug("enrichment_mark_queued_failed error=%s", str(exc))


# ── Phase 2: dedup guard ──────────────────────────────────────────────────────

def should_enrich(
    *,
    action: str,
    profile: Any,
    recency_hours: int = ENRICH_RECENCY_HOURS,
) -> tuple[bool, str]:
    """
    Determine if enrichment should run for this action + profile.

    Returns:
        (should_run, skip_reason)
        skip_reason is "" when should_run is True.
    """
    normalized_action = _t(action).lower()

    # Only enrich for explicit positive recruiter decisions
    if normalized_action not in ENRICHMENT_TRIGGER_ACTIONS:
        return False, "not_trigger_action"

    current_state = _current_enrichment_state(profile)

    # Already in-progress — don't start a duplicate
    if current_state == ENRICH_STATE_IN_PROGRESS:
        return False, "already_in_progress"

    # Recently enriched — reuse existing result
    if _is_recently_enriched(profile, recency_hours=recency_hours):
        return False, "already_completed_recently"

    # Check LinkedIn URL — enrichment is pointless without it
    raw_data = dict(getattr(profile, "raw_data", {}) or {})
    linkedin_url = _t(
        raw_data.get("linkedin_url")
        or raw_data.get("linkedinUrl")
        or raw_data.get("source_url")
        or getattr(profile, "linkedin_url", "")
        or ""
    )
    if not linkedin_url or "linkedin.com/in/" not in linkedin_url.lower():
        return False, "no_linkedin_url"

    return True, ""


# ── Phase 3: single orchestration entry point ─────────────────────────────────

def request_enrichment(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    action: str,
    source_type: str = "ui",
    selection_session_id: str = "",
    linkedin_url: str = "",
    workflow_token: str = "",
) -> EnrichmentOrchestrationResult:
    """
    Single canonical enrichment entry point.

    Call this from:
      - apply_feedback (action = "accept")
      - submit_selection_choice (action = "selected")
      - any future trigger

    Returns EnrichmentOrchestrationResult — never raises.
    """
    result = EnrichmentOrchestrationResult(
        candidate_id=candidate_id,
        job_id=job_id,
        action=action,
    )

    try:
        from app.db.repositories import CandidateProfileRepository

        profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
        if not profile:
            result.skipped = True
            result.skip_reason = "candidate_profile_missing"
            logger.info(
                "enrichment_skipped job_id=%s candidate_id=%s reason=candidate_profile_missing",
                job_id, candidate_id,
            )
            return result

        # Resolve linkedin_url if not provided by caller
        if not linkedin_url:
            raw_data = dict(getattr(profile, "raw_data", {}) or {})
            linkedin_url = _t(
                raw_data.get("linkedin_url")
                or raw_data.get("linkedinUrl")
                or raw_data.get("source_url")
                or getattr(profile, "linkedin_url", "")
                or ""
            )

        run, skip_reason = should_enrich(action=action, profile=profile)
        result.enrichment_state = _current_enrichment_state(profile)

        if not run:
            result.skipped = True
            result.skip_reason = skip_reason
            logger.info(
                "enrichment_skipped job_id=%s candidate_id=%s action=%s reason=%s state=%s",
                job_id, candidate_id, action, skip_reason, result.enrichment_state,
            )
            return result

        # Mark as queued on profile before scheduling
        _mark_enrichment_queued(profile, db)
        result.enrichment_state = ENRICH_STATE_QUEUED

        # Schedule via AutomationJobEntity (has unique automation_key = dedup gate)
        automation_key = f"candidate-enrichment:{job_id}:{candidate_id}"
        try:
            from app.services.automation_service import schedule_automation_job

            sched = schedule_automation_job(
                db=db,
                automation_type="candidate_enrichment",
                job_id=job_id,
                candidate_id=candidate_id,
                run_at=datetime.now(timezone.utc),
                payload={
                    "feedbackAction": action,
                    "sourceType": source_type,
                    "selectionSessionId": selection_session_id,
                    "linkedinUrl": linkedin_url,
                    "workflowToken": workflow_token,
                },
                automation_key=automation_key,
            )
            result.triggered = True
            result.queue_job_id = _t(str(sched.get("automation_job_id") or sched.get("id") or ""))
            logger.info(
                "enrichment_queued job_id=%s candidate_id=%s action=%s automation_key=%s",
                job_id, candidate_id, action, automation_key,
            )
        except Exception as queue_exc:
            # If the automation_key already exists (IntegrityError), it's a dedup success
            err_str = str(queue_exc).lower()
            if "unique" in err_str or "duplicate" in err_str or "integrity" in err_str:
                result.skipped = True
                result.skip_reason = "dedup_key_exists"
                logger.info(
                    "enrichment_dedup_skip job_id=%s candidate_id=%s action=%s key=%s",
                    job_id, candidate_id, action, automation_key,
                )
            else:
                result.skipped = True
                result.skip_reason = "queue_error"
                result.error = str(queue_exc)[:200]
                logger.warning(
                    "enrichment_queue_failed job_id=%s candidate_id=%s error=%s",
                    job_id, candidate_id, str(queue_exc),
                )

    except Exception as exc:
        result.skipped = True
        result.skip_reason = "orchestration_error"
        result.error = str(exc)[:200]
        logger.warning(
            "enrichment_orchestration_error job_id=%s candidate_id=%s error=%s",
            job_id, candidate_id, str(exc),
        )

    return result


# ── Phase 7: surface enrichment state on candidate payload ────────────────────

def get_enrichment_state_payload(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    """
    Return enrichment-aware metadata for a candidate payload.

    Safe to call anywhere — returns empty dict on any error.
    """
    try:
        from app.db.repositories import CandidateProfileRepository
        profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
        if not profile:
            return {"enrichment_state": ENRICH_STATE_NOT_REQUESTED}

        ats_meta = dict(getattr(profile, "ats_metadata", {}) or {})
        state = _current_enrichment_state(profile)

        return {
            "enrichment_state": state,
            "enrichment_requested_at": _t(ats_meta.get("enrichmentRequestedAt") or ""),
            "enrichment_completed_at": _t(ats_meta.get("updatedAt") or ""),
            "enrichment_providers_used": [_t(ats_meta.get("enrichmentProvider") or "")],
            "enrichment_failed_reason": _t(ats_meta.get("enrichmentReason") or ""),
        }
    except Exception as exc:
        logger.debug("get_enrichment_state_payload_failed error=%s", str(exc))
        return {"enrichment_state": ENRICH_STATE_NOT_REQUESTED}


# ── Phase 5: dedup check for refresh pipeline ─────────────────────────────────

def is_enrichment_needed(
    profile: Any,
    *,
    force: bool = False,
    recency_hours: int = ENRICH_RECENCY_HOURS,
) -> bool:
    """
    Lightweight check used by candidate_refresh_service to skip redundant re-enrichment.

    Returns True if enrichment should run, False if it should be skipped.
    """
    if force:
        return True
    state = _current_enrichment_state(profile)
    if state == ENRICH_STATE_IN_PROGRESS:
        return False
    if _is_recently_enriched(profile, recency_hours=recency_hours):
        return False
    return True
