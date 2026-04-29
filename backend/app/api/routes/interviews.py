from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.candidate import InterviewBookingData, InterviewBookingRequest, InterviewSessionData, InterviewSessionRequest
from app.services.interview_service import list_interviews
from app.services.interview_session_service import book_interview_session, create_interview_session, get_interview_session
from app.utils.responses import success_response

router = APIRouter(tags=["interviews"])


@router.get("/interviews")
def get_interviews(jobId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = list_interviews(db=db, job_id=jobId)
    return success_response([row.model_dump() for row in rows])


@router.post("/interview/session")
def create_session(payload: InterviewSessionRequest, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    data = create_interview_session(db=db, job_id=payload.jobId, candidate_id=payload.candidateId)
    return success_response(InterviewSessionData(**data).model_dump())


@router.get("/interview/session")
def get_session(token: str = Query(...), db: Session = Depends(get_db)):
    data = get_interview_session(db=db, token=token)
    return success_response(InterviewSessionData(**data).model_dump())


@router.post("/interview/book")
def book_session(payload: InterviewBookingRequest, db: Session = Depends(get_db)):
    data = book_interview_session(db=db, token=payload.token, scheduled_at=payload.scheduledAt)
    return success_response(InterviewBookingData(**data).model_dump())
