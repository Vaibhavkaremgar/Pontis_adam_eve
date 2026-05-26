from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import APOLLO_ENRICHMENT_ENABLED, SOURCE_PROVIDER
from app.db.repositories import CandidateProfileRepository
from app.services.apollo_enrichment_service import enrich_candidate_with_apollo
from app.services.metrics_service import log_metric
from app.services.sourcing.candidate_matching_service import build_apollo_match_trace

logger = logging.getLogger(__name__)


def enrich_selected_candidate(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    source_type: str = "linkedin_xray",
    workflow_token: str = "",
    selection_session_id: str = "",
    automation_job_id: str = "",
) -> dict[str, Any]:
    if SOURCE_PROVIDER != "xray_apollo" or not APOLLO_ENRICHMENT_ENABLED:
        logger.info(
            "[apollo_enrichment] skipped source_provider=%s enabled=%s job_id=%s candidate_id=%s",
            SOURCE_PROVIDER,
            APOLLO_ENRICHMENT_ENABLED,
            job_id,
            candidate_id,
        )
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "failed",
            "reason": "apollo_enrichment_disabled",
            "shouldOutreach": False,
        }

    logger.info(
        "[apollo_search] start job_id=%s candidate_id=%s source_type=%s automation_job_id=%s",
        job_id,
        candidate_id,
        source_type,
        automation_job_id,
    )
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    raw_profile = dict(getattr(profile, "raw_data", {}) or {}) if profile else {}
    candidate = {
        "linkedin_url": raw_profile.get("linkedin_url") or raw_profile.get("linkedinUrl") or getattr(profile, "linkedin_url", "") or "",
        "full_name": getattr(profile, "name", "") or raw_profile.get("full_name") or raw_profile.get("name") or "",
        "current_company": getattr(profile, "company", "") or raw_profile.get("current_company") or raw_profile.get("company") or "",
        "title": getattr(profile, "role", "") or raw_profile.get("title") or raw_profile.get("headline") or "",
        "location": raw_profile.get("location") or "",
    }
    result = enrich_candidate_with_apollo(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        source_type=source_type,
        workflow_token=workflow_token,
        selection_session_id=selection_session_id,
        automation_job_id=automation_job_id,
    )
    status = str(result.get("status") or "").strip().lower()
    confidence = float(result.get("confidence") or result.get("identityMatchConfidence") or 0.0)
    person = result.get("person") if isinstance(result.get("person"), dict) else None
    trace = build_apollo_match_trace(candidate=candidate, person=person, confidence=confidence, status=status)
    logger.info(
        "[apollo_match] job_id=%s candidate_id=%s match_type=%s confidence=%.4f matched_fields=%s",
        job_id,
        candidate_id,
        trace.get("matchType", ""),
        float(trace.get("confidence") or 0.0),
        trace.get("matchedFields") or [],
    )
    if status in {"failed", "no_match_found", "ambiguous_match"}:
        logger.warning(
            "[apollo_reject] job_id=%s candidate_id=%s status=%s reason=%s",
            job_id,
            candidate_id,
            status,
            result.get("reason") or "",
        )
    else:
        logger.info(
            "[apollo_enrichment] job_id=%s candidate_id=%s status=%s should_outreach=%s email_status=%s",
            job_id,
            candidate_id,
            status,
            bool(result.get("shouldOutreach")),
            result.get("emailStatus") or result.get("email_status") or "",
        )
    log_metric(
        "apollo_enrichment",
        job_id=job_id,
        candidate_id=candidate_id,
        status=status,
        confidence=round(confidence, 4),
    )
    return result
