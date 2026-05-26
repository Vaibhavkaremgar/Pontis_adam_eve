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
        logger.info(
            "[outreach_trigger] skipped job_id=%s candidate_id=%s status=%s should_outreach=%s",
            job_id,
            candidate_id,
            status,
            should_outreach,
        )
        return {"status": "skipped", "shouldOutreach": False}

    transition_candidate_ats_state(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        to_status="outreach_pending",
        source="apollo_enrichment",
        reason="apollo_enrichment_complete",
        metadata={
            "selectionSessionId": selection_session_id,
            "automationJobId": automation_job_id,
            "sourceType": source_type,
            "contactEmail": contact_email,
        },
    )
    result = process_outreach(
        db=db,
        job_id=job_id,
        selected_candidates=[candidate_id],
        custom_body="",
        recipient_email=contact_email,
    )
    logger.info(
        "[outreach_trigger] sent job_id=%s candidate_id=%s provider=%s",
        job_id,
        candidate_id,
        result.get("provider") or "",
    )
    return result
