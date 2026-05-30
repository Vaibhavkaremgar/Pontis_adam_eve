from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.outreach_service import process_outreach

logger = logging.getLogger(__name__)


def trigger_outreach_after_enrichment(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    enrichment_result: dict[str, Any],
    selection_session_id: str = "",
    automation_job_id: str = "",
    source_type: str = "linkedin_xray",
) -> dict[str, Any]:
    status = str(enrichment_result.get("status") or "").strip().lower()
    should_outreach = bool(enrichment_result.get("shouldOutreach"))
    contact_email = str(enrichment_result.get("contactEmail") or "").strip()
    if status not in {"verified", "high_confidence"} or not should_outreach or not contact_email:
        logger.warning(
            "[outreach_trigger] skipped job_id=%s candidate_id=%s status=%s should_outreach=%s contact_email=%s reason=%s",
            job_id,
            candidate_id,
            status,
            should_outreach,
            contact_email or "missing",
            "missing_or_unverified_email" if not contact_email else "enrichment_not_ready",
        )
        return {"status": "skipped", "shouldOutreach": False}

    transition_candidate_ats_state(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        to_status="outreach_pending",
        source="apify_enrichment",
        reason="apify_enrichment_complete",
        metadata={
            "selectionSessionId": selection_session_id,
            "automationJobId": automation_job_id,
            "sourceType": source_type,
            "contactEmail": contact_email,
        },
    )
    try:
        result = process_outreach(
            db=db,
            job_id=job_id,
            selected_candidates=[candidate_id],
            custom_body="",
            recipient_email=contact_email,
        )
    except Exception as exc:
        logger.exception(
            "[outreach_trigger] failed job_id=%s candidate_id=%s recipient_email=%s error=%s",
            job_id,
            candidate_id,
            contact_email,
            str(exc),
        )
        raise

    outreach_status = str(result.get("status") or "").strip().lower()
    if outreach_status in {"sent", "delivered", "queued"}:
        logger.info(
            "[outreach_trigger] success job_id=%s candidate_id=%s status=%s provider=%s recipient_email=%s",
            job_id,
            candidate_id,
            outreach_status,
            result.get("provider") or "",
            contact_email,
        )
    else:
        logger.warning(
            "[outreach_trigger] not_sent job_id=%s candidate_id=%s status=%s reason=%s recipient_email=%s",
            job_id,
            candidate_id,
            outreach_status or "unknown",
            result.get("reason") or result.get("error") or "outreach_not_sent",
            contact_email,
        )
    return result
