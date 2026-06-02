from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi import Request
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.candidate import InterviewBookingData, InterviewBookingRequest, InterviewDecisionData, InterviewDecisionRequest, InterviewInsightsData, InterviewRescheduleData, InterviewRescheduleRequest, InterviewSessionData, InterviewSessionRequest
from app.services.audit_service import record_audit_event
from app.services.interview_stage_service import advance_interview_stage, get_interview_insights
from app.services.interview_service import list_interviews
from app.services.interview_evaluation_service import list_interview_evaluations, record_interview_evaluation
from app.services.interview_session_service import book_interview_session, create_interview_session, get_interview_session, mark_interview_no_show, reschedule_interview_session
from app.services.ownership import assert_job_ownership
from app.utils.responses import success_response

router = APIRouter(tags=["interviews"])


@router.get("/interviews")
def get_interviews(jobId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    rows = list_interviews(db=db, job_id=jobId)
    return success_response([row.model_dump() for row in rows])


@router.post("/interview/session")
def create_session(payload: InterviewSessionRequest, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=payload.jobId, user_id=request.state.user["id"])
    data = create_interview_session(
        db=db,
        job_id=payload.jobId,
        candidate_id=payload.candidateId,
        available_slots=list(payload.availableSlots or []),
        timezone_name=payload.timezone or "UTC",
    )
    record_audit_event(
        db=db,
        actor_id=request.state.user["id"],
        action="interview_session_created",
        entity_type="job",
        entity_id=payload.jobId,
        metadata={"candidate_id": payload.candidateId},
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    db.commit()
    return success_response(InterviewSessionData(**data).model_dump())


@router.get("/interview/session")
def get_session(token: str = Query(...), db: Session = Depends(get_db)):
    data = get_interview_session(db=db, token=token)
    return success_response(InterviewSessionData(**data).model_dump())


@router.post("/interview/book")
def book_session(payload: InterviewBookingRequest, db: Session = Depends(get_db)):
    data = book_interview_session(db=db, token=payload.token, scheduled_at=payload.scheduledAt)
    return success_response(InterviewBookingData(**data).model_dump())


@router.post("/interview/reschedule")
def reschedule_session(payload: InterviewRescheduleRequest, db: Session = Depends(get_db)):
    data = reschedule_interview_session(db=db, token=payload.token, scheduled_at=payload.scheduledAt, reason=payload.reason)
    return success_response(InterviewRescheduleData(**data).model_dump())


@router.post("/interview/no-show")
def interview_no_show(payload: dict, db: Session = Depends(get_db)):
    token = str(payload.get("token") or "").strip()
    if not token:
        return success_response({"created": False, "error": "token is required"})
    data = mark_interview_no_show(db=db, token=token, reason=str(payload.get("reason") or "no_show_detected").strip())
    return success_response(data)


@router.get("/interview/insights")
def interview_insights(jobId: str = Query(...), candidateId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    data = get_interview_insights(db=db, job_id=jobId, candidate_id=candidateId)
    return success_response(InterviewInsightsData(**data).model_dump())


@router.post("/interview/decision")
def interview_decision(payload: InterviewDecisionRequest, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=payload.jobId, user_id=request.state.user["id"])
    result = advance_interview_stage(
        db=db,
        job_id=payload.jobId,
        candidate_id=payload.candidateId,
        action=payload.action,
        target_stage=payload.targetStage or None,
        notes=payload.notes,
        recommendation=payload.recommendation,
        interviewer_id=payload.interviewerId or None,
        source_app=payload.sourceType or "adam",
    )
    record_audit_event(
        db=db,
        actor_id=request.state.user["id"],
        action="interview_decision",
        entity_type="job",
        entity_id=payload.jobId,
        metadata={
            "candidate_id": payload.candidateId,
            "action": payload.action,
            "target_stage": payload.targetStage,
            "recommendation": payload.recommendation,
        },
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return success_response(InterviewDecisionData(**result).model_dump())


@router.get("/interview/evaluations")
def get_evaluations(jobId: str = Query(...), candidateId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    return success_response(list_interview_evaluations(db=db, job_id=jobId, candidate_id=candidateId))


@router.post("/interview/evaluations")
def create_evaluation(payload: dict, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    job_id = str(payload.get("jobId") or "").strip()
    candidate_id = str(payload.get("candidateId") or "").strip()
    assert_job_ownership(db=db, job_id=job_id, user_id=request.state.user["id"])
    result = record_interview_evaluation(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        stage_name=str(payload.get("stageName") or "screen").strip(),
        interviewer_id=str(payload.get("interviewerId") or "").strip() or None,
        summary=str(payload.get("summary") or "").strip(),
        recommendation=str(payload.get("recommendation") or "").strip(),
        competency_scores=dict(payload.get("competencyScores") or {}),
        notes=str(payload.get("notes") or "").strip(),
        metadata=dict(payload.get("metadata") or {}),
    )
    record_audit_event(
        db=db,
        actor_id=request.state.user["id"],
        action="interview_evaluation_recorded",
        entity_type="job",
        entity_id=job_id,
        metadata={"candidate_id": candidate_id, "stage_name": payload.get("stageName"), "recommendation": payload.get("recommendation")},
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return success_response(result)
