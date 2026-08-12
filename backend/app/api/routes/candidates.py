from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import INTERNAL_API_KEY
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.entities import JobEntity
from app.schemas.candidate import CandidateApplicationRequest, CandidateExportRequest, CandidateSelectionRequest, SwipeFeedbackRequest
from app.services.ats_service import export_to_ats
from app.services.candidate_application_service import submit_candidate_application
from app.services.candidate_service import apply_feedback
from app.services.candidate_selection_service import (
    get_final_selection_results,
    get_first_selection_batch,
    get_next_selection_batch,
    submit_selection_choice,
)
from app.services.ownership import assert_job_ownership, resolve_company_id_for_user
from app.services.candidate_request_service import create_interest_request, get_request_status, record_not_interested, request_state_map
from app.services.candidate_access_service import get_accepted_candidates, get_candidate_profile, get_pending_candidates
from app.services.candidate_response_service import get_pending_requests_for_candidate, respond_to_candidate_request
from app.utils.exceptions import APIError
from app.utils.responses import success_response
from app.services.candidate_presentation_service import build_candidate_view_model
from app.services.enrichment_orchestration_service import get_enrichment_state_payload
from app.services.internal_candidate_semantic_service import match_internal_candidates_for_job

router = APIRouter(tags=["candidates"])


def _resolve_agency_scope(db: Session, *, user_id: str, job_id: str) -> str:
    agency_id = resolve_company_id_for_user(db=db, user_id=user_id)
    if not agency_id:
        raise APIError("Forbidden", status_code=403)
    return agency_id


def _verify_internal_key(x_internal_api_key: str | None = Header(default=None, alias="X-Internal-Api-Key")) -> None:
    """Dependency: rejects requests that do not carry the correct internal API key.

    This gate is used on the Eve-facing transition endpoint so that Adam
    recruiters (who authenticate via JWT cookie) cannot call it directly.
    Eve will present this key when it eventually calls the endpoint.
    """
    if not INTERNAL_API_KEY or x_internal_api_key != INTERNAL_API_KEY:
        raise APIError("Forbidden", status_code=403)


# ── IMPORTANT: static routes MUST be registered before /{candidate_id} routes.
# FastAPI resolves path segments in registration order; if a dynamic route is
# registered first, "accepted" and "pending-acceptance" would be matched as
# candidate_id values instead of reaching their correct handlers.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/candidates")
def get_candidates(
    jobId: str = Query(...),
    mode: str | None = Query(None, pattern="^(volume|elite)$"),
    refresh: bool = Query(False),
    debug: bool = Query(False),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    agency_id = _resolve_agency_scope(db, user_id=_.get("id", ""), job_id=jobId)
    internal_result = match_internal_candidates_for_job(
        db=db,
        job_id=jobId,
        agency_id=agency_id,
        limit=None,
    )
    candidates = internal_result["candidates"]

    payload: list[dict] = []
    request_states = request_state_map(db, job_id=jobId, agency_id=agency_id)
    for candidate in candidates:
        base = candidate.model_dump(exclude_none=True)
        state = request_states.get(candidate.id, {})
        base["recruiterAction"] = state.get("recruiter_action", "NONE")
        base["requestStatus"] = state.get("status") or state.get("request_status")
        # profileAccess: FULL only when ACCEPTED; otherwise LIMITED.
        # The list endpoint never includes private fields — full data is fetched
        # separately via GET /candidates/{id}/profile.
        base["profileAccess"] = "FULL" if base["requestStatus"] == "ACCEPTED" else "LIMITED"
        vm = build_candidate_view_model(candidate)
        base["recruiterSummary"] = vm["recruiter_summary"]
        base["recruiterSummaryLines"] = vm["summary_lines"]
        base["fitScoreDisplay"] = vm["fit_score_display"]
        base["matchedSkills"] = vm["matched_skills"]
        base["linkedinUrl"] = vm["linkedin_url"] or base.get("linkedinUrl", "")
        payload.append(base)

    debug_payload = {
        "source": internal_result["source"],
        "retrievalCount": internal_result["retrieval_count"],
        "qualifiedInternalCount": internal_result["qualified_count"],
        "semanticTopK": internal_result["semantic_top_k"],
        "threshold": internal_result["threshold"],
        "minimumInternalMatches": internal_result["minimum_internal_matches"],
        "fallbackEligible": internal_result["fallback_eligible"],
        "fallbackReason": internal_result["fallback_reason"],
        "matchingDurationMs": internal_result["matching_duration_ms"],
    }
    no_results_reason = "" if payload else internal_result["fallback_reason"]
    if internal_result.get("status") == "index_not_ready":
        sourcing_state = "internal_index_not_ready"
    else:
        sourcing_state = "internal_delivered" if payload else "internal_pool_insufficient"

    response = success_response(payload)
    response.update(
        {
            "total": len(payload),
            "internalCandidates": payload,
            "externalCandidates": [],
            "fallbackEligible": internal_result["fallback_eligible"],
            "fallbackReason": internal_result["fallback_reason"],
            "sourcingState": sourcing_state,
            "noResultsReason": no_results_reason,
            "debug": debug_payload if debug or not payload else None,
        }
    )
    if not response["debug"]:
        response.pop("debug", None)

    return response


# ── Static routes (must come before /{candidate_id}) ─────────────────────────

@router.get("/candidates/accepted")
def get_accepted_candidates_for_job(
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all candidates with ACCEPTED requests for a job.
    Used to populate the Accepted section in the Results/Review UI.
    """
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    agency_id = _resolve_agency_scope(db, user_id=_.get("id", ""), job_id=jobId)
    candidates = get_accepted_candidates(db=db, job_id=jobId, agency_id=agency_id)
    return success_response(candidates)


@router.get("/candidates/pending-acceptance")
def get_pending_acceptance_candidates(
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all candidates with PENDING requests for a job.
    Used to populate the 'To Be Accepted' section.
    """
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    agency_id = _resolve_agency_scope(db, user_id=_.get("id", ""), job_id=jobId)
    candidates = get_pending_candidates(db=db, job_id=jobId, agency_id=agency_id)
    return success_response(candidates)


@router.get("/candidates/shortlisted")
def get_shortlisted_candidates(
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return only shortlisted candidates for a job — used by the outreach page."""
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    from app.services.candidate_service import list_shortlisted_candidates
    candidates = list_shortlisted_candidates(db=db, job_id=jobId)
    return success_response([candidate.model_dump() for candidate in candidates])


@router.post("/candidates/swipe")
def swipe_candidate(payload: SwipeFeedbackRequest, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=payload.jobId, user_id=_.get("id", ""))
    result = apply_feedback(
        db=db,
        job_id=payload.jobId,
        candidate_id=payload.candidateId,
        action=payload.action,
        reactivate_at=payload.reactivateAt,
    )
    return success_response(result)


@router.post("/candidates/export")
def export_candidates(payload: CandidateExportRequest, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=payload.jobId, user_id=request.state.user["id"])
    result = export_to_ats(
        db=db,
        job_id=payload.jobId,
        candidate_ids=payload.candidateIds,
        provider=payload.provider,
    )
    return success_response(result)


@router.post("/candidates/applications")
def submit_application(
    payload: CandidateApplicationRequest,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = submit_candidate_application(
        db=db,
        job_id=payload.jobId,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        resume_text=payload.resumeText,
        resume_file_name=payload.resumeFileName,
        resume_file_path=payload.resumeFilePath,
    )
    return success_response(result)


@router.get("/candidates/selection/first")
def get_first_candidate_batch(
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    result = get_first_selection_batch(db=db, job_id=jobId)
    return success_response(result)


@router.get("/candidates/selection/next")
def get_next_candidate_batch(
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    result = get_next_selection_batch(db=db, job_id=jobId)
    return success_response(result)


@router.get("/candidates/selection/final")
def get_final_candidate_selection(
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    result = get_final_selection_results(db=db, job_id=jobId)
    return success_response(result)


@router.post("/candidates/selection")
def select_candidate(
    payload: CandidateSelectionRequest,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=payload.jobId, user_id=request.state.user["id"])
    result = submit_selection_choice(db=db, job_id=payload.jobId, candidate_id=payload.candidateId)
    return success_response(result)


@router.post("/candidates/select")
def select_candidate_for_enrichment(
    payload: CandidateSelectionRequest,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=payload.jobId, user_id=request.state.user["id"])
    result = submit_selection_choice(db=db, job_id=payload.jobId, candidate_id=payload.candidateId)
    return success_response(result)


@router.get("/candidates/enrichment")
def get_candidate_enrichment_state(
    jobId: str = Query(...),
    candidateId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return current enrichment state for a specific candidate — used by frontend to poll."""
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    payload = get_enrichment_state_payload(db=db, job_id=jobId, candidate_id=candidateId)
    return success_response(payload)


# ── Dynamic routes (/{candidate_id} prefix) ───────────────────────────────────

@router.post("/candidates/{candidate_id}/interest")
def candidate_interest(candidate_id: str, jobId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    agency_id = _resolve_agency_scope(db, user_id=_.get("id", ""), job_id=jobId)
    result = create_interest_request(db=db, job_id=jobId, candidate_id=candidate_id, agency_id=agency_id, recruiter_id=_.get("id", ""))
    return success_response(result)


@router.post("/candidates/{candidate_id}/not-interested")
def candidate_not_interested(candidate_id: str, jobId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Mark a candidate as not interested.

    Business rule: if a PENDING interest request already exists for this
    candidate, this call returns 409. A recruiter cannot silently cancel a
    pending consent request — the candidate must respond first.

    This preserves the integrity of the consent workflow:
        Recruiter marks Interested → PENDING → Candidate must respond
    Allowing a recruiter to overwrite PENDING with NOT_INTERESTED would
    invalidate the candidate's outstanding consent request without notice.
    """
    agency_id = _resolve_agency_scope(db, user_id=_.get("id", ""), job_id=jobId)
    result = record_not_interested(db=db, job_id=jobId, candidate_id=candidate_id, agency_id=agency_id, recruiter_id=_.get("id", ""))
    return success_response(result)


@router.get("/candidates/{candidate_id}/request-status")
def candidate_request_status(candidate_id: str, jobId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    agency_id = _resolve_agency_scope(db, user_id=_.get("id", ""), job_id=jobId)
    return success_response(get_request_status(db=db, job_id=jobId, candidate_id=candidate_id, agency_id=agency_id))


@router.get("/candidates/{candidate_id}/profile")
def candidate_full_profile(
    candidate_id: str,
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return full or limited profile based on request acceptance status.

    Full profile (contact, resume, work experience) is only returned when
    candidate_requests.status == ACCEPTED for this (candidate, job, agency) triple.
    PENDING and DECLINED keep the profile locked (LIMITED).
    """
    agency_id = _resolve_agency_scope(db, user_id=_.get("id", ""), job_id=jobId)
    profile = get_candidate_profile(db=db, candidate_id=candidate_id, job_id=jobId, agency_id=agency_id)
    return success_response(profile)


@router.post("/candidates/{candidate_id}/respond")
def candidate_respond(
    candidate_id: str,
    action: str = Query(..., pattern="^(accept|decline)$"),
    request_id: str = Query(...),
    _: None = Depends(_verify_internal_key),
    db: Session = Depends(get_db),
):
    """Eve-facing endpoint: transition a PENDING request to ACCEPTED or DECLINED.

    Security:
        - Gated by X-Internal-Api-Key header — NOT accessible to Adam recruiters.
        - candidate_id in the path is validated against the stored request row.
        - agency_id and job_id are derived from the stored row, never from the caller.
        - Recruiters cannot self-accept: this endpoint rejects JWT-authenticated calls.

    State machine:
        PENDING  + accept  → ACCEPTED  (sets responded_at)
        PENDING  + decline → DECLINED  (sets responded_at)
        ACCEPTED + accept  → idempotent (returns current state)
        DECLINED + decline → idempotent (returns current state)
        ACCEPTED + decline → 409
        DECLINED + accept  → 409

    Eve integration:
        Eve will call this endpoint when a candidate taps Accept or Decline
        on their interest notification. Eve presents the INTERNAL_API_KEY
        in the X-Internal-Api-Key header.
    """
    result = respond_to_candidate_request(
        db=db,
        request_id=request_id,
        candidate_id=candidate_id,
        action=action,
    )
    return success_response(result)


@router.get("/candidates/{candidate_id}/pending-requests")
def candidate_pending_requests(
    candidate_id: str,
    _: None = Depends(_verify_internal_key),
    db: Session = Depends(get_db),
):
    """Eve-facing endpoint: return all PENDING requests for a candidate.

    Eve uses this to show the candidate their outstanding interest requests
    before they accept or decline.

    Gated by X-Internal-Api-Key — not accessible to Adam recruiters.
    """
    results = get_pending_requests_for_candidate(db=db, candidate_id=candidate_id)
    return success_response(results)
