from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi import Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import INTERNAL_API_KEY
from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.candidate import InterviewBookingData, InterviewBookingRequest, InterviewDecisionData, InterviewDecisionRequest, InterviewInsightsData, InterviewRescheduleData, InterviewRescheduleRequest, InterviewSessionData, InterviewSessionRequest
from app.db.repositories import InterviewRepository, JobRepository, NotificationWorkflowTokenRepository
from app.services.audit_service import record_audit_event
from app.services.interview_stage_service import advance_interview_stage, get_interview_insights
from app.services.interview_service import list_interviews
from app.services.interview_evaluation_service import list_interview_evaluations, record_interview_evaluation
from app.services.interview_session_service import book_interview_session, create_interview_session, get_interview_session, mark_interview_no_show, reschedule_interview_session
from app.utils.exceptions import APIError
from app.services.ownership import assert_job_company_ownership, resolve_company_id_for_user
from app.utils.responses import success_response

router = APIRouter(tags=["interviews"])


class InterviewResultsCallbackRequest(BaseModel):
    workflow_token: str
    transcript: str = ""
    interview_score: float = 0.0
    technical_score: float = 0.0
    communication_score: float = 0.0
    culture_fit_score: float = 0.0
    ai_summary: str = ""
    feedback: str = ""
    interviewer_notes: str = ""
    video_url: str = ""
    completed_at: datetime | None = None


@router.get("/interviews")
def get_interviews(jobId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    user_id = _.get("id", "")
    company_id = resolve_company_id_for_user(db=db, user_id=user_id)
    assert_job_company_ownership(db=db, job_id=jobId, user_id=user_id)
    rows = list_interviews(db=db, job_id=jobId, company_id=company_id)
    return success_response([row.model_dump() for row in rows])


@router.post("/interviews/results")
def interview_results_callback(payload: InterviewResultsCallbackRequest, request: Request, db: Session = Depends(get_db)):
    provided_key = str(request.headers.get("X-Internal-API-Key", "") or "").strip()
    if not INTERNAL_API_KEY or not provided_key or not secrets.compare_digest(provided_key, INTERNAL_API_KEY):
        raise APIError("Unauthorized", status_code=401)

    token_row = NotificationWorkflowTokenRepository(db).get_by_token(payload.workflow_token, source_app="ui")
    if not token_row:
        raise APIError("Interview workflow token not found", status_code=404)

    token_payload = dict(token_row.payload or {}) if isinstance(token_row.payload, dict) else {}
    job_id = str(token_payload.get("job_id") or token_payload.get("jobId") or token_row.job_id or "").strip()
    candidate_id = str(token_payload.get("candidate_id") or token_payload.get("candidateId") or token_row.candidate_id or "").strip()
    if not job_id or not candidate_id:
        raise APIError("Interview context not found", status_code=404)

    completed_at = payload.completed_at or datetime.now(timezone.utc)
    InterviewRepository(db).upsert_interview_results(
        job_id=job_id,
        candidate_id=candidate_id,
        result_data={
            "interview_score": payload.interview_score,
            "technical_score": payload.technical_score,
            "communication_score": payload.communication_score,
            "culture_fit_score": payload.culture_fit_score,
            "transcript": payload.transcript,
            "ai_summary": payload.ai_summary,
            "feedback": payload.feedback,
            "interviewer_notes": payload.interviewer_notes,
            "video_url": payload.video_url,
            "completed_at": completed_at,
        },
    )
    db.commit()
    return {"success": True}


@router.post("/interview/session")
def create_session(payload: InterviewSessionRequest, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    user_id = request.state.user["id"]
    company_id = resolve_company_id_for_user(db=db, user_id=user_id)
    assert_job_company_ownership(db=db, job_id=payload.jobId, user_id=user_id)
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
        metadata={"candidate_id": payload.candidateId, "company_id": company_id},
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
    user_id = _.get("id", "")
    resolve_company_id_for_user(db=db, user_id=user_id)
    assert_job_company_ownership(db=db, job_id=jobId, user_id=user_id)
    data = get_interview_insights(db=db, job_id=jobId, candidate_id=candidateId)
    return success_response(InterviewInsightsData(**data).model_dump())


@router.post("/interview/decision")
def interview_decision(payload: InterviewDecisionRequest, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    user_id = request.state.user["id"]
    resolve_company_id_for_user(db=db, user_id=user_id)
    assert_job_company_ownership(db=db, job_id=payload.jobId, user_id=user_id)
    job = JobRepository(db).get(payload.jobId)
    result = advance_interview_stage(
        db=db,
        job_id=payload.jobId,
        candidate_id=payload.candidateId,
        action=payload.action,
        target_stage=payload.targetStage or None,
        notes=payload.notes,
        recommendation=payload.recommendation,
        interviewer_id=payload.interviewerId or None,
        source_app=payload.sourceType or (job.source_app if job else "ui"),
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
    user_id = _.get("id", "")
    resolve_company_id_for_user(db=db, user_id=user_id)
    assert_job_company_ownership(db=db, job_id=jobId, user_id=user_id)
    return success_response(list_interview_evaluations(db=db, job_id=jobId, candidate_id=candidateId))


@router.post("/interview/evaluations")
def create_evaluation(payload: dict, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    job_id = str(payload.get("jobId") or "").strip()
    candidate_id = str(payload.get("candidateId") or "").strip()
    user_id = request.state.user["id"]
    resolve_company_id_for_user(db=db, user_id=user_id)
    assert_job_company_ownership(db=db, job_id=job_id, user_id=user_id)
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
