from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.services.ownership import assert_job_ownership
from app.services.recruiter_interview_orchestrator import (
    advance_recruiter_interview_stage,
    build_recruiter_interview_response,
    start_recruiter_interview_session,
    update_recruiter_interview_session,
)
from app.services.recruiter_preference_round_service import bootstrap_preference_session, build_state_response, finalize_preference_session
from app.utils.exceptions import APIError
from app.utils.responses import success_response

router = APIRouter(prefix="/recruiters", tags=["recruiter-intelligence"])


class RecruiterIntelligenceUpdateRequest(BaseModel):
    jobId: str
    transcript: str = ""
    voiceSummary: str = ""
    entities: dict[str, Any] = Field(default_factory=dict)


@router.get("/{recruiter_id}/intelligence/jobs/{job_id}")
def get_recruiter_intelligence_job(
    recruiter_id: str,
    job_id: str,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if _.get("id", "") != recruiter_id:
        raise APIError("Forbidden", status_code=403)
    assert_job_ownership(db=db, job_id=job_id, user_id=recruiter_id)
    interview_state = start_recruiter_interview_session(db=db, recruiter_id=recruiter_id, job_id=job_id)
    selection_state = bootstrap_preference_session(
        db=db,
        recruiter_id=recruiter_id,
        job_id=job_id,
        voice_summary=interview_state.get("voice_summary", ""),
        gap_analysis=interview_state.get("gap_analysis") or {},
    )
    db.commit()
    return success_response(
        {
            "interview": build_recruiter_interview_response(state=interview_state),
            "selection": build_state_response(selection_state),
        }
    )


@router.post("/{recruiter_id}/intelligence/jobs/{job_id}")
def update_recruiter_intelligence_job(
    recruiter_id: str,
    job_id: str,
    payload: RecruiterIntelligenceUpdateRequest,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.state.user["id"] != recruiter_id:
        raise APIError("Forbidden", status_code=403)
    assert_job_ownership(db=db, job_id=job_id, user_id=recruiter_id)
    interview_state = update_recruiter_interview_session(
        db=db,
        recruiter_id=recruiter_id,
        job_id=job_id,
        transcript=payload.transcript or payload.voiceSummary,
        parsed_entities=payload.entities,
    )
    selection_state = bootstrap_preference_session(
        db=db,
        recruiter_id=recruiter_id,
        job_id=job_id,
        voice_summary=interview_state.get("voice_summary", ""),
        gap_analysis=interview_state.get("gap_analysis") or {},
    )
    db.commit()
    return success_response(
        {
            "interview": build_recruiter_interview_response(state=interview_state),
            "selection": build_state_response(selection_state),
        }
    )


@router.post("/{recruiter_id}/intelligence/jobs/{job_id}/advance")
def advance_recruiter_intelligence_job(
    recruiter_id: str,
    job_id: str,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.state.user["id"] != recruiter_id:
        raise APIError("Forbidden", status_code=403)
    assert_job_ownership(db=db, job_id=job_id, user_id=recruiter_id)
    interview_state = advance_recruiter_interview_stage(db=db, recruiter_id=recruiter_id, job_id=job_id)
    selection_state = bootstrap_preference_session(
        db=db,
        recruiter_id=recruiter_id,
        job_id=job_id,
        voice_summary=interview_state.get("voice_summary", ""),
        gap_analysis=interview_state.get("gap_analysis") or {},
    )
    db.commit()
    return success_response(
        {
            "interview": build_recruiter_interview_response(state=interview_state),
            "selection": build_state_response(selection_state),
        }
    )


@router.post("/{recruiter_id}/intelligence/jobs/{job_id}/finalize")
def finalize_recruiter_intelligence_job(
    recruiter_id: str,
    job_id: str,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.state.user["id"] != recruiter_id:
        raise APIError("Forbidden", status_code=403)
    assert_job_ownership(db=db, job_id=job_id, user_id=recruiter_id)
    interview_state = advance_recruiter_interview_stage(db=db, recruiter_id=recruiter_id, job_id=job_id)
    selection_state = finalize_preference_session(db=db, recruiter_id=recruiter_id, job_id=job_id)
    db.commit()
    return success_response(
        {
            "interview": build_recruiter_interview_response(state=interview_state),
            "selection": build_state_response(selection_state),
        }
    )
