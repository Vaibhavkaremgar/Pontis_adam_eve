from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi import Request
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.candidate import CandidateExportRequest, CandidateSelectionRequest, SwipeFeedbackRequest
from app.services.ats_service import export_to_ats
from app.services.candidate_service import apply_feedback, build_candidate_fetch_debug, fetch_ranked_candidates
from app.services.candidate_selection_service import (
    get_final_selection_results,
    get_first_selection_batch,
    get_next_selection_batch,
    submit_selection_choice,
)
from app.services.ownership import assert_job_ownership
from app.utils.responses import success_response

router = APIRouter(tags=["candidates"])


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
    candidates = fetch_ranked_candidates(db=db, job_id=jobId, mode=mode, refresh=refresh, debug=debug)
    payload = [candidate.model_dump(exclude_none=True) for candidate in candidates]
    debug_payload = build_candidate_fetch_debug(
        db=db,
        job_id=jobId,
        mode=mode,
        refresh=refresh,
        request_source="api",
        returned_count=len(payload),
    )
    if debug or not payload:
        return {"success": True, "data": payload, "error": None, "debug": debug_payload}
    return success_response(payload)


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


@router.get("/candidates/selection/final")
def get_final_candidate_selection(
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    result = get_final_selection_results(db=db, job_id=jobId)
    return success_response(result)
