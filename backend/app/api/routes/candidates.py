from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.candidate import CandidateExportRequest, CandidateSelectionRequest, SwipeFeedbackRequest
from app.services.ats_service import export_to_ats
from app.services.candidate_service import apply_feedback, fetch_ranked_candidates
from app.services.candidate_selection_service import (
    get_final_selection_results,
    get_first_selection_batch,
    get_next_selection_batch,
    submit_selection_choice,
)
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
    candidates = fetch_ranked_candidates(db=db, job_id=jobId, mode=mode, refresh=refresh, debug=debug)
    return success_response([candidate.model_dump(exclude_none=True) for candidate in candidates])


@router.get("/candidates/shortlisted")
def get_shortlisted_candidates(
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return only shortlisted candidates for a job — used by the outreach page."""
    from app.services.candidate_service import list_shortlisted_candidates
    candidates = list_shortlisted_candidates(db=db, job_id=jobId)
    return success_response([candidate.model_dump() for candidate in candidates])


@router.post("/candidates/swipe")
def swipe_candidate(payload: SwipeFeedbackRequest, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    result = apply_feedback(
        db=db,
        job_id=payload.jobId,
        candidate_id=payload.candidateId,
        action=payload.action,
    )
    return success_response(result)


@router.post("/candidates/export")
def export_candidates(payload: CandidateExportRequest, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
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
    result = get_first_selection_batch(db=db, job_id=jobId)
    return success_response(result)


@router.get("/candidates/selection/next")
def get_next_candidate_batch(
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = get_next_selection_batch(db=db, job_id=jobId)
    return success_response(result)


@router.post("/candidates/selection")
def select_candidate(
    payload: CandidateSelectionRequest,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = submit_selection_choice(db=db, job_id=payload.jobId, candidate_id=payload.candidateId)
    return success_response(result)


@router.get("/candidates/selection/final")
def get_final_candidate_selection(
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = get_final_selection_results(db=db, job_id=jobId)
    return success_response(result)
